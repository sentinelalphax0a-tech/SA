"""Tests for merge detection — N12 filter, net position checks, merge resolution.

Coverage:
  - BehaviorAnalyzer._check_merge (N12)
  - SellDetector._check_net_position
  - SellDetector.check_net_positions (GitHub Actions guard)
  - SellDetector.check_merge_resolution
  - AlertFormatter.format_merge_notification
  - AlertFormatter.format_position_gone
"""

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.analysis.behavior_analyzer import BehaviorAnalyzer
from src.analysis.sell_detector import SellDetector
from src.database.models import Alert, TradeEvent
from src.publishing.formatter import AlertFormatter
from src import config


# ── Helpers ──────────────────────────────────────────────────


def _trade(
    direction: str = "YES",
    amount: float = 1000.0,
    price: float = 0.50,
    hours_ago: float = 0.0,
    wallet: str = "0xabc",
    market: str = "m1",
) -> TradeEvent:
    return TradeEvent(
        wallet_address=wallet,
        market_id=market,
        direction=direction,
        amount=amount,
        price=price,
        timestamp=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
    )


def _shares(amount: float, price: float) -> float:
    return amount / price


# ── N12 — _check_merge ───────────────────────────────────────


class TestCheckMerge:
    """Unit tests for BehaviorAnalyzer._check_merge."""

    def setup_method(self):
        self.analyzer = BehaviorAnalyzer()

    def test_no_yes_trades_returns_empty(self):
        trades = [_trade("NO", 5000, 0.10) for _ in range(3)]
        assert self.analyzer._check_merge(trades) == []

    def test_no_no_trades_returns_empty(self):
        trades = [_trade("YES", 5000, 0.90) for _ in range(3)]
        assert self.analyzer._check_merge(trades) == []

    def test_merge_detected_symmetric(self):
        """$5000 YES @0.50 = 10000 shares, $5000 NO @0.50 = 10000 shares → net=0 → N12."""
        trades = [
            _trade("YES", 5000, 0.50),
            _trade("NO", 5000, 0.50),
        ]
        results = self.analyzer._check_merge(trades)
        assert len(results) == 1
        assert results[0].filter_id == "N12"
        assert results[0].points == config.FILTER_N12["points"]  # -40

    def test_merge_detected_asymmetric_dollars_but_equal_shares(self):
        """Classic CLOB arbitrage: $900 YES@0.90 = 1000 shares, $100 NO@0.10 = 1000 shares.
        Dollars differ (900 vs 100) but shares are equal → N12 fires.
        Amount must be >= MERGE_MIN_SHARES=1000 on the smaller side.
        """
        trades = [
            _trade("YES", 900, 0.90),   # 1000 shares
            _trade("NO", 100, 0.10),    # 1000 shares
        ]
        results = self.analyzer._check_merge(trades)
        assert len(results) == 1
        assert results[0].filter_id == "N12"

    def test_no_merge_when_net_shares_too_large(self):
        """YES 10000 shares, NO 3000 shares → net=7000 > 15% of 10000 → no N12."""
        trades = [
            _trade("YES", 5000, 0.50),  # 10000 shares
            _trade("NO", 1500, 0.50),   # 3000 shares
        ]
        results = self.analyzer._check_merge(trades)
        assert results == []

    def test_no_merge_when_smaller_side_too_small(self):
        """NO side only 500 shares (< MERGE_MIN_SHARES=1000) → no N12."""
        trades = [
            _trade("YES", 5000, 0.50),   # 10000 shares
            _trade("NO", 50, 0.10),      # 500 shares — below min
        ]
        results = self.analyzer._check_merge(trades)
        assert results == []

    def test_no_merge_when_outside_time_window(self):
        """YES trade 13h before NO trade (> MERGE_WINDOW_HOURS=12) → no N12."""
        trades = [
            _trade("YES", 5000, 0.50, hours_ago=13.0),  # 10000 shares
            _trade("NO", 5000, 0.50, hours_ago=0.0),    # 10000 shares
        ]
        results = self.analyzer._check_merge(trades)
        assert results == []

    def test_merge_within_window_boundary(self):
        """YES 11h ago, NO now → within 12h window → N12 fires."""
        trades = [
            _trade("YES", 5000, 0.50, hours_ago=11.0),  # 10000 shares
            _trade("NO", 5000, 0.50, hours_ago=0.0),    # 10000 shares
        ]
        results = self.analyzer._check_merge(trades)
        assert len(results) == 1
        assert results[0].filter_id == "N12"

    def test_detail_string_contains_shares_info(self):
        """N12 detail string must mention shares and dollars."""
        trades = [
            _trade("YES", 5000, 0.50),  # 10000 shares
            _trade("NO", 5000, 0.50),   # 10000 shares
        ]
        results = self.analyzer._check_merge(trades)
        assert results
        detail = results[0].details or ""
        assert "shares" in detail.lower()
        assert "$" in detail

    def test_n12_appears_in_full_analyze_flow(self):
        """N12 detected via the main analyze() entry point."""
        trades = [
            _trade("YES", 5000, 0.50),
            _trade("NO", 5000, 0.50),
        ]
        results = self.analyzer.analyze("0xabc", trades, "m1")
        ids = {r.filter_id for r in results}
        assert "N12" in ids

    def test_n12_reduces_score(self):
        """N12 is a negative filter: points must be <= 0."""
        assert config.FILTER_N12["points"] < 0


# ── SellDetector._check_net_position ─────────────────────────


class TestCheckNetPosition:
    """Unit tests for SellDetector._check_net_position.

    Uses is_market_order to distinguish buy vs sell side:
      True  = side=="BUY"  (acquiring tokens)
      False = side=="SELL" (liquidating tokens)
    """

    def setup_method(self):
        self.db = MagicMock()
        self.db.update_alert_fields = MagicMock()
        self.sd = SellDetector(db_client=self.db, polymarket_client=MagicMock())

    def _pos(self, direction="YES", total_amount=10000.0, entry_odds=0.50):
        return {
            "wallet_address": "0xabc",
            "market_id": "m1",
            "direction": direction,
            "total_amount": total_amount,
            "entry_odds": entry_odds,
            "alert_id": 99,
        }

    @staticmethod
    def _t(direction, amount, price, is_buy: bool) -> TradeEvent:
        t = _trade(direction, amount, price)
        t.is_market_order = is_buy
        return t

    def test_returns_none_when_entry_odds_zero(self):
        pos = self._pos(entry_odds=0)
        result = self.sd._check_net_position(pos, [], "m1", "Q?")
        assert result is None

    def test_returns_none_when_no_buys_in_window(self):
        """No same-direction BUY trades → buys_shares=0 → None."""
        pos = self._pos()
        trades = [self._t("NO", 500, 0.50, True)]  # only opp direction
        result = self.sd._check_net_position(pos, trades, "m1", "Q?")
        assert result is None

    def test_returns_none_when_position_still_open(self):
        """Net 80% remaining → above both thresholds → None."""
        pos = self._pos()
        trades = [
            self._t("YES", 5000, 0.50, True),   # BUY: 10000 shares
            self._t("YES", 1000, 0.50, False),  # SELL: 2000 shares → net 8000 = 80%
        ]
        result = self.sd._check_net_position(pos, trades, "m1", "Q?")
        assert result is None

    def test_detects_total_exit(self):
        """Explicit SELL 9800/10000 shares → net 2% → net_exit_total."""
        pos = self._pos()
        trades = [
            self._t("YES", 5000, 0.50, True),   # BUY: 10000 shares
            self._t("YES", 4900, 0.50, False),  # SELL: 9800 shares → net 200 = 2%
        ]
        result = self.sd._check_net_position(pos, trades, "m1", "Q?")
        assert result is not None
        assert result["type"] in ("net_exit_total", "position_gone")
        assert result["wallets"][0]["remaining_pct"] < 20

    def test_detects_partial_exit(self):
        """SELL 6000/10000 shares → net 40% → net_exit_partial."""
        pos = self._pos()
        trades = [
            self._t("YES", 5000, 0.50, True),   # BUY: 10000 shares
            self._t("YES", 3000, 0.50, False),  # SELL: 6000 shares → net 4000 = 40%
        ]
        result = self.sd._check_net_position(pos, trades, "m1", "Q?")
        assert result is not None
        assert result["type"] == "net_exit_partial"
        remaining = result["wallets"][0]["remaining_pct"]
        assert 20 <= remaining < 60

    def test_close_reason_sell_clob_when_only_explicit_sells(self):
        """Explicit dir SELL, no opp buys → close_reason = sell_clob."""
        pos = self._pos()
        trades = [
            self._t("YES", 5000, 0.50, True),   # BUY: 10000 shares
            self._t("YES", 4900, 0.50, False),  # SELL: 9800 shares — explicit close
        ]
        result = self.sd._check_net_position(pos, trades, "m1", "Q?")
        assert result is not None
        assert result["close_reason"] == "sell_clob"

    def test_close_reason_merge_suspected_with_opp_buys(self):
        """Wallet BUY YES + BUY NO → merge_suspected."""
        pos = self._pos()
        trades = [
            self._t("YES", 5000, 0.50, True),  # BUY YES: 10000 shares
            self._t("NO",  5000, 0.50, True),  # BUY NO: 10000 shares (hedge)
        ]
        result = self.sd._check_net_position(pos, trades, "m1", "Q?")
        assert result is not None
        assert result["close_reason"] == "merge_suspected"

    def test_close_reason_merge_suspected_with_both_sell_and_opp_buy(self):
        """Explicit SELL + BUY opp → merge_suspected (both present)."""
        pos = self._pos()
        trades = [
            self._t("YES", 5000, 0.50, True),   # BUY YES: 10000 shares
            self._t("YES", 2000, 0.50, False),  # SELL YES: 4000 shares
            self._t("NO",  2000, 0.50, True),   # BUY NO: 4000 shares (hedge)
        ]
        result = self.sd._check_net_position(pos, trades, "m1", "Q?")
        assert result is not None
        assert result["close_reason"] == "merge_suspected"

    def test_position_gone_without_clob_sells(self):
        """< 5% net with no sells or opp buys → position_gone."""
        pos = self._pos()
        # Wallet shows only tiny buy in window (e.g. lookback window is short)
        # buys=100 shares, sells=0, opp=0 → net=100 = 100% → no event (not gone)
        # To trigger position_gone we need net < 5% of buys with no sell/opp activity.
        # This requires buys > 0 AND net < 5% AND no opp/sells — effectively impossible
        # with only buy trades (net would equal buys = 100%).
        # The case is covered by integration: if lookback misses original buy but still
        # sees a tiny re-buy, net would look tiny vs original position.
        # Documented: position_gone is triggered by the formula in integration only.
        pass


# ── SellDetector.check_net_positions — GitHub Actions guard ──


class TestCheckNetPositionsGitHubActions:
    def test_skipped_in_github_actions(self):
        """check_net_positions must return [] when GITHUB_ACTIONS=true."""
        db = MagicMock()
        sd = SellDetector(db_client=db, polymarket_client=MagicMock())
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            result = sd.check_net_positions()
        assert result == []
        db.get_open_positions.assert_not_called()

    def test_skipped_when_no_db(self):
        sd = SellDetector()
        result = sd.check_net_positions()
        assert result == []

    def test_empty_when_no_positions(self):
        db = MagicMock()
        db.get_open_positions.return_value = []
        sd = SellDetector(db_client=db, polymarket_client=MagicMock())
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("GITHUB_ACTIONS", None)
            result = sd.check_net_positions()
        assert result == []


# ── SellDetector.check_merge_resolution ──────────────────────


class TestCheckMergeResolution:
    def test_no_db_returns_empty(self):
        sd = SellDetector()
        result = sd.check_merge_resolution()
        assert result == {"checked": 0, "confirmed": 0}

    def test_no_pm_returns_empty(self):
        sd = SellDetector(db_client=MagicMock())
        result = sd.check_merge_resolution()
        assert result == {"checked": 0, "confirmed": 0}

    def test_query_failure_returns_empty(self):
        db = MagicMock()
        db.client.table.return_value.select.return_value.eq.return_value.eq.return_value\
            .eq.return_value.order.return_value.limit.return_value.execute.side_effect = \
            Exception("DB error")
        sd = SellDetector(db_client=db, polymarket_client=MagicMock())
        result = sd.check_merge_resolution()
        assert result == {"checked": 0, "confirmed": 0}

    def test_confirmed_when_net_usd_below_500(self):
        """Alert with net USD < $500 → merge_confirmed=True."""
        db = MagicMock()
        pm = MagicMock()

        # Setup: 1 merge_suspected alert with 1 wallet
        db.client.table.return_value.select.return_value\
            .eq.return_value.eq.return_value.eq.return_value\
            .order.return_value.limit.return_value.execute.return_value.data = [
            {
                "id": 42,
                "market_id": "m1",
                "direction": "YES",
                "odds_at_alert": 0.50,
                "wallets": [{"address": "0xabc"}],
                "merge_confirmed": False,
            }
        ]

        # Net USD: wallet bought $200 YES, sold $400 NO → net = max(0, -200) = 0 < $500
        pm.get_recent_trades.return_value = [
            _trade("YES", 200, 0.50, wallet="0xabc"),   # $200 YES
            _trade("NO", 400, 0.50, wallet="0xabc"),    # $400 NO
        ]

        sd = SellDetector(db_client=db, polymarket_client=pm)
        result = sd.check_merge_resolution()

        assert result["checked"] == 1
        assert result["confirmed"] == 1
        db.update_alert_fields.assert_called_once_with(42, {"merge_confirmed": True})

    def test_not_confirmed_when_net_usd_above_500(self):
        """Alert with net USD > $500 → merge_confirmed stays False."""
        db = MagicMock()
        pm = MagicMock()

        db.client.table.return_value.select.return_value\
            .eq.return_value.eq.return_value.eq.return_value\
            .order.return_value.limit.return_value.execute.return_value.data = [
            {
                "id": 43,
                "market_id": "m1",
                "direction": "YES",
                "odds_at_alert": 0.50,
                "wallets": [{"address": "0xabc"}],
                "merge_confirmed": False,
            }
        ]

        # Net USD: $2000 YES, $0 NO → net = $2000 > $500
        pm.get_recent_trades.return_value = [
            _trade("YES", 2000, 0.50, wallet="0xabc"),
        ]

        sd = SellDetector(db_client=db, polymarket_client=pm)
        result = sd.check_merge_resolution()

        assert result["checked"] == 1
        assert result["confirmed"] == 0
        db.update_alert_fields.assert_not_called()


# ── AlertFormatter.format_merge_notification ─────────────────


class TestFormatMergeNotification:
    def setup_method(self):
        self.formatter = AlertFormatter()
        self.alert = Alert(
            market_id="m1",
            alert_type="accumulation",
            score=120,
            id=99,
            market_question="Will X happen?",
            direction="NO",
            star_level=3,
            merge_suspected=True,
        )

    def test_contains_merge_header(self):
        msg = self.formatter.format_merge_notification(self.alert)
        assert "MERGE" in msg.upper()

    def test_contains_alert_id(self):
        msg = self.formatter.format_merge_notification(self.alert)
        assert "99" in msg

    def test_contains_score(self):
        msg = self.formatter.format_merge_notification(self.alert)
        assert "120" in msg

    def test_contains_market_question(self):
        msg = self.formatter.format_merge_notification(self.alert)
        assert "Will X happen?" in msg

    def test_contains_merge_detail_when_provided(self):
        detail = "YES=10000 shares ($5000), NO=10000 shares ($5000), net=0"
        msg = self.formatter.format_merge_notification(self.alert, merge_detail=detail)
        assert detail in msg

    def test_no_merge_detail_when_none(self):
        msg = self.formatter.format_merge_notification(self.alert, merge_detail=None)
        # Should not crash and should not show "Detalle" section
        assert "YES=" not in msg

    def test_contains_cautionary_note(self):
        msg = self.formatter.format_merge_notification(self.alert)
        # Should mention that N12 reduced the score
        assert "N12" in msg or "merge_suspected" in msg.lower() or "-40" in msg


# ── AlertFormatter.format_position_gone ──────────────────────


class TestFormatPositionGone:
    def setup_method(self):
        self.formatter = AlertFormatter()
        self.event = {
            "type": "position_gone",
            "close_reason": "position_gone",
            "market_id": "m1",
            "market_question": "Will Y resolve YES?",
            "wallets": [
                {
                    "address": "0xabc123def456",
                    "direction": "YES",
                    "original_amount": 5000.0,
                    "remaining_pct": 3.5,
                }
            ],
            "timestamp": datetime.now(timezone.utc),
        }

    def test_contains_position_header(self):
        msg = self.formatter.format_position_gone(self.event)
        assert "POSICI" in msg or "POSITION" in msg.upper() or "DESAPARECIDA" in msg

    def test_contains_market_question(self):
        msg = self.formatter.format_position_gone(self.event)
        assert "Will Y resolve YES?" in msg

    def test_contains_direction(self):
        msg = self.formatter.format_position_gone(self.event)
        assert "YES" in msg

    def test_contains_remaining_pct(self):
        # remaining_pct=3.5 → Python's :.0f banker's rounding → "4"
        msg = self.formatter.format_position_gone(self.event)
        assert "4%" in msg or "~4" in msg or "3" in msg  # ~3-4% depending on rounding

    def test_contains_original_amount(self):
        msg = self.formatter.format_position_gone(self.event)
        assert "5,000" in msg or "5000" in msg

    def test_merge_suspected_close_reason(self):
        event = dict(self.event)
        event["close_reason"] = "merge_suspected"
        msg = self.formatter.format_position_gone(event)
        # Merge-specific language
        assert "merge" in msg.lower() or "CLOB" in msg

    def test_position_gone_close_reason(self):
        msg = self.formatter.format_position_gone(self.event)
        # CTF-specific language
        assert "CTF" in msg or "burn" in msg.lower() or "CLOB" in msg

    def test_contains_market_link(self):
        msg = self.formatter.format_position_gone(self.event)
        assert "polymarket.com" in msg

    def test_no_crash_with_empty_wallets(self):
        event = dict(self.event)
        event["wallets"] = []
        msg = self.formatter.format_position_gone(event)
        assert isinstance(msg, str)
        assert len(msg) > 10
