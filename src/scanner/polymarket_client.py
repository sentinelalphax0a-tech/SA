"""
Polymarket CLOB API client.

Fetches active markets, recent trades, and order book data.

APIs used:
- Gamma API (gamma-api.polymarket.com) — market discovery, metadata, odds
- CLOB API  (clob.polymarket.com)      — orderbook, midpoint pricing
- Data API  (data-api.polymarket.com)   — public trade history
"""

import json
import logging
import time
from datetime import datetime, timedelta, timezone

import requests

from src import config
from src.database.models import Market, TradeEvent

logger = logging.getLogger(__name__)

# Tags that map to our categories (lowercased for matching)
CATEGORY_TAGS: dict[str, set[str]] = {
    "politics": {"politics", "elections", "trump", "trump presidency", "congress",
                 "biden", "democrat", "republican", "senate", "house"},
    "economics": {"economics", "fed", "fed rates", "inflation", "crypto",
                  "bitcoin", "stock market", "recession", "gdp", "interest rates"},
    "geopolitics": {"geopolitics", "world", "middle east", "china", "russia",
                    "ukraine", "iran", "nato", "war", "sanctions"},
}

DATA_API_BASE = "https://data-api.polymarket.com"

# Rate-limit delays (seconds)
GAMMA_DELAY = 0.1
CLOB_DELAY = 0.05


class PolymarketClient:
    """Client for Polymarket CLOB and Gamma APIs."""

    def __init__(self) -> None:
        self.clob_base = config.POLYMARKET_CLOB_BASE_URL
        self.gamma_base = config.POLYMARKET_GAMMA_BASE_URL
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        # Cache raw market data keyed by conditionId to avoid re-fetching
        self._market_cache: dict[str, dict] = {}

    # ── Markets (Gamma API) ─────────────────────────────────

    def get_active_markets(
        self, categories: list[str] | None = None
    ) -> list[Market]:
        """Fetch active markets filtered by relevant categories and odds range.

        Uses the Gamma /events endpoint to get markets grouped by event,
        then filters by tag-based categories and odds range.
        """
        if categories is None:
            categories = config.MARKET_CATEGORIES

        target_tags: set[str] = set()
        for cat in categories:
            target_tags.update(CATEGORY_TAGS.get(cat, {cat.lower()}))

        all_markets: list[Market] = []
        offset = 0
        limit = 50
        max_pages = 10

        for _ in range(max_pages):
            try:
                resp = self.session.get(
                    f"{self.gamma_base}/events",
                    params={
                        "active": "true",
                        "closed": "false",
                        "limit": limit,
                        "offset": offset,
                        "order": "volume24hr",
                        "ascending": "false",
                    },
                    timeout=10,
                )
                resp.raise_for_status()
                events = resp.json()
            except Exception as e:
                logger.error("Gamma /events fetch failed (offset=%d): %s", offset, e)
                break

            if not events:
                break

            for event in events:
                # Check tags match our categories
                event_tags = {
                    t.get("label", "").lower()
                    for t in event.get("tags", [])
                }
                if not event_tags & target_tags:
                    continue

                # Extract markets from the event
                for mkt in event.get("markets", []):
                    # Cache raw data for later use by odds/orderbook methods
                    cid = mkt.get("conditionId")
                    if cid:
                        self._market_cache[cid] = mkt

                    market = self._parse_gamma_market(mkt)
                    if market is None:
                        continue

                    # Filter by odds range
                    odds = market.current_odds
                    if odds is not None and config.ODDS_MIN <= odds <= config.ODDS_MAX:
                        all_markets.append(market)

            offset += limit
            time.sleep(GAMMA_DELAY)

        logger.info("Fetched %d active markets in target categories", len(all_markets))
        return all_markets

    def get_market_info(self, market_id: str) -> Market | None:
        """Fetch detailed info for a single market via Gamma."""
        try:
            resp = self.session.get(
                f"{self.gamma_base}/markets",
                params={"conditionId": market_id, "limit": 1},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            if data:
                return self._parse_gamma_market(data[0])
        except Exception as e:
            logger.error("get_market_info failed for %s: %s", market_id, e)
        return None

    def _parse_gamma_market(self, raw: dict) -> Market | None:
        """Parse a Gamma API market dict into a Market model."""
        condition_id = raw.get("conditionId")
        question = raw.get("question")
        if not condition_id or not question:
            return None

        # Parse outcomePrices — can be a JSON string or list
        outcome_prices = raw.get("outcomePrices", "[]")
        if isinstance(outcome_prices, str):
            try:
                outcome_prices = json.loads(outcome_prices)
            except (json.JSONDecodeError, TypeError):
                outcome_prices = []

        yes_price = None
        if outcome_prices:
            try:
                yes_price = float(outcome_prices[0])
            except (ValueError, IndexError):
                pass

        # Parse clobTokenIds
        clob_tokens = raw.get("clobTokenIds", "[]")
        if isinstance(clob_tokens, str):
            try:
                clob_tokens = json.loads(clob_tokens)
            except (json.JSONDecodeError, TypeError):
                clob_tokens = []

        # Volume fields
        vol_24h = raw.get("volume24hr") or raw.get("volume24hrClob") or 0.0
        vol_7d = raw.get("volume1wk") or raw.get("volume1wkClob") or 0.0
        # Approximate 7d daily average
        vol_7d_avg = float(vol_7d) / 7.0 if vol_7d else 0.0

        # Resolution date
        end_date = None
        end_str = raw.get("endDate")
        if end_str:
            try:
                end_date = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            except ValueError:
                pass

        return Market(
            market_id=condition_id,
            question=question,
            slug=raw.get("slug"),
            category=raw.get("category"),
            current_odds=yes_price,
            volume_24h=float(vol_24h),
            volume_7d_avg=vol_7d_avg,
            liquidity=float(raw.get("liquidityNum") or 0.0),
            resolution_date=end_date,
            is_resolved=bool(raw.get("closed", False)),
            outcome=None,
            is_active=bool(raw.get("active", True)) and not bool(raw.get("closed", False)),
            opposite_market=raw.get("negRiskMarketID"),
        )

    # ── Trades (Data API) ──────────────────────────────────

    def get_recent_trades(
        self,
        market_id: str,
        minutes: int = 35,
        min_amount: float = 100.0,
    ) -> list[TradeEvent]:
        """Fetch recent trades for a market from the Data API.

        Args:
            market_id: The conditionId of the market.
            minutes: Look-back window in minutes.
            min_amount: Minimum notional value (size * price) to include.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        cutoff_ts = int(cutoff.timestamp())

        all_trades: list[TradeEvent] = []
        offset = 0
        limit = 500
        max_pages = 5

        for _ in range(max_pages):
            try:
                resp = self.session.get(
                    f"{DATA_API_BASE}/trades",
                    params={
                        "market": market_id,
                        "limit": limit,
                        "offset": offset,
                    },
                    timeout=10,
                )
                resp.raise_for_status()
                raw_trades = resp.json()
            except Exception as e:
                logger.error("Data API /trades failed for %s: %s", market_id, e)
                break

            if not raw_trades:
                break

            page_had_old = False
            for t in raw_trades:
                ts = t.get("timestamp", 0)
                if isinstance(ts, str):
                    ts = int(ts)

                if ts < cutoff_ts:
                    page_had_old = True
                    continue

                size = float(t.get("size", 0))
                price = float(t.get("price", 0))
                notional = size * price

                if notional < min_amount:
                    continue

                trade_dt = datetime.fromtimestamp(ts, tz=timezone.utc)

                # Determine direction from outcome field
                outcome = t.get("outcome", "")
                direction = "YES" if outcome == "Yes" else "NO"

                trade = TradeEvent(
                    wallet_address=t.get("proxyWallet", ""),
                    market_id=market_id,
                    direction=direction,
                    amount=notional,
                    price=price,
                    timestamp=trade_dt,
                    is_market_order=t.get("side", "") == "BUY",
                    tx_hash=t.get("transactionHash"),
                )
                all_trades.append(trade)

            # If all trades on this page are older than cutoff, stop
            if page_had_old or len(raw_trades) < limit:
                break

            offset += limit
            time.sleep(CLOB_DELAY)

        return all_trades

    # ── Odds (CLOB API) ────────────────────────────────────

    def get_market_odds(self, market_id: str) -> dict | None:
        """Get current YES/NO odds for a market.

        Tries CLOB /midpoint first, falls back to Gamma outcomePrices.

        Returns:
            {"yes": float, "no": float} or None on failure.
        """
        market_info = self._get_market_raw(market_id)
        if not market_info:
            return None

        # Try CLOB midpoint first
        clob_tokens = market_info.get("clobTokenIds", "[]")
        if isinstance(clob_tokens, str):
            try:
                clob_tokens = json.loads(clob_tokens)
            except (json.JSONDecodeError, TypeError):
                clob_tokens = []

        result = {}
        if clob_tokens:
            yes_token = clob_tokens[0] if len(clob_tokens) > 0 else None
            no_token = clob_tokens[1] if len(clob_tokens) > 1 else None

            for label, token_id in [("yes", yes_token), ("no", no_token)]:
                if not token_id:
                    continue
                try:
                    resp = self.session.get(
                        f"{self.clob_base}/midpoint",
                        params={"token_id": token_id},
                        timeout=10,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    result[label] = float(data.get("mid", 0))
                    time.sleep(CLOB_DELAY)
                except Exception:
                    pass  # Fall through to Gamma fallback

        # Fallback: use Gamma outcomePrices
        if not result:
            outcome_prices = market_info.get("outcomePrices", "[]")
            if isinstance(outcome_prices, str):
                try:
                    outcome_prices = json.loads(outcome_prices)
                except (json.JSONDecodeError, TypeError):
                    outcome_prices = []
            if len(outcome_prices) >= 2:
                try:
                    result = {
                        "yes": float(outcome_prices[0]),
                        "no": float(outcome_prices[1]),
                    }
                except (ValueError, TypeError):
                    pass

        return result if result else None

    # ── Orderbook (CLOB API) ────────────────────────────────

    def get_market_orderbook(self, market_id: str) -> dict | None:
        """Get the orderbook for a market's YES token.

        Returns:
            {"bids": [...], "asks": [...]} or None on failure.
        """
        market_info = self._get_market_raw(market_id)
        if not market_info:
            return None

        clob_tokens = market_info.get("clobTokenIds", "[]")
        if isinstance(clob_tokens, str):
            try:
                clob_tokens = json.loads(clob_tokens)
            except (json.JSONDecodeError, TypeError):
                return None

        if not clob_tokens:
            return None

        # Try each token until one works (negRisk markets may 404 on some)
        for token_id in clob_tokens:
            try:
                resp = self.session.get(
                    f"{self.clob_base}/book",
                    params={"token_id": token_id},
                    timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "bids": data.get("bids", []),
                    "asks": data.get("asks", []),
                    "market": data.get("market"),
                    "asset_id": data.get("asset_id"),
                }
            except Exception:
                continue

        logger.warning("CLOB /book: no valid token found for %s", market_id)
        return None

    # ── Odds History (Gamma API) ────────────────────────────

    def get_market_odds_history(
        self, market_id: str, hours: int = 48
    ) -> list[dict]:
        """Fetch price/odds history for a market.

        Uses Gamma /markets endpoint to get current snapshot.
        Full timeseries would require CLOB websocket or timeseries endpoint.
        Returns a list of {"timestamp": datetime, "price": float} dicts.
        """
        # Gamma doesn't expose a dedicated timeseries endpoint publicly.
        # For now, return the current price as a single data point.
        # A full implementation would use CLOB websockets or a third-party
        # data source for historical prices.
        market = self.get_market_info(market_id)
        if market and market.current_odds is not None:
            return [
                {
                    "timestamp": datetime.now(timezone.utc),
                    "price": market.current_odds,
                }
            ]
        return []

    # ── Internal helpers ────────────────────────────────────

    def _get_market_raw(self, market_id: str) -> dict | None:
        """Get raw market data, using cache first, then Gamma API."""
        # Check cache first (populated by get_active_markets)
        if market_id in self._market_cache:
            return self._market_cache[market_id]

        # Fallback: fetch from Gamma
        try:
            resp = self.session.get(
                f"{self.gamma_base}/markets",
                params={"id": market_id, "limit": 10},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            # Find exact conditionId match
            for mkt in data:
                if mkt.get("conditionId") == market_id:
                    self._market_cache[market_id] = mkt
                    return mkt
            # If no exact match, try first result
            if data:
                self._market_cache[market_id] = data[0]
                return data[0]
        except Exception as e:
            logger.error("_get_market_raw failed for %s: %s", market_id, e)
        return None
