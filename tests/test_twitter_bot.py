"""Tests for the Twitter/X bot."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from src.database.models import Alert
from src.publishing.twitter_bot import TwitterBot


def _alert(**overrides) -> Alert:
    defaults = dict(
        market_id="m1",
        alert_type="accumulation",
        score=75,
        market_question="Will X resign?",
        direction="YES",
        star_level=3,
        total_amount=47200.0,
        odds_at_alert=0.08,
        price_impact=0.06,
        confluence_count=3,
    )
    defaults.update(overrides)
    return Alert(**defaults)


def _make_bot(authenticated: bool = True) -> TwitterBot:
    """Create a TwitterBot with mocked authentication."""
    with patch.object(TwitterBot, "_authenticate"):
        bot = TwitterBot()
    if authenticated:
        bot.client = MagicMock()
        bot.client.create_tweet.return_value = MagicMock(
            data={"id": "1234567890"}
        )
    else:
        bot.client = None
    return bot


# ── __init__ / auth ──────────────────────────────────────────


class TestInit:
    def test_no_credentials_no_crash(self):
        """Missing credentials → bot disabled, no exception."""
        with patch("src.publishing.twitter_bot.config") as mock_cfg:
            mock_cfg.TWITTER_API_KEY = ""
            mock_cfg.TWITTER_API_SECRET = ""
            mock_cfg.TWITTER_ACCESS_TOKEN = ""
            mock_cfg.TWITTER_ACCESS_SECRET = ""
            bot = TwitterBot()
        assert bot.client is None

    def test_import_error_no_crash(self):
        """tweepy not installed → bot disabled, no exception."""
        with patch("src.publishing.twitter_bot.config") as mock_cfg:
            mock_cfg.TWITTER_API_KEY = "key"
            mock_cfg.TWITTER_API_SECRET = "secret"
            mock_cfg.TWITTER_ACCESS_TOKEN = "token"
            mock_cfg.TWITTER_ACCESS_SECRET = "secret"
            with patch.dict("sys.modules", {"tweepy": None}):
                bot = TwitterBot()
        assert bot.client is None


# ── can_publish ──────────────────────────────────────────────


class TestCanPublish:
    def test_true_when_authenticated_and_under_limit(self):
        bot = _make_bot(authenticated=True)
        assert bot.can_publish() is True

    def test_false_when_not_authenticated(self):
        bot = _make_bot(authenticated=False)
        assert bot.can_publish() is False

    def test_false_when_limit_reached(self):
        bot = _make_bot(authenticated=True)
        # Simulate 10 tweets posted today
        now = datetime.now(timezone.utc)
        bot._today_date = now.strftime("%Y-%m-%d")
        bot._tweets_today = [now] * 10
        assert bot.can_publish() is False

    def test_true_when_under_limit(self):
        bot = _make_bot(authenticated=True)
        now = datetime.now(timezone.utc)
        bot._today_date = now.strftime("%Y-%m-%d")
        bot._tweets_today = [now] * 9
        assert bot.can_publish() is True


# ── get_tweets_today ─────────────────────────────────────────


class TestGetTweetsToday:
    def test_starts_at_zero(self):
        bot = _make_bot()
        assert bot.get_tweets_today() == 0

    def test_counts_after_publish(self):
        bot = _make_bot()
        bot.publish_alert(_alert(star_level=4))
        assert bot.get_tweets_today() == 1

    def test_resets_on_new_day(self):
        bot = _make_bot()
        # Set counter to yesterday
        bot._today_date = "2025-01-01"
        bot._tweets_today = [datetime(2025, 1, 1, tzinfo=timezone.utc)] * 5
        # Accessing today should reset
        count = bot.get_tweets_today()
        assert count == 0


# ── publish_alert ────────────────────────────────────────────


class TestPublishAlert:
    def test_publishes_3_star(self):
        bot = _make_bot()
        tweet_id = bot.publish_alert(_alert(star_level=3))
        assert tweet_id == "1234567890"
        bot.client.create_tweet.assert_called_once()

    def test_publishes_5_star(self):
        bot = _make_bot()
        tweet_id = bot.publish_alert(_alert(star_level=5))
        assert tweet_id == "1234567890"

    def test_skips_below_3_stars(self):
        bot = _make_bot()
        tweet_id = bot.publish_alert(_alert(star_level=2))
        assert tweet_id is None
        bot.client.create_tweet.assert_not_called()

    def test_skips_0_stars(self):
        bot = _make_bot()
        tweet_id = bot.publish_alert(_alert(star_level=0))
        assert tweet_id is None

    def test_skips_when_not_authenticated(self):
        bot = _make_bot(authenticated=False)
        tweet_id = bot.publish_alert(_alert(star_level=5))
        assert tweet_id is None

    def test_skips_when_daily_limit_reached(self):
        bot = _make_bot()
        now = datetime.now(timezone.utc)
        bot._today_date = now.strftime("%Y-%m-%d")
        bot._tweets_today = [now] * 10
        tweet_id = bot.publish_alert(_alert(star_level=5))
        assert tweet_id is None

    def test_tweet_text_contains_market(self):
        bot = _make_bot()
        bot.publish_alert(_alert(market_question="Will Bitcoin hit 100k?"))
        call_args = bot.client.create_tweet.call_args
        text = call_args.kwargs.get("text") or call_args[1].get("text")
        assert "Bitcoin" in text
        assert "SMART MONEY DETECTED" in text

    def test_tweet_text_has_no_score(self):
        """X tweets must never expose the score."""
        bot = _make_bot()
        bot.publish_alert(_alert(score=99, star_level=4))
        call_args = bot.client.create_tweet.call_args
        text = call_args.kwargs.get("text") or call_args[1].get("text")
        assert "Score" not in text
        assert "99" not in text

    def test_api_error_returns_none(self):
        bot = _make_bot()
        bot.client.create_tweet.side_effect = Exception("API error")
        tweet_id = bot.publish_alert(_alert(star_level=4))
        assert tweet_id is None

    def test_api_error_does_not_increment_counter(self):
        bot = _make_bot()
        bot.client.create_tweet.side_effect = Exception("API error")
        bot.publish_alert(_alert(star_level=4))
        assert bot.get_tweets_today() == 0


# ── publish_resolution ───────────────────────────────────────


class TestPublishResolution:
    def test_publishes_resolution(self):
        bot = _make_bot()
        alert = _alert(outcome="YES", direction="YES")
        tweet_id = bot.publish_resolution(alert)
        assert tweet_id == "1234567890"
        bot.client.create_tweet.assert_called_once()

    def test_resolution_text_contains_resolved(self):
        bot = _make_bot()
        alert = _alert(outcome="YES", direction="YES")
        bot.publish_resolution(alert)
        call_args = bot.client.create_tweet.call_args
        text = call_args.kwargs.get("text") or call_args[1].get("text")
        assert "ALERT RESOLVED" in text

    def test_skips_when_not_authenticated(self):
        bot = _make_bot(authenticated=False)
        tweet_id = bot.publish_resolution(_alert(outcome="YES"))
        assert tweet_id is None

    def test_respects_daily_limit(self):
        bot = _make_bot()
        now = datetime.now(timezone.utc)
        bot._today_date = now.strftime("%Y-%m-%d")
        bot._tweets_today = [now] * 10
        tweet_id = bot.publish_resolution(_alert(outcome="YES"))
        assert tweet_id is None


# ── Daily counter tracking ───────────────────────────────────


class TestDailyCounter:
    def test_increments_on_success(self):
        bot = _make_bot()
        assert bot.get_tweets_today() == 0
        bot.publish_alert(_alert(star_level=4))
        assert bot.get_tweets_today() == 1
        bot.publish_alert(_alert(star_level=5))
        assert bot.get_tweets_today() == 2

    def test_mixed_alert_and_resolution(self):
        bot = _make_bot()
        bot.publish_alert(_alert(star_level=3))
        bot.publish_resolution(_alert(outcome="YES"))
        assert bot.get_tweets_today() == 2

    def test_stops_at_limit(self):
        bot = _make_bot()
        for _ in range(10):
            bot.publish_alert(_alert(star_level=5))
        assert bot.get_tweets_today() == 10
        # 11th should be blocked
        result = bot.publish_alert(_alert(star_level=5))
        assert result is None
        assert bot.get_tweets_today() == 10
