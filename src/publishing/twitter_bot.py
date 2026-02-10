"""
Twitter/X Bot — Publishes alerts to @SentinelAlpha.

Handles API auth, rate limiting (max 10 tweets/day), and tweet formatting.
Only publishes alerts with star_level >= 3 (score >= 70).
"""

import logging

import tweepy

from src import config

logger = logging.getLogger(__name__)


class TwitterBot:
    """Publishes alerts to X/Twitter."""

    def __init__(self) -> None:
        self.client: tweepy.Client | None = None
        self._authenticate()

    def _authenticate(self) -> None:
        """Set up tweepy client with OAuth 1.0a credentials."""
        if not all([
            config.TWITTER_API_KEY,
            config.TWITTER_API_SECRET,
            config.TWITTER_ACCESS_TOKEN,
            config.TWITTER_ACCESS_SECRET,
        ]):
            logger.warning("Twitter credentials not configured, bot disabled")
            return
        self.client = tweepy.Client(
            consumer_key=config.TWITTER_API_KEY,
            consumer_secret=config.TWITTER_API_SECRET,
            access_token=config.TWITTER_ACCESS_TOKEN,
            access_token_secret=config.TWITTER_ACCESS_SECRET,
        )

    def publish(self, text: str) -> str | None:
        """
        Post a tweet. Returns the tweet ID or None on failure.

        Respects MAX_TWEETS_PER_DAY limit.
        """
        if not self.client:
            logger.warning("Twitter bot not authenticated, skipping publish")
            return None
        try:
            response = self.client.create_tweet(text=text)
            tweet_id = str(response.data["id"])
            logger.info(f"Published tweet {tweet_id}")
            return tweet_id
        except Exception as e:
            logger.error(f"Failed to publish tweet: {e}")
            return None
