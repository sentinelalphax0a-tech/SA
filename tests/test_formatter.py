"""Tests for the alert formatter."""

from src.publishing.formatter import AlertFormatter
from src.database.models import Alert


class TestAlertFormatter:
    def test_format_x(self):
        formatter = AlertFormatter()
        alert = Alert(
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
        text = formatter.format_x(alert)
        assert "SMART MONEY DETECTED" in text
        assert "Will X resign?" in text
        assert "$47,200" in text
        assert "3 coordinated wallets" in text

    def test_format_telegram(self):
        formatter = AlertFormatter()
        alert = Alert(
            market_id="m1",
            alert_type="accumulation",
            score=55,
            market_question="Will Y happen?",
            direction="NO",
            star_level=2,
            total_amount=12000.0,
        )
        text = formatter.format_telegram(alert)
        assert "Score: 55" in text
        assert "Will Y happen?" in text

    def test_format_whale(self):
        formatter = AlertFormatter()
        alert = Alert(
            market_id="m1",
            alert_type="whale_entry",
            score=30,
            market_question="Big event?",
            direction="YES",
            total_amount=25000.0,
            price_impact=0.032,
        )
        text = formatter.format_telegram_whale(alert)
        assert "WHALE ENTRY" in text
        assert "$25,000" in text
