"""
Wallet Tracker — Silent collection systems.

WR01: Win Rate Tracker
  - Updates wallet_categories with win/loss record after market resolution
  - Activates (becomes meaningful) at >= 5 resolved markets
  - Silent: no scoring impact during live alerts

SP01: Specialization Tracker
  - Tracks which market categories each wallet trades in
  - Updates wallet_categories.specialty_tags when >= 3 markets in same category
  - Silent: no scoring impact during live alerts
"""

import logging
from datetime import datetime, timezone

from src.database.models import WalletCategory

logger = logging.getLogger(__name__)

# Minimum resolved markets before win rate is considered meaningful
WR01_MIN_RESOLVED: int = 5

# Minimum markets in same category to tag as specialist
SP01_MIN_CATEGORY_MARKETS: int = 3


class WalletTracker:
    """Silent tracking systems for wallet performance and specialization."""

    def __init__(self, db_client=None) -> None:
        self.db = db_client

    # ── WR01 — Win Rate Tracker ───────────────────────────

    def update_win_rate(
        self,
        wallet_address: str,
        is_correct: bool,
        total_amount: float = 0.0,
    ) -> None:
        """Update a wallet's win rate after a market resolution.

        Args:
            wallet_address: The wallet whose alert was resolved.
            is_correct: Whether the alert's direction matched the outcome.
            total_amount: Amount tracked in this resolved market.
        """
        if self.db is None:
            return

        try:
            existing = self.db.get_wallet_category(wallet_address)
        except Exception as e:
            logger.debug("get_wallet_category failed for %s: %s", wallet_address, e)
            existing = None

        if existing:
            resolved = existing.get("markets_resolved", 0) + 1
            won = existing.get("markets_won", 0) + (1 if is_correct else 0)
            tracked = existing.get("total_tracked", 0.0) + total_amount
            win_rate = won / resolved if resolved > 0 else None
            category = existing.get("category", "unknown")

            # Auto-categorize based on win rate when enough data
            if resolved >= WR01_MIN_RESOLVED:
                if win_rate is not None and win_rate >= 0.65:
                    category = "smart_money"
                elif win_rate is not None and win_rate < 0.35:
                    category = "degen"
        else:
            resolved = 1
            won = 1 if is_correct else 0
            tracked = total_amount
            win_rate = float(won) / resolved
            category = "unknown"

        cat = WalletCategory(
            wallet_address=wallet_address,
            category=category,
            win_rate=win_rate,
            markets_resolved=resolved,
            markets_won=won,
            total_tracked=tracked,
            specialty_tags=existing.get("specialty_tags") if existing else None,
            updated_at=datetime.now(timezone.utc),
        )

        try:
            self.db.upsert_wallet_category(cat)
            logger.debug(
                "WR01: %s resolved=%d won=%d rate=%.2f",
                wallet_address[:10], resolved, won, win_rate or 0,
            )
        except Exception as e:
            logger.error("upsert_wallet_category failed for %s: %s", wallet_address, e)

    # ── SP01 — Specialization Tracker ─────────────────────

    def update_specialization(
        self,
        wallet_address: str,
        market_category: str | None,
    ) -> None:
        """Track which categories a wallet trades in.

        Tags the wallet as a specialist when >= 3 markets in the same category.

        Args:
            wallet_address: The wallet to track.
            market_category: Category of the market (e.g., "Politics").
        """
        if self.db is None or not market_category:
            return

        try:
            existing = self.db.get_wallet_category(wallet_address)
        except Exception as e:
            logger.debug("get_wallet_category failed for %s: %s", wallet_address, e)
            existing = None

        tags: list[str] = []
        if existing and existing.get("specialty_tags"):
            tags = list(existing["specialty_tags"])

        # Add the category as a tag (track occurrences by keeping duplicates)
        normalized = market_category.lower()
        tags.append(normalized)

        # Check if any category has >= SP01_MIN_CATEGORY_MARKETS
        from collections import Counter
        counts = Counter(tags)
        specialty_label = None
        for cat, count in counts.most_common(1):
            if count >= SP01_MIN_CATEGORY_MARKETS:
                specialty_label = cat

        cat = WalletCategory(
            wallet_address=wallet_address,
            category=existing.get("category", "unknown") if existing else "unknown",
            win_rate=existing.get("win_rate") if existing else None,
            markets_resolved=existing.get("markets_resolved", 0) if existing else 0,
            markets_won=existing.get("markets_won", 0) if existing else 0,
            specialty_tags=tags,
            total_tracked=existing.get("total_tracked", 0.0) if existing else 0.0,
            updated_at=datetime.now(timezone.utc),
        )

        try:
            self.db.upsert_wallet_category(cat)
            if specialty_label:
                logger.debug(
                    "SP01: %s specialist in '%s' (%d markets)",
                    wallet_address[:10], specialty_label, counts[specialty_label],
                )
        except Exception as e:
            logger.error("upsert_wallet_category failed for %s: %s", wallet_address, e)
