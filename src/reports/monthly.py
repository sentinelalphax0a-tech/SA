"""
Monthly Report Generator.

Runs on the 1st of each month at 8:00 AM UTC via GitHub Actions.
Aggregates the past month's performance and publishes summary.
"""

import logging
from datetime import date, timedelta

from src.database.supabase_client import SupabaseClient
from src.publishing.chart_generator import ChartGenerator
from src.publishing.twitter_bot import TwitterBot
from src.publishing.telegram_bot import TelegramBot

logger = logging.getLogger(__name__)


def generate_monthly_report() -> None:
    """Generate and publish the monthly performance report."""
    db = SupabaseClient()
    charts = ChartGenerator()
    twitter = TwitterBot()
    telegram = TelegramBot()

    today = date.today()
    month_end = today.replace(day=1) - timedelta(days=1)
    month_start = month_end.replace(day=1)

    logger.info(f"Generating monthly report: {month_start} to {month_end}")

    # TODO: Query alerts for the month, calculate stats, generate charts
    raise NotImplementedError


if __name__ == "__main__":
    generate_monthly_report()
