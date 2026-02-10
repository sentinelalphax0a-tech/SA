"""
Arbitrage Filter — N03 & N04.

Detects hedging/arbitrage across equivalent markets:
  N03: Wallet has YES on one market and NO on the equivalent → -100 (kill alert)
  N04: Positions in opposite markets, same direction → 0 pts (flag only)

Detection strategy:
  1. Use markets.opposite_market if set in DB.
  2. Fallback: basic name-similarity matching across known markets.
"""

import logging
import re

from src import config
from src.database.models import TradeEvent, FilterResult

logger = logging.getLogger(__name__)

# Words stripped before similarity comparison (too common to be meaningful)
_STOP_WORDS = frozenset({
    "will", "the", "a", "an", "in", "on", "at", "to", "of", "by",
    "be", "is", "it", "for", "or", "and", "this", "that", "with",
    "from", "as", "are", "was", "were", "been", "being", "have",
    "has", "had", "do", "does", "did", "but", "if", "than", "so",
    "before", "after",
})

# Minimum Jaccard similarity to consider two market questions related
_SIMILARITY_THRESHOLD = 0.60


def _fr(filt: dict, details: str | None = None) -> FilterResult:
    """Build a FilterResult from a config filter dict."""
    return FilterResult(
        filter_id=filt["id"],
        filter_name=filt["name"],
        points=filt["points"],
        category=filt["category"],
        details=details,
    )


class ArbitrageFilter:
    """Detects arbitrage activity across equivalent markets."""

    def __init__(self, db_client=None) -> None:
        self.db = db_client
        self._market_cache: dict[str, dict] = {}
        self._all_markets: list[dict] | None = None

    # ── Main entry point ─────────────────────────────────────

    def check(
        self,
        wallet_address: str,
        market_id: str,
        direction: str,
        all_wallet_trades: list[TradeEvent],
    ) -> list[FilterResult]:
        """Check for arbitrage (N03) and opposite-market positions (N04).

        Args:
            wallet_address: Wallet being analyzed.
            market_id: Current market being scored.
            direction: Wallet's direction in current market ("YES"/"NO").
            all_wallet_trades: All trades by this wallet across ALL markets.

        Returns:
            [N03] if arbitrage detected (-100, kills alert).
            [N04] if positions exist in opposite market but same direction (flag).
            [] if no opposite market found or no overlap.
        """
        # Find the opposite/equivalent market
        opposite_id = self._find_opposite_market(market_id)
        if not opposite_id:
            return []

        # Check if wallet has trades in the opposite market
        opposite_trades = [
            t for t in all_wallet_trades
            if t.market_id == opposite_id and t.wallet_address == wallet_address
        ]
        if not opposite_trades:
            return []

        opp_direction = _dominant_direction(opposite_trades)

        # N03: opposite directions = hedging = arbitrage
        if direction != opp_direction:
            return [_fr(
                config.FILTER_N03,
                f"{direction}@{market_id[:12]}… + {opp_direction}@{opposite_id[:12]}…",
            )]

        # N04: same direction on both markets — not arbitrage but notable
        return [_fr(
            config.FILTER_N04,
            f"{direction} on both {market_id[:12]}… and {opposite_id[:12]}…",
        )]

    # ── Opposite market detection ────────────────────────────

    def _find_opposite_market(self, market_id: str) -> str | None:
        """Find the opposite/equivalent market for a given market.

        Strategy:
          1. DB field ``markets.opposite_market`` (manual mapping).
          2. Fallback: name-similarity scan across all known markets.
        """
        # 1. Explicit mapping from DB
        market = self._get_market(market_id)
        if market and market.get("opposite_market"):
            return market["opposite_market"]

        # 2. Name-similarity fallback
        return self._find_opposite_by_similarity(market_id, market)

    def _find_opposite_by_similarity(
        self, market_id: str, market: dict | None = None,
    ) -> str | None:
        """Find a likely opposite market by comparing question text."""
        if market is None:
            market = self._get_market(market_id)
        if not market or not market.get("question"):
            return None

        all_markets = self._get_all_markets()
        if not all_markets:
            return None

        question = market["question"]
        tokens_a = _tokenize(question)
        if not tokens_a:
            return None

        best_id: str | None = None
        best_score: float = 0.0

        for m in all_markets:
            other_id = m.get("market_id")
            if other_id == market_id:
                continue
            other_q = m.get("question")
            if not other_q:
                continue

            tokens_b = _tokenize(other_q)
            score = _jaccard(tokens_a, tokens_b)

            if score > best_score and score >= _SIMILARITY_THRESHOLD:
                best_score = score
                best_id = other_id

        if best_id:
            logger.debug(
                "similarity match for %s: %s (score=%.2f)",
                market_id[:12], best_id[:12], best_score,
            )
        return best_id

    # ── DB helpers ───────────────────────────────────────────

    def _get_market(self, market_id: str) -> dict | None:
        """Fetch a single market, with cache."""
        if market_id in self._market_cache:
            return self._market_cache[market_id]
        if self.db is None:
            return None
        try:
            data = self.db.get_market(market_id)
            if data:
                self._market_cache[market_id] = data
            return data
        except Exception as e:
            logger.debug("get_market failed for %s: %s", market_id, e)
            return None

    def _get_all_markets(self) -> list[dict]:
        """Fetch all markets (cached after first call)."""
        if self._all_markets is not None:
            return self._all_markets
        if self.db is None:
            return []
        try:
            self._all_markets = self.db.get_all_markets()
            # Warm the single-market cache too
            for m in self._all_markets:
                mid = m.get("market_id")
                if mid:
                    self._market_cache[mid] = m
            return self._all_markets
        except Exception as e:
            logger.debug("get_all_markets failed: %s", e)
            return []


# ── Module-level helpers ─────────────────────────────────────


def _dominant_direction(trades: list[TradeEvent]) -> str:
    """Return the dominant direction by total amount traded."""
    yes_total = sum(t.amount for t in trades if t.direction == "YES")
    no_total = sum(t.amount for t in trades if t.direction == "NO")
    return "YES" if yes_total >= no_total else "NO"


def _tokenize(question: str) -> set[str]:
    """Extract meaningful lowercase tokens from a market question."""
    words = re.findall(r"[a-zA-Z0-9]+", question.lower())
    return {w for w in words if w not in _STOP_WORDS and len(w) > 1}


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity between two token sets."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
