"""
Arbitrage Filter — N03.

Detects YES + NO positions on equivalent markets, which indicates
arbitrage rather than informed betting.
Score: -100 (effectively kills the alert).
"""

import logging

from src import config
from src.database.models import FilterResult

logger = logging.getLogger(__name__)


class ArbitrageFilter:
    """Detects arbitrage activity across equivalent markets."""

    def check(
        self,
        wallet_address: str,
        market_id: str,
        direction: str,
        opposite_market_id: str | None = None,
    ) -> list[FilterResult]:
        """
        N03 — Check if wallet holds opposite position on an equivalent market.

        Returns [FILTER_N03] if arbitrage detected, empty list otherwise.
        """
        if not opposite_market_id:
            return []
        # TODO: Query wallet positions on opposite market
        raise NotImplementedError
