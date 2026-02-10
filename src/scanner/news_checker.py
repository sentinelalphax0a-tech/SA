"""
News checker via Google News RSS.

Checks whether a market topic has recent public news coverage (N02 filter).
"""

import logging
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import feedparser

from src import config

logger = logging.getLogger(__name__)

GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search"


class NewsChecker:
    """Checks Google News RSS for recent coverage of a market topic."""

    def check_news(
        self, keywords: str, hours: int = config.NEWS_LOOKBACK_HOURS
    ) -> tuple[bool, str | None]:
        """Search Google News RSS for recent articles matching keywords.

        Args:
            keywords: Search query string.
            hours: Look-back window in hours (default 24).

        Returns:
            (has_news, summary): True + headline if found, False + None otherwise.
        """
        url = self._build_rss_url(keywords)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

        try:
            feed = feedparser.parse(url)
        except Exception as e:
            logger.error("Failed to fetch Google News RSS: %s", e)
            return False, None

        if feed.bozo and not feed.entries:
            logger.warning("RSS parse error for '%s': %s", keywords, feed.bozo_exception)
            return False, None

        for entry in feed.entries:
            pub_date = self._parse_date(entry)
            if pub_date is None:
                continue
            if pub_date >= cutoff:
                title = entry.get("title", "").strip()
                # Strip source suffix like " - CNN"
                summary = title.rsplit(" - ", 1)[0].strip() if " - " in title else title
                return True, summary

        return False, None

    # Keep the old name as alias so existing code doesn't break
    has_recent_news = check_news

    def _build_rss_url(self, query: str) -> str:
        """Build Google News RSS search URL."""
        encoded = query.replace(" ", "+")
        return f"{GOOGLE_NEWS_RSS_URL}?q={encoded}&hl=en-US&gl=US&ceid=US:en"

    @staticmethod
    def _parse_date(entry: dict) -> datetime | None:
        """Parse the published date from an RSS entry."""
        raw = entry.get("published")
        if not raw:
            return None
        try:
            return parsedate_to_datetime(raw)
        except Exception:
            return None
