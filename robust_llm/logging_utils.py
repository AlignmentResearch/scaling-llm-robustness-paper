from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import wandb
import yaml
from accelerate import Accelerator
from datasets import Dataset
from datasets.utils.logging import disable_progress_bar
from omegaconf import OmegaConf

from robust_llm import logger
from robust_llm.dist_utils import is_main_process

if TYPE_CHECKING:
    from robust_llm.config.configs import ExperimentConfig

LOGGING_LEVELS = {
    logging.DEBUG,
    logging.INFO,
    logging.WARNING,
    logging.ERROR,
    logging.CRITICAL,
}

# How often to log training metrics to INFO
TRAIN_LOG_INTERVAL_SECS = 30


@dataclass
class LoggingCounter:
    """Class for logging precise step and datapoint counts.

    Used to keep track of the number of batches seen during training,
    as well as the precise number of datapoints that corresponds to.
    Individual models can each have their own logging counter, and
    all point to the same global counter, which is used to keep track
    of the total number of batches and datapoints seen across all models
    during the current experiment.
    """

    _name: str
    _step_count: int = 0
    _datapoint_count: int = 0
    _is_global: bool = False

    def __post_init__(self) -> None:
        self._parent = None
        if not self._is_global:
            self._parent = GLOBAL_LOGGING_COUNTER

    def increment(
        self, step_count_to_add: int, datapoint_count_to_add: int, commit: bool = False
    ) -> None:
        # We never commit the global counter, since we only ever increment
        # it while incrementing a non-global counter. This non-global counter
        # will manage whether or not to commit.
        if self._parent is not None:
            self._parent.increment(
                step_count_to_add, datapoint_count_to_add, commit=False
            )

        self._step_count += step_count_to_add
        self._datapoint_count += datapoint_count_to_add

        self._log_to_wandb(commit=commit)

    @property
    def step_count(self) -> int:
        return self._step_count

    @property
    def datapoint_count(self) -> int:
        return self._datapoint_count

    def _log_to_wandb(self, commit: bool = False) -> None:
        wandb_log(
            {
                f"{self._name}_step_count": self._step_count,
                f"{self._name}_datapoint_count": self._datapoint_count,
            },
            commit=commit,
        )

    @property
    def root(self) -> LoggingCounter:
        return self._parent.root if self._parent is not None else self


GLOBAL_LOGGING_COUNTER = LoggingCounter(_name="global", _is_global=True)


def setup_wandb_metrics() -> None:
    # Set the default horizontal axis on wandb
    # NOTE: Older versions of wandb don't have "define_metric",
    # so try updating wandb if you get an error here.
    assert wandb.run is not None
    assert hasattr(wandb, "define_metric")
    wandb.define_metric("global_datapoint_count")
    wandb.define_metric("victim_training_datapoint_count")
    wandb.define_metric("training_attack_datapoint_count")
    wandb.define_metric("validation_attack_datapoint_count")
    wandb.define_metric(
        "*",
        step_metric="global_datapoint_count",
    )
    wandb.define_metric(
        "train/*",
        step_metric="victim_training_datapoint_count",
    )
    wandb.define_metric(
        "eval/*",
        step_metric="victim_training_datapoint_count",
    )
    wandb.define_metric(
        "training_attack/*",
        step_metric="training_attack_datapoint_count",
    )
    wandb.define_metric(
        "validation_attack/*",
        step_metric="validation_attack_datapoint_count",
    )

    # Make sure step metrics for global counter are logged at least once.
    GLOBAL_LOGGING_COUNTER._log_to_wandb()


def wandb_set_really_finished() -> None:
    # Sometimes wandb runs are marked as finished even though in fact they are not.
    # In order to know for sure whether a run finished properly, we manually
    # call this function at the end of the run. This way, we have the guarantee
    # that the run finished properly iff we see `really_finished=1` on wandb.
    assert wandb.run is not None
    wandb.run.log({"really_finished": 1}, commit=True)


def log_dataset_to_wandb(
    dataset: Dataset, dataset_name: str, max_n_examples: Optional[int] = None
) -> None:
    if max_n_examples is not None:
        dataset = dataset.select(range(min(len(dataset), max_n_examples)))

    dataset_table = wandb.Table(columns=["text", "label"])

    if "label" in dataset.column_names and "clf_label" in dataset.column_names:
        raise ValueError("Dataset has both 'label' and 'clf_label' columns.")

    if "label" in dataset.column_names:
        label_column = "label"
    elif "clf_label" in dataset.column_names:
        label_column = "clf_label"
    else:
        raise ValueError("Dataset has neither 'label' nor 'clf_label' columns.")

    for text, label in zip(
        dataset["text"],
        dataset[label_column],
    ):
        dataset_table.add_data(text, label)

    wandb_log({dataset_name: dataset_table}, commit=False)


def log_config_to_wandb(config: ExperimentConfig) -> None:
    """Logs the job config to wandb."""
    if not wandb.run:
        raise ValueError("wandb should have been initialized by now, exiting...")
    config_yaml = yaml.load(OmegaConf.to_yaml(config), Loader=yaml.FullLoader)
    wandb.run.summary["experiment_yaml"] = config_yaml  # type: ignore[has-type]


class LoggingContext:
    """
    Logging context manager for setting up and cleaning up logging.

    Singleton class to set up and clean up experiment logging to console, file,
    and wandb. Ensures only one logging context exists throughout the
    application.

    Args:
        is_main_process: whether this process is the main process
        args: config of the experiment
        set_up_step_metrics:
            whether to set up wandb step metrics which are used to
            define default x-axes for logged values
        model_family: The name of the model's family
        model_size:
            number of parameters in the model
            TODO: #348 - recording of `model_size` would ideally be done elsewhere
    """

    _instance = None
    _initialized = False

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        args: ExperimentConfig,
        set_up_step_metrics: bool = False,
        model_family: Optional[str] = None,
        model_size: Optional[int] = None,
    ) -> None:
        # Only initialize once
        if not LoggingContext._initialized:
            self.logger = logger
            self.args = args
            self.set_up_step_metrics = set_up_step_metrics
            self.model_family = model_family
            self.model_size = model_size
            self.local_files_path: Path | None = None
            disable_progress_bar()
            LoggingContext._initialized = True

    def save_logs(self) -> None:
        for handler in self.logger.handlers:
            if isinstance(handler, logging.FileHandler):
                handler.flush()

    def setup_logging(self) -> None:
        logging_level = self.args.environment.logging_level
        # Create logger and formatter
        self.logger.propagate = False
        self.logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter(
            "%(process)d - %(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )

        # Create console handler which logs at the configured level
        console_handler = logging.StreamHandler()
        assert (
            logging_level in LOGGING_LEVELS
        ), f"Invalid logging level: {logging_level}"
        console_handler.setLevel(logging_level)
        console_handler.setFormatter(formatter)

        # Add handler to logger
        self.logger.addHandler(console_handler)

    def wandb_initialize(self, run_id: str | None = None) -> str:
        """Initializes wandb run and does appropriate setup.

        In the training pipeline, unlike in the evaluation pipeline,
        we don't set up our wandb step metrics here
        (which logged values can be used as x-axes, and what the default x-axes
        are set to be) because HuggingFace sets up its own metrics when we initialize
        the Trainer, and we wait until that is done to overwrite them with our own.
        We do this in the `CustomLoggingWandbCallback`'s `setup` method.
        """
        config = self.args
        run = wandb.init(
            project="robust-llm",
            group=config.experiment_name,
            job_type=config.job_type,
            name=config.run_name,
            # default if not in test_mode
            mode="disabled" if config.environment.test_mode else None,
            resume="allow",
            id=run_id,
        )
        assert run is not None
        if self.set_up_step_metrics:
            setup_wandb_metrics()
        log_config_to_wandb(config)
        self.local_files_path = (
            Path(config.environment.save_root)
            / "local-files"
            / config.experiment_name
            / config.run_name
            / run.id
        )
        assert isinstance(self.local_files_path, Path)
        self.local_files_path.mkdir(exist_ok=True, parents=True)
        wandb_log({"local_files_path": str(self.local_files_path)}, commit=False)

        if config.environment.wandb_info_filename is not None:
            with open(config.environment.wandb_info_filename, "w") as f:
                f.write(
                    json.dumps({"wandb_run_name": run.name, "wandb_run_id": run.id})
                )
        return run.id

    def maybe_log_model_info(self, model_family: str, model_size: int) -> None:
        """Logs model info to wandb for use in plots.

        We use `commit=False` to avoid incrementing the step counter.
        """
        model_info_dict = {"model_family": model_family, "model_size": model_size}
        wandb_log(model_info_dict, commit=False)

    @staticmethod
    def wandb_cleanup() -> None:
        """Does necessary cleanup for wandb before the experiment ends."""
        wandb_set_really_finished()
        wandb.summary["finish_reason"] = "completed"
        wandb.finish()

    def cleanup(self) -> None:
        self.save_logs()
        if is_main_process():
            self.wandb_cleanup()

    @classmethod
    def get_instance(cls, *args, **kwargs):
        """
        Get the singleton instance of LoggingContext.

        Creates a new instance if one doesn't exist.
        """
        if cls._instance is None:
            cls._instance = LoggingContext(*args, **kwargs)
        return cls._instance

    @classmethod
    def reset(cls):
        """
        Reset the singleton instance.

        Useful for testing purposes.
        """
        cls._instance = None
        cls._initialized = False


class WandbTable:
    """
    Wrapper around wandb.Table to make it easier to log dicts as table rows.

    Args:
        name: Name of the table.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._table: wandb.Table | None = None
        self.logged = False

    @property
    def table(self) -> wandb.Table:
        assert isinstance(self._table, wandb.Table)
        return self._table

    def add_data(self, data: dict[str, Any]) -> None:
        if not should_log():
            return
        data = {
            k: v
            for k, v in data.items()
            if isinstance(v, (int, float, str)) or v is None
        }
        if self._table is None:
            self._table = wandb.Table(columns=list(data.keys()))
        assert self.table.columns == list(data.keys())
        self.table.add_data(*data.values())

    def save(self, commit: bool = False) -> None:
        if not should_log():
            return
        assert not self.logged, (
            "Table has already been logged. "
            "Wandb cannot handle multiple logs of the same table in one run."
        )
        wandb_log({self.name: self.table}, commit=commit)
        self.logged = True


def should_log():
    """Returns whether WandB logging should be done from this process.

    Logging should be done if either:
    - We are doing multiprocessing and this is the main process.
    - We are not doing multiprocessing.
    """
    if not wandb.run:
        return False
    return is_main_process()


def wandb_log(d: dict, commit: bool) -> None:
    if should_log():
        wandb.log(d, commit=commit)


def log(
    message: str,
    main_process_only: bool = True,
    level: str = "info",
    colors: bool = True,
    accelerator: Accelerator | None = None,
):
    """Log a message.

    Args:
        message:
            The message to log.
        main_process_only:
            Whether to only log the message on the main process.
        level:
            The logging level to use.
            Choose "print" to print, or the normal logging levels "info",
            "warning", "error", "critical".
        colors:
            Whether to color output by process index
        accelerator:
            The accelerator object.
    """
    if accelerator is None:
        accelerator = Accelerator()
    if main_process_only and not accelerator.is_main_process:
        return

    # Add process to the message
    message = f"Proc {accelerator.process_index} | {message}"
    if colors:
        color = COLOR_MAP[accelerator.process_index % len(COLOR_MAP)]
        message = f"{color}{message}{COLOR_END}"

    if level == "print":
        # Add the newline manually to avoid issues in multi-process logging.
        message = f"{message}\n"
        print(message, end="")

    elif level == "debug":
        logger.debug(message)
    elif level == "info":
        logger.info(message)
    elif level == "warning":
        logger.warning(message)
    elif level == "error":
        logger.error(message)
    else:
        raise ValueError(f"Invalid logging level: {level}")


# Blue for process 0, yellow for process 1, etc
COLOR_MAP = ["\033[94m", "\033[93m", "\033[92m", "\033[91m", "\033[95m"]
COLOR_END = "\033[0m"


def format_time(seconds: float) -> str:
    """Format seconds into human readable string like '1h 23m' or '5m 27s'"""
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = seconds / 60
    if minutes < 60:
        return f"{int(minutes)}m {int(seconds % 60)}s"
    return f"{int(minutes/60)}h {int(minutes % 60)}m"


def format_training_status(
    epoch: int, batch: int, total_batches: int, loss: float, elapsed_secs: float
) -> str:
    """Progress bar adapted from Claude."""
    progress = batch / total_batches
    filled = int(20 * progress)
    bar = "█" * filled + "░" * (20 - filled)

    # Linear estimate of time remaining
    if progress > 0:
        secs_per_batch = elapsed_secs / batch
        remaining_secs = secs_per_batch * (total_batches - batch)
        time_info = (
            f"| {format_time(elapsed_secs)} elapsed | "
            f"ETA: {format_time(remaining_secs)}"
        )
    else:
        time_info = f"| {format_time(elapsed_secs)} elapsed"

    return (
        f"Epoch {epoch:3d} | {bar} | "
        f"{progress:>3.0%} | "
        f"Loss: {loss:.4e} "
        f"{time_info}"
    )


def format_attack_status(
    total_examples: int,
    current_index: int,
    start_index: int,
    current_time: float,
    start_time: float,
) -> str:
    """Progress bar adapted from Claude."""
    progress = current_index / total_examples
    filled = int(20 * progress)
    bar = "█" * filled + "░" * (20 - filled)

    # Linear estimate of time remaining
    if progress > 0:
        secs_per_example = (current_time - start_time) / (current_index - start_index)
        remaining_secs = secs_per_example * (total_examples - current_index)
        time_info = (
            f" {format_time(current_time - start_time)} elapsed | "
            f" {format_time(secs_per_example)} per example | "
            f"ETA: {format_time(remaining_secs)}"
        )
    else:
        time_info = f"| {format_time(current_time - start_time)} elapsed"

    return (
        f"Example {current_index:3d} of {total_examples:3d} | {bar} | "
        f"{progress:>3.0%} | "
        f"{time_info}"
    )
