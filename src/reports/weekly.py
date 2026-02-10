"""
Weekly Report Generator.

Runs every Monday at 8:00 AM UTC via GitHub Actions.
Aggregates the past week's alerts, calculates accuracy by star level,
generates charts, and publishes summary to X + Telegram.
"""

import logging
from datetime import date, timedelta

from src.database.supabase_client import SupabaseClient
from src.database.models import WeeklyReport
from src.publishing.chart_generator import ChartGenerator
from src.publishing.twitter_bot import TwitterBot
from src.publishing.telegram_bot import TelegramBot

logger = logging.getLogger(__name__)


def generate_weekly_report() -> None:
    """Generate and publish the weekly performance report."""
    db = SupabaseClient()
    charts = ChartGenerator()
    twitter = TwitterBot()
    telegram = TelegramBot()

    today = date.today()
    week_end = today - timedelta(days=today.weekday())  # last Monday
    week_start = week_end - timedelta(days=7)

    logger.info(f"Generating weekly report: {week_start} to {week_end}")

    # TODO: Query alerts for the week, calculate stats, generate charts
    raise NotImplementedError


if __name__ == "__main__":
    generate_weekly_report()
