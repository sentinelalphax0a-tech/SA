"""
Chart Generator — Creates visual charts for reports.

Uses matplotlib to generate:
  - Alert distribution by star level
  - Accuracy rate over time
  - Weekly/monthly summary charts
"""

import logging
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # Non-interactive backend for CI
import matplotlib.pyplot as plt

logger = logging.getLogger(__name__)

CHART_OUTPUT_DIR = Path("charts")


class ChartGenerator:
    """Generates PNG charts for reports."""

    def __init__(self) -> None:
        CHART_OUTPUT_DIR.mkdir(exist_ok=True)

    def alerts_by_stars(
        self, data: dict[int, int], title: str = "Alerts by Star Level"
    ) -> str:
        """
        Bar chart of alert counts by star level.

        Returns the path to the saved PNG file.
        """
        fig, ax = plt.subplots(figsize=(8, 5))
        stars = sorted(data.keys())
        counts = [data[s] for s in stars]
        labels = [f"{'⭐' * s}" if s > 0 else "0★" for s in stars]

        ax.bar(labels, counts, color="#4F46E5")
        ax.set_title(title)
        ax.set_xlabel("Star Level")
        ax.set_ylabel("Count")

        path = str(CHART_OUTPUT_DIR / "alerts_by_stars.png")
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        logger.info(f"Chart saved: {path}")
        return path

    def accuracy_by_stars(
        self, data: dict[int, float], title: str = "Accuracy by Star Level"
    ) -> str:
        """Bar chart of accuracy rate by star level."""
        fig, ax = plt.subplots(figsize=(8, 5))
        stars = sorted(data.keys())
        rates = [data[s] * 100 for s in stars]
        labels = [f"{'⭐' * s}" if s > 0 else "0★" for s in stars]

        ax.bar(labels, rates, color="#10B981")
        ax.set_title(title)
        ax.set_xlabel("Star Level")
        ax.set_ylabel("Accuracy (%)")
        ax.set_ylim(0, 100)

        path = str(CHART_OUTPUT_DIR / "accuracy_by_stars.png")
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        logger.info(f"Chart saved: {path}")
        return path
