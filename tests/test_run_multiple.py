from unittest.mock import patch

import pytest

from robust_llm.batch_job_utils import run_multiple


# We have to mock the wandb function because it's not available in the test environment.
@patch("robust_llm.batch_job_utils.get_wandb_running_finished_runs", return_value=[])
def test_clean_single_example(mock_get_wandb_running_finished_runs):
    EXPERIMENT_NAME = "typo_example"
    HYDRA_CONFIG = "Eval/pm_gcg"

    MODELS = [
        "AlignmentResearch/robust_llm_pythia-14m_clf_pm_v-ian-068_s-0",
    ]
    OVERRIDE_ARGS_LIST = [
        {
            "model.name_or_path": model,
        }
        for model in MODELS
    ]
    # check it runs without errors
    run_multiple(
        EXPERIMENT_NAME,
        HYDRA_CONFIG,
        OVERRIDE_ARGS_LIST,
        memory="50G",
        cluster="a6k",
        dry_run=True,
        skip_git_checks=True,
    )
    mock_get_wandb_running_finished_runs.assert_called_once_with("typo_example")


# We have to mock the wandb function because it's not available in the test environment.
@patch("robust_llm.batch_job_utils.get_wandb_running_finished_runs", return_value=[])
def test_clean_list_example(mock_get_wandb_running_finished_runs):
    EXPERIMENT_NAME = "typo_example"
    HYDRA_CONFIG = "Eval/pm_gcg"

    MODELS = [
        "AlignmentResearch/robust_llm_pythia-tt-14m-mz-v0",
        "AlignmentResearch/robust_llm_pythia-wl-14m-niki-ada-v4-s-2",
    ]
    OVERRIDE_ARGS_LIST = [
        {
            "model.name_or_path": model,
        }
        for model in MODELS
    ]
    # check it runs without errors
    run_multiple(
        EXPERIMENT_NAME,
        HYDRA_CONFIG,
        OVERRIDE_ARGS_LIST,
        memory="50G",
        cluster="h100",
        dry_run=True,
        skip_git_checks=True,
        gpu=[1, 2],
    )

    mock_get_wandb_running_finished_runs.assert_called_once_with("typo_example")


def test_wrong_gpu_list_length():
    EXPERIMENT_NAME = "typo_example"
    HYDRA_CONFIG = "Eval/pm_gcg"

    MODELS = [
        "AlignmentResearch/robust_llm_pythia-tt-14m-mz-v0",
    ]
    OVERRIDE_ARGS_LIST = [
        {
            "model.name_or_path": model,
        }
        for model in MODELS
    ]
    # gpu list length must match override_args_list length
    with pytest.raises(AssertionError):
        run_multiple(
            EXPERIMENT_NAME,
            HYDRA_CONFIG,
            OVERRIDE_ARGS_LIST,
            memory="50G",
            dry_run=True,
            skip_git_checks=True,
            gpu=[1, 2],
        )


def test_typo_example():
    EXPERIMENT_NAME = "typo_example"
    HYDRA_CONFIG = "Eval/pm_gcg"

    MODELS = [
        "AlignmentResearch/robust_llm_pythia-tt-14m-mz-v0",
    ]
    OVERRIDE_ARGS_LIST = [
        {
            # very obvious mistake in the overrides
            "model.name_or_path_OOPS_typo": model,
        }
        for model in MODELS
    ]
    with pytest.raises(ValueError) as e_info:
        run_multiple(
            EXPERIMENT_NAME,
            HYDRA_CONFIG,
            OVERRIDE_ARGS_LIST,
            memory="50G",
            cluster="a6k",
            dry_run=True,
            skip_git_checks=True,
        )
    assert str(e_info.value) == "override_args are invalid, aborting."
