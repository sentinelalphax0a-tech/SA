"""Tests for ML snapshot fields on alerts (feat: ML schema fix).

Verifies that:
  1. _build_alert() populates all 11 ML snapshot / *_initial fields.
  2. The cross-scan dedup update_fields dict never contains *_initial keys.
  3. MarketResolver skips non-YES/NO market outcomes (guard against mislabeled training data).
  4. Migration is idempotent — running migrate() twice raises no error.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from src.database.models import Alert, FilterResult, Market, ScoringResult
from src.main import _build_alert, _find_new_wallets
from src.tracking.resolver import MarketResolver


# ── Constants ─────────────────────────────────────────────────

_ML_INITIAL_FIELDS = {
    "scan_mode",
    "score_initial",
    "score_raw_initial",
    "odds_at_alert_initial",
    "total_amount_initial",
    "filters_triggered_initial",
    "market_category",
    "market_volume_24h_at_alert",
    "market_liquidity_at_alert",
    "hours_to_deadline",
    "wallets_count_initial",
}


# ── Helpers ───────────────────────────────────────────────────

def _sr(score: int = 60, score_raw: int = 50, star: int = 2,
        filters: list[FilterResult] | None = None) -> ScoringResult:
    return ScoringResult(
        score_raw=score_raw,
        multiplier=1.0,
        score_final=score,
        star_level=star,
        filters_triggered=filters or [],
    )


def _market(
    category: str | None = "Politics",
    volume_24h: float = 75_000.0,
    liquidity: float = 300_000.0,
    resolution_date: datetime | None = None,
    odds: float = 0.25,
) -> Market:
    return Market(
        market_id="mkt-1",
        question="Will X happen?",
        current_odds=odds,
        category=category,
        volume_24h=volume_24h,
        liquidity=liquidity,
        resolution_date=resolution_date,
    )


def _wallets(n: int = 2, amount: float = 5000.0) -> list[dict]:
    return [{"address": f"0x{i}", "total_amount": amount} for i in range(n)]


def _make_resolver():
    db = MagicMock()
    pm = MagicMock()
    return MarketResolver(db=db, polymarket=pm), db, pm


def _pending_alert(**overrides) -> dict:
    base = {
        "id": 1,
        "market_id": "mkt-1",
        "market_question": "Will X happen?",
        "direction": "YES",
        "odds_at_alert": 0.35,
        "outcome": "pending",
        "wallets": [{"address": "0xAAA", "total_amount": 5000}],
        "timestamp": (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),
    }
    base.update(overrides)
    return base


# ── Test 1: _build_alert populates all ML snapshot fields ─────


class TestBuildAlertMLFields:
    """_build_alert() must freeze all T0 snapshot fields."""

    def test_scan_mode_defaults_to_quick(self):
        alert = _build_alert(_market(), "YES", _sr(), _wallets())
        assert alert.scan_mode == "quick"

    def test_scan_mode_deep(self):
        alert = _build_alert(_market(), "YES", _sr(), _wallets(), scan_mode="deep")
        assert alert.scan_mode == "deep"

    def test_score_initial_matches_score_final(self):
        alert = _build_alert(_market(), "YES", _sr(score=75), _wallets())
        assert alert.score_initial == 75
        assert alert.score == 75  # live field also correct

    def test_score_raw_initial_matches_score_raw(self):
        alert = _build_alert(_market(), "YES", _sr(score_raw=55), _wallets())
        assert alert.score_raw_initial == 55
        assert alert.score_raw == 55

    def test_odds_at_alert_initial(self):
        m = _market(odds=0.30)
        alert = _build_alert(m, "YES", _sr(), _wallets())
        assert alert.odds_at_alert_initial == 0.30
        assert alert.odds_at_alert == 0.30

    def test_total_amount_initial(self):
        wallets = _wallets(n=3, amount=5000.0)  # 3 × 5000 = 15000
        alert = _build_alert(_market(), "YES", _sr(), wallets)
        assert alert.total_amount_initial == 15_000.0
        assert alert.total_amount == 15_000.0

    def test_wallets_count_initial(self):
        alert = _build_alert(_market(), "YES", _sr(), _wallets(4))
        assert alert.wallets_count_initial == 4

    def test_market_category(self):
        alert = _build_alert(_market(category="Sports"), "YES", _sr(), _wallets())
        assert alert.market_category == "Sports"

    def test_market_category_none(self):
        alert = _build_alert(_market(category=None), "YES", _sr(), _wallets())
        assert alert.market_category is None

    def test_market_volume_24h_at_alert(self):
        alert = _build_alert(_market(volume_24h=123_000.0), "YES", _sr(), _wallets())
        assert alert.market_volume_24h_at_alert == 123_000.0

    def test_market_liquidity_at_alert(self):
        alert = _build_alert(_market(liquidity=500_000.0), "YES", _sr(), _wallets())
        assert alert.market_liquidity_at_alert == 500_000.0

    def test_hours_to_deadline_future(self):
        resolution = datetime.now(timezone.utc) + timedelta(hours=48)
        alert = _build_alert(_market(resolution_date=resolution), "YES", _sr(), _wallets())
        assert alert.hours_to_deadline is not None
        assert 47 < alert.hours_to_deadline < 49

    def test_hours_to_deadline_past_is_negative(self):
        resolution = datetime.now(timezone.utc) - timedelta(hours=12)
        alert = _build_alert(_market(resolution_date=resolution), "YES", _sr(), _wallets())
        assert alert.hours_to_deadline is not None
        assert alert.hours_to_deadline < 0

    def test_hours_to_deadline_none_when_no_date(self):
        alert = _build_alert(_market(resolution_date=None), "YES", _sr(), _wallets())
        assert alert.hours_to_deadline is None

    def test_filters_triggered_initial_empty(self):
        alert = _build_alert(_market(), "YES", _sr(filters=[]), _wallets())
        assert alert.filters_triggered_initial == []

    def test_filters_triggered_initial_with_filter(self):
        fr = FilterResult(filter_id="W01", filter_name="W01", points=25, category="wallet")
        sr = _sr(filters=[fr])
        alert = _build_alert(_market(), "YES", sr, _wallets())
        assert isinstance(alert.filters_triggered_initial, list)
        assert len(alert.filters_triggered_initial) == 1
        assert alert.filters_triggered_initial[0]["filter_id"] == "W01"

    def test_all_non_nullable_initial_fields_populated(self):
        """With a complete market, all 11 ML snapshot fields must be non-None."""
        resolution = datetime.now(timezone.utc) + timedelta(hours=24)
        alert = _build_alert(
            _market(resolution_date=resolution),
            "YES",
            _sr(),
            _wallets(2),
            scan_mode="quick",
        )
        for field in _ML_INITIAL_FIELDS - {"market_category"}:
            # market_category can be None if market has no category; skip it here
            val = getattr(alert, field)
            assert val is not None, f"Field '{field}' should not be None"


# ── Test 2: cross-scan dedup never writes *_initial fields ────


class TestProtectedFieldsNotUpdated:
    """The update_fields dict built by cross-scan dedup must never include *_initial keys."""

    def _build_cross_scan_update_fields(self, alert: Alert, existing: dict) -> dict:
        """Replicate the update_fields construction from run_scan() cross-scan dedup block."""
        existing_score = existing.get("score") or 0
        existing_star = existing.get("star_level") or 0
        existing_amount = existing.get("total_amount") or 0.0
        existing_wallets = existing.get("wallets") or []

        update_fields: dict = {
            "odds_at_alert": alert.odds_at_alert,
        }

        new_wallets = _find_new_wallets(alert.wallets or [], existing_wallets)
        if new_wallets:
            update_fields["wallets"] = existing_wallets + new_wallets
            new_wallets_amount = sum(w.get("total_amount", 0) for w in new_wallets)
            update_fields["total_amount"] = existing_amount + new_wallets_amount
        elif (alert.total_amount or 0) > existing_amount:
            update_fields["total_amount"] = alert.total_amount

        if (alert.score or 0) > existing_score:
            update_fields["score"] = alert.score
            update_fields["score_raw"] = alert.score_raw
            update_fields["multiplier"] = alert.multiplier
            update_fields["filters_triggered"] = alert.filters_triggered or []
            if (alert.star_level or 0) > existing_star:
                update_fields["star_level"] = alert.star_level

        return update_fields

    def _make_alert_with_ml_fields(self) -> Alert:
        return Alert(
            market_id="mkt-1",
            alert_type="accumulation",
            score=70,
            direction="YES",
            score_raw=60,
            multiplier=1.0,
            star_level=3,
            wallets=[{"address": "0x2", "total_amount": 8000.0}],
            total_amount=8000.0,
            odds_at_alert=0.25,
            # ML snapshot fields — must NOT end up in update_fields
            scan_mode="deep",
            score_initial=70,
            score_raw_initial=60,
            odds_at_alert_initial=0.25,
            total_amount_initial=8000.0,
            filters_triggered_initial=[],
            market_category="Politics",
            market_volume_24h_at_alert=75_000.0,
            market_liquidity_at_alert=300_000.0,
            hours_to_deadline=48.0,
            wallets_count_initial=1,
        )

    def _existing(self, score: int = 50, star: int = 2, amount: float = 5000.0) -> dict:
        return {
            "id": 99,
            "score": score,
            "star_level": star,
            "total_amount": amount,
            "wallets": [{"address": "0x1", "total_amount": amount}],
        }

    def test_no_initial_field_in_update_score_upgrade(self):
        """When score improves, update_fields must not include any *_initial field."""
        alert = self._make_alert_with_ml_fields()
        update_fields = self._build_cross_scan_update_fields(alert, self._existing(score=50))

        for field in _ML_INITIAL_FIELDS:
            assert field not in update_fields, (
                f"Protected field '{field}' must not appear in update_fields"
            )

    def test_no_initial_field_when_only_odds_updated(self):
        """Even minimal update (odds only) must not leak *_initial fields."""
        alert = self._make_alert_with_ml_fields()
        # existing score >= alert score → no score upgrade branch
        # existing wallet matches alert wallet → no new wallets → no wallet update
        existing = {
            "id": 99,
            "score": 80,
            "star_level": 4,
            "total_amount": 10_000.0,
            "wallets": [{"address": "0x2", "total_amount": 10_000.0}],  # same addr as alert
        }
        update_fields = self._build_cross_scan_update_fields(alert, existing)

        # Only odds_at_alert should be updated (no score/wallet upgrades)
        assert set(update_fields.keys()) == {"odds_at_alert"}
        for field in _ML_INITIAL_FIELDS:
            assert field not in update_fields

    def test_no_initial_field_when_wallets_added(self):
        """When new wallets are merged in, no *_initial field must appear."""
        alert = self._make_alert_with_ml_fields()
        existing = self._existing(score=80, star=4, amount=5000.0)
        update_fields = self._build_cross_scan_update_fields(alert, existing)

        for field in _ML_INITIAL_FIELDS:
            assert field not in update_fields


# ── Test 3: Resolver skips non-standard outcomes ──────────────


class TestResolverNonStandardOutcome:
    """Markets with non-YES/NO outcomes must be skipped entirely."""

    def test_skips_na_outcome(self):
        resolver, db, pm = _make_resolver()
        db.get_pending_market_ids.return_value = {"mkt-1"}
        pm.get_market_resolution.return_value = {"resolved": True, "outcome": "N/A"}

        result = resolver.run()

        assert result == {"resolved": 0, "correct": 0, "incorrect": 0}
        db.update_market_resolution.assert_not_called()
        db.get_pending_alerts_for_market.assert_not_called()

    def test_skips_cancelled_outcome(self):
        resolver, db, pm = _make_resolver()
        db.get_pending_market_ids.return_value = {"mkt-1"}
        pm.get_market_resolution.return_value = {"resolved": True, "outcome": "CANCELLED"}

        result = resolver.run()

        assert result == {"resolved": 0, "correct": 0, "incorrect": 0}

    def test_skips_void_outcome(self):
        resolver, db, pm = _make_resolver()
        db.get_pending_market_ids.return_value = {"mkt-1"}
        pm.get_market_resolution.return_value = {"resolved": True, "outcome": "VOID"}

        result = resolver.run()

        assert result == {"resolved": 0, "correct": 0, "incorrect": 0}

    def test_yes_outcome_still_resolved(self):
        """YES is a standard outcome — must be resolved normally."""
        resolver, db, pm = _make_resolver()
        db.get_pending_market_ids.return_value = {"mkt-1"}
        db.get_pending_alerts_for_market.return_value = [_pending_alert(direction="YES")]
        pm.get_market_resolution.return_value = {"resolved": True, "outcome": "YES"}

        result = resolver.run()

        assert result["resolved"] == 1
        assert result["correct"] == 1

    def test_no_outcome_still_resolved(self):
        """NO is a standard outcome — must be resolved normally."""
        resolver, db, pm = _make_resolver()
        db.get_pending_market_ids.return_value = {"mkt-2"}
        db.get_pending_alerts_for_market.return_value = [
            _pending_alert(market_id="mkt-2", direction="NO", odds_at_alert=0.40)
        ]
        pm.get_market_resolution.return_value = {"resolved": True, "outcome": "NO"}

        result = resolver.run()

        assert result["resolved"] == 1
        assert result["correct"] == 1

    def test_mixed_markets_some_nonstandard(self):
        """Markets with N/A outcome are skipped; YES/NO markets are resolved."""
        resolver, db, pm = _make_resolver()

        db.get_pending_market_ids.return_value = {"mkt-yes", "mkt-na"}

        def get_resolution(mid):
            return {
                "mkt-yes": {"resolved": True, "outcome": "YES"},
                "mkt-na": {"resolved": True, "outcome": "N/A"},
            }[mid]

        pm.get_market_resolution.side_effect = get_resolution
        db.get_pending_alerts_for_market.return_value = [
            _pending_alert(market_id="mkt-yes", direction="YES")
        ]

        result = resolver.run()

        # Only the YES market was resolved
        assert result["resolved"] == 1
        # get_pending_alerts_for_market called exactly once (for mkt-yes, not mkt-na)
        assert db.get_pending_alerts_for_market.call_count == 1


# ── Test 4: Migration idempotency ────────────────────────────


class TestMigrationIdempotency:
    """Migration uses ADD COLUMN IF NOT EXISTS — running twice must not raise."""

    def test_migrate_calls_exec_sql_for_each_column(self):
        from migrations.add_ml_snapshot_fields import migrate, _SQL_STATEMENTS

        db = MagicMock()
        db.client.rpc.return_value.execute.return_value = MagicMock()

        migrate(db)

        assert db.client.rpc.call_count == len(_SQL_STATEMENTS)
        for c in db.client.rpc.call_args_list:
            args, _ = c
            assert args[0] == "exec_sql"

    def test_migrate_twice_no_error(self):
        """Second run must succeed (idempotent)."""
        from migrations.add_ml_snapshot_fields import migrate

        db = MagicMock()
        db.client.rpc.return_value.execute.return_value = MagicMock()

        migrate(db)
        migrate(db)  # no exception expected

    def test_all_sql_statements_are_idempotent(self):
        """Every SQL must use IF NOT EXISTS to guarantee idempotency."""
        from migrations.add_ml_snapshot_fields import _SQL_STATEMENTS

        for sql in _SQL_STATEMENTS:
            assert "IF NOT EXISTS" in sql.upper(), (
                f"SQL not idempotent (missing IF NOT EXISTS): {sql}"
            )

    def test_eleven_columns_defined(self):
        from migrations.add_ml_snapshot_fields import _SQL_STATEMENTS

        assert len(_SQL_STATEMENTS) == 11
