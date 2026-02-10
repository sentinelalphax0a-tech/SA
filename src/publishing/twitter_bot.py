"""
Twitter/X Bot — Publishes alerts to @SentinelAlpha.

Handles API auth, rate limiting (max 10 tweets/day), and formatting.
Only publishes alerts with star_level >= 3 (score >= 70).

If the Twitter API is not approved or credentials are missing, all methods
degrade gracefully: log a warning and return safe defaults without crashing.
"""

import logging
from datetime import datetime, timezone

from src import config
from src.database.models import Alert
from src.publishing.formatter import AlertFormatter

logger = logging.getLogger(__name__)


class TwitterBot:
    """Publishes alerts to X/Twitter."""

    def __init__(self) -> None:
        self.client = None
        self.formatter = AlertFormatter()
        self._tweets_today: list[datetime] = []
        self._today_date: str = ""
        self._authenticate()

    # ── Authentication ───────────────────────────────────────

    def _authenticate(self) -> None:
        """Set up tweepy client with OAuth 1.0a credentials.

        If credentials are missing or tweepy is unavailable, the bot
        stays disabled and all publish calls become no-ops.
        """
        creds = [
            config.TWITTER_API_KEY,
            config.TWITTER_API_SECRET,
            config.TWITTER_ACCESS_TOKEN,
            config.TWITTER_ACCESS_SECRET,
        ]

        if not all(creds):
            logger.warning("Twitter API not available, skipping")
            return

        try:
            import tweepy

            self.client = tweepy.Client(
                consumer_key=config.TWITTER_API_KEY,
                consumer_secret=config.TWITTER_API_SECRET,
                access_token=config.TWITTER_ACCESS_TOKEN,
                access_token_secret=config.TWITTER_ACCESS_SECRET,
            )
            logger.info("Twitter bot authenticated")
        except ImportError:
            logger.warning("Twitter API not available, skipping — tweepy not installed")
        except Exception as e:
            logger.warning("Twitter API not available, skipping — %s", e)

    # ── Public API ───────────────────────────────────────────

    def publish_alert(self, alert: Alert) -> str | None:
        """Publish a smart money alert to X.

        Only publishes if:
          - API is available
          - Daily limit not reached
          - star_level >= 3

        Returns:
            tweet_id on success, None otherwise.
        """
        if not self.can_publish():
            return None

        star = alert.star_level or 0
        if star < 3:
            logger.debug("Skipping X publish: star_level %d < 3", star)
            return None

        text = self.formatter.format_x_alert(alert)
        return self._post_tweet(text)

    def publish_resolution(self, alert: Alert) -> str | None:
        """Publish a resolution follow-up to X.

        Only publishes if the API is available and daily limit allows.

        Returns:
            tweet_id on success, None otherwise.
        """
        if not self.can_publish():
            return None

        text = self.formatter.format_x_resolution(alert)
        return self._post_tweet(text)

    def get_tweets_today(self) -> int:
        """Return the number of tweets posted today (local tracking).

        Resets the counter at midnight UTC automatically.
        """
        self._reset_if_new_day()
        return len(self._tweets_today)

    def can_publish(self) -> bool:
        """Check whether the bot can publish right now.

        Returns False if:
          - API client is not authenticated
          - Daily tweet limit has been reached
        """
        if self.client is None:
            logger.debug("Twitter API not available, skipping")
            return False

        if self.get_tweets_today() >= config.MAX_TWEETS_PER_DAY:
            logger.warning(
                "Daily tweet limit reached (%d/%d)",
                len(self._tweets_today),
                config.MAX_TWEETS_PER_DAY,
            )
            return False

        return True

    # ── Internal ─────────────────────────────────────────────

    def _post_tweet(self, text: str) -> str | None:
        """Post a tweet and track it in the daily counter.

        Returns:
            tweet_id string on success, None on failure.
        """
        if self.client is None:
            logger.warning("Twitter API not available, skipping")
            return None

        try:
            response = self.client.create_tweet(text=text)
            tweet_id = str(response.data["id"])
            self._record_tweet()
            logger.info("Published tweet %s (%d/%d today)",
                        tweet_id, self.get_tweets_today(),
                        config.MAX_TWEETS_PER_DAY)
            return tweet_id
        except Exception as e:
            logger.error("Failed to publish tweet: %s", e)
            return None

    def _record_tweet(self) -> None:
        """Add current timestamp to the daily tweet log."""
        self._reset_if_new_day()
        self._tweets_today.append(datetime.now(timezone.utc))

    def _reset_if_new_day(self) -> None:
        """Reset the tweet counter if the UTC date has changed."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._today_date:
            self._tweets_today = []
            self._today_date = today
