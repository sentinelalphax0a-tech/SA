"""Tests for the dashboard generator."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.dashboard.generate_dashboard import (
    build_html,
    compute_stats,
    enrich_alerts,
    generate,
)


# ── Fixtures ──────────────────────────────────────────


def _alert(**overrides) -> dict:
    """Build a default alert dict with sensible defaults."""
    base = {
        "id": 1,
        "market_id": "m1",
        "alert_type": "accumulation",
        "score": 75,
        "score_raw": 60,
        "multiplier": 1.25,
        "market_question": "Will X happen?",
        "direction": "YES",
        "star_level": 3,
        "wallets": [
            {
                "address": "0xAAAABBBBCCCCDDDD",
                "total_amount": 5000,
                "trade_count": 3,
                "time_span_hours": 2.5,
                "avg_entry_price": 0.35,
                "trades": [],
            }
        ],
        "total_amount": 5000.0,
        "odds_at_alert": 0.35,
        "outcome": "pending",
        "filters_triggered": [
            {
                "filter_id": "B01",
                "points": 20,
                "filter_name": "Drip Buy",
                "details": "5 buys in 48h",
            },
        ],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "resolved_at": None,
        "actual_return": None,
        "odds_max": None,
        "odds_min": None,
        "has_news": False,
        "news_summary": None,
    }
    base.update(overrides)
    return base


def _market(**overrides) -> dict:
    """Build a default market dict."""
    base = {
        "market_id": "m1",
        "question": "Will X happen?",
        "slug": "will-x-happen",
        "current_odds": 0.45,
        "resolution_date": (
            datetime.now(timezone.utc) + timedelta(days=5)
        ).isoformat(),
        "is_resolved": False,
    }
    base.update(overrides)
    return base


# ── Test: build_html ──────────────────────────────────


class TestBuildHtml:
    def test_injects_alerts_data(self):
        template = '<script>const ALERTS_DATA = [/* __ALERTS_DATA__ */];</script>'
        html = build_html('[{"id":1}]', '{}', "2026-01-01T00:00:00Z", template)
        assert '[{"id":1}]' in html
        assert "__ALERTS_DATA__" not in html

    def test_injects_stats_data(self):
        template = '<script>const STATS = {/* __STATS_DATA__ */};</script>'
        html = build_html('[]', '{"total":5}', "2026-01-01T00:00:00Z", template)
        assert '{"total":5}' in html
        assert "__STATS_DATA__" not in html

    def test_injects_generated_at(self):
        template = 'const GENERATED_AT = "/* __GENERATED_AT__ */";'
        html = build_html('[]', '{}', "2026-02-14T12:00:00Z", template)
        assert "2026-02-14T12:00:00Z" in html
        assert "__GENERATED_AT__" not in html

    def test_output_is_valid_html(self):
        template = '<!DOCTYPE html><html><body>/* __ALERTS_DATA__ *//* __STATS_DATA__ *//* __GENERATED_AT__ */</body></html>'
        html = build_html('[]', '{}', "now", template)
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html


# ── Test: compute_stats ───────────────────────────────


class TestComputeStats:
    def test_total_alerts_count(self):
        alerts = [_alert(id=1), _alert(id=2), _alert(id=3)]
        stats = compute_stats(alerts, {})
        assert stats["total_alerts"] == 3

    def test_active_vs_resolved_split(self):
        alerts = [
            _alert(id=1, outcome="pending"),
            _alert(id=2, outcome="correct"),
            _alert(id=3, outcome="incorrect"),
            _alert(id=4, outcome="pending"),
        ]
        stats = compute_stats(alerts, {})
        assert stats["active_alerts"] == 2
        assert stats["resolved_alerts"] == 2

    def test_accuracy_3plus_stars(self):
        alerts = [
            _alert(id=1, star_level=3, outcome="correct"),
            _alert(id=2, star_level=3, outcome="correct"),
            _alert(id=3, star_level=3, outcome="incorrect"),
            _alert(id=4, star_level=4, outcome="correct"),
            # 2-star should not count
            _alert(id=5, star_level=2, outcome="incorrect"),
        ]
        stats = compute_stats(alerts, {})
        # 3 correct out of 4 resolved 3+ star = 75%
        assert stats["accuracy_3plus"] == 75.0

    def test_accuracy_excludes_pending(self):
        alerts = [
            _alert(id=1, star_level=3, outcome="correct"),
            _alert(id=2, star_level=3, outcome="pending"),
        ]
        stats = compute_stats(alerts, {})
        assert stats["accuracy_3plus"] == 100.0

    def test_star_breakdown_counts(self):
        alerts = [
            _alert(id=1, star_level=5, outcome="correct"),
            _alert(id=2, star_level=5, outcome="incorrect"),
            _alert(id=3, star_level=5, outcome="pending"),
            _alert(id=4, star_level=3, outcome="correct"),
        ]
        stats = compute_stats(alerts, {})
        star5 = stats["by_star"]["5"]
        assert star5["count"] == 3
        assert star5["correct"] == 1
        assert star5["incorrect"] == 1
        assert star5["pending"] == 1
        assert star5["accuracy"] == 50.0

        star3 = stats["by_star"]["3"]
        assert star3["count"] == 1
        assert star3["correct"] == 1

    def test_empty_alerts_returns_defaults(self):
        stats = compute_stats([], {})
        assert stats["total_alerts"] == 0
        assert stats["active_alerts"] == 0
        assert stats["resolved_alerts"] == 0
        assert stats["accuracy_3plus"] is None
        assert stats["by_star"]["1"]["count"] == 0

    def test_filter_distribution_counts(self):
        alerts = [
            _alert(
                id=1,
                outcome="correct",
                filters_triggered=[
                    {"filter_id": "B01", "points": 20, "filter_name": "Drip"},
                    {"filter_id": "W01", "points": 25, "filter_name": "New"},
                ],
            ),
            _alert(
                id=2,
                outcome="incorrect",
                filters_triggered=[
                    {"filter_id": "B01", "points": 20, "filter_name": "Drip"},
                ],
            ),
        ]
        stats = compute_stats(alerts, {})
        fd = {f["filter_id"]: f for f in stats["filter_distribution"]}
        assert fd["B01"]["correct"] == 1
        assert fd["B01"]["incorrect"] == 1
        assert fd["W01"]["correct"] == 1
        assert fd["W01"]["incorrect"] == 0


# ── Test: enrich_alerts ───────────────────────────────


class TestEnrichAlerts:
    def test_row_class_winning(self):
        alerts = [_alert(odds_at_alert=0.30, direction="YES")]
        markets = {"m1": _market(current_odds=0.50)}
        result = enrich_alerts(alerts, markets)
        assert result[0]["row_class"] == "winning"

    def test_row_class_losing(self):
        alerts = [_alert(odds_at_alert=0.50, direction="YES")]
        markets = {"m1": _market(current_odds=0.30)}
        result = enrich_alerts(alerts, markets)
        assert result[0]["row_class"] == "losing"

    def test_row_class_correct(self):
        alerts = [_alert(outcome="correct")]
        result = enrich_alerts(alerts, {})
        assert result[0]["row_class"] == "correct"

    def test_row_class_incorrect(self):
        alerts = [_alert(outcome="incorrect")]
        result = enrich_alerts(alerts, {})
        assert result[0]["row_class"] == "incorrect"

    def test_direction_no_adjusts_winning_logic(self):
        # For NO direction: current_odds going DOWN means we're winning
        alerts = [_alert(odds_at_alert=0.60, direction="NO")]
        markets = {"m1": _market(current_odds=0.40)}
        result = enrich_alerts(alerts, markets)
        # NO adj: entry = 1-0.60 = 0.40, current = 1-0.40 = 0.60 → winning
        assert result[0]["row_class"] == "winning"
        assert result[0]["odds_change_pct"] > 0

    def test_polymarket_url(self):
        alerts = [_alert()]
        markets = {"m1": _market(slug="will-x-happen")}
        result = enrich_alerts(alerts, markets)
        assert result[0]["polymarket_url"] == "https://polymarket.com/event/will-x-happen"

    def test_odds_change_pct(self):
        alerts = [_alert(odds_at_alert=0.40, direction="YES")]
        markets = {"m1": _market(current_odds=0.50)}
        result = enrich_alerts(alerts, markets)
        # (0.50 - 0.40) / 0.40 * 100 = 25.0%
        assert result[0]["odds_change_pct"] == 25.0

    def test_closing_soon_row_class(self):
        soon = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
        alerts = [_alert(outcome="pending")]
        markets = {"m1": _market(resolution_date=soon)}
        result = enrich_alerts(alerts, markets)
        assert result[0]["row_class"] == "closing-soon"


# ── Test: generate (integration) ─────────────────────


class TestGenerate:
    @patch("src.dashboard.generate_dashboard.SupabaseClient")
    def test_generates_html_file(self, mock_cls, tmp_path):
        db = MagicMock()
        mock_cls.return_value = db

        # Mock Supabase responses
        alerts_resp = MagicMock()
        alerts_resp.data = [_alert()]
        markets_resp = MagicMock()
        markets_resp.data = [_market()]
        scans_resp = MagicMock()
        scans_resp.data = [{"timestamp": "2026-01-01T00:00:00Z", "status": "success"}]

        table_mock = MagicMock()
        table_mock.select.return_value = table_mock
        table_mock.order.return_value = table_mock
        table_mock.limit.return_value = table_mock
        table_mock.execute.side_effect = [alerts_resp, markets_resp, scans_resp]

        db.client.table.return_value = table_mock

        output = generate(output_dir=tmp_path)

        assert output.exists()
        content = output.read_text()
        assert len(content) > 100
        assert "<!DOCTYPE html>" in content

    @patch("src.dashboard.generate_dashboard.SupabaseClient")
    def test_html_contains_alerts_data(self, mock_cls, tmp_path):
        db = MagicMock()
        mock_cls.return_value = db

        alert = _alert(market_question="Test shutdown market?")
        alerts_resp = MagicMock()
        alerts_resp.data = [alert]
        markets_resp = MagicMock()
        markets_resp.data = [_market()]
        scans_resp = MagicMock()
        scans_resp.data = []

        table_mock = MagicMock()
        table_mock.select.return_value = table_mock
        table_mock.order.return_value = table_mock
        table_mock.limit.return_value = table_mock
        table_mock.execute.side_effect = [alerts_resp, markets_resp, scans_resp]

        db.client.table.return_value = table_mock

        output = generate(output_dir=tmp_path)
        content = output.read_text()
        assert "ALERTS_DATA" in content
        assert "Test shutdown market?" in content

    @patch("src.dashboard.generate_dashboard.SupabaseClient")
    def test_html_contains_stats(self, mock_cls, tmp_path):
        db = MagicMock()
        mock_cls.return_value = db

        alerts_resp = MagicMock()
        alerts_resp.data = [
            _alert(id=1, star_level=4, outcome="correct"),
            _alert(id=2, star_level=3, outcome="incorrect"),
            _alert(id=3, star_level=5, outcome="pending"),
        ]
        markets_resp = MagicMock()
        markets_resp.data = [_market()]
        scans_resp = MagicMock()
        scans_resp.data = []

        table_mock = MagicMock()
        table_mock.select.return_value = table_mock
        table_mock.order.return_value = table_mock
        table_mock.limit.return_value = table_mock
        table_mock.execute.side_effect = [alerts_resp, markets_resp, scans_resp]

        db.client.table.return_value = table_mock

        output = generate(output_dir=tmp_path)
        content = output.read_text()
        assert "STATS" in content
        assert '"total_alerts": 3' in content
        assert '"active_alerts": 1' in content

    def test_filters_work(self):
        """Verify compute_stats filtering/counting produces correct results."""
        alerts = [
            _alert(id=1, star_level=5, outcome="correct", actual_return=50.0),
            _alert(id=2, star_level=5, outcome="correct", actual_return=30.0),
            _alert(id=3, star_level=5, outcome="incorrect", actual_return=-100.0),
            _alert(id=4, star_level=3, outcome="correct", actual_return=20.0),
            _alert(id=5, star_level=3, outcome="incorrect", actual_return=-100.0),
            _alert(id=6, star_level=3, outcome="incorrect", actual_return=-100.0),
            _alert(id=7, star_level=1, outcome="pending"),
            _alert(id=8, star_level=2, outcome="correct", actual_return=10.0),
        ]
        stats = compute_stats(alerts, {})

        # Overall
        assert stats["total_alerts"] == 8
        assert stats["active_alerts"] == 1
        assert stats["resolved_alerts"] == 7

        # 3+ star accuracy: 3 correct / 6 resolved = 50%
        assert stats["accuracy_3plus"] == 50.0

        # Star 5: 2 correct / 3 = 66.7%
        assert stats["by_star"]["5"]["accuracy"] == 66.7

        # Star 3: 1 correct / 3 = 33.3%
        assert stats["by_star"]["3"]["accuracy"] == 33.3

        # Star 1: no resolved, accuracy None
        assert stats["by_star"]["1"]["accuracy"] is None
        assert stats["by_star"]["1"]["pending"] == 1
