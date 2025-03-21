import argparse

import matplotlib.pyplot as plt

from robust_llm.metrics.asr_per_iteration import (
    ASRMetricResults,
    compute_asr_per_iteration_from_wandb,
)


def plot_interpolated_asr_from_asrs(asrs: ASRMetricResults) -> None:
    """Computes the interpolated ASR from the ASRs for each iteration of the attack."""
    plt.plot(asrs.asr_per_iteration, label="raw", linewidth=4)
    # plt.xscale("log")
    # plt.yscale("log")
    deciles = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    interpolated_iterations = [
        asrs.interpolated_iteration_for_asr(decile) for decile in deciles
    ]
    # filter out Nones
    it_decile_pairs = list(
        filter(lambda x: x[1] is not None, zip(interpolated_iterations, deciles))
    )
    filtered_its, filtered_deciles = zip(*it_decile_pairs)
    print(filtered_its, filtered_deciles)
    plt.plot(
        filtered_its, filtered_deciles, marker="o", color="red", label="interpolated"
    )
    plt.legend()
    plt.show()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Plot interpolated ASR from ASRs.")
    parser.add_argument(
        "--group_name", type=str, help="The group name for the experiment."
    )
    parser.add_argument(
        "--run_index", type=str, help="The run index for the experiment."
    )

    args = parser.parse_args()

    asrs = compute_asr_per_iteration_from_wandb(args.group_name, args.run_index)
    plot_interpolated_asr_from_asrs(asrs)
