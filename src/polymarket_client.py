"""Polymarket CLOB client wrapper for market discovery, pricing, and order placement."""
import logging
import time
from typing import Optional

import requests
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, MarketOrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL

from .config import Config

log = logging.getLogger(__name__)


class PolymarketClient:
    """Unified client for Polymarket CLOB + Gamma + Data APIs."""

    def __init__(self, config: Config, dry_run: bool = True):
        self.config = config
        self.dry_run = dry_run
        self._clob: Optional[ClobClient] = None
        self._session = requests.Session()

    # ── Authentication ──────────────────────────────────────────────

    def connect(self):
        """Initialize CLOB client and authenticate."""
        self._clob = ClobClient(
            self.config.clob_url,
            key=self.config.private_key,
            chain_id=self.config.chain_id,
            signature_type=0,  # EOA wallet
        )

        if self.config.api_key:
            self._clob.set_api_creds(ApiCreds(
                api_key=self.config.api_key,
                api_secret=self.config.api_secret,
                api_passphrase=self.config.api_passphrase,
            ))
            log.info("Connected with existing API credentials")
        else:
            creds = self._clob.create_or_derive_api_creds()
            self._clob.set_api_creds(creds)
            log.info("Derived new API credentials: key=%s", creds.api_key)
            log.info("Save these to .env: API_KEY=%s API_SECRET=%s API_PASSPHRASE=%s",
                     creds.api_key, creds.api_secret, creds.api_passphrase)
        return self

    # ── Market Discovery (Gamma API) ────────────────────────────────

    def get_active_sports_markets(self, limit: int = 200) -> list[dict]:
        """Fetch active sports markets from Gamma API using sport tag IDs."""
        # First fetch sport tag mappings
        tag_map = self._get_sport_tags()
        markets = []
        for sport in self.config.target_sports:
            tag_id = tag_map.get(sport)
            if not tag_id:
                log.debug("No tag mapping for sport: %s", sport)
                continue
            url = f"{self.config.gamma_url}/events"
            params = {
                "active": "true",
                "closed": "false",
                "limit": limit,
                "tag_id": tag_id,
            }
            try:
                resp = self._session.get(url, params=params, timeout=10)
                resp.raise_for_status()
                events = resp.json()
                for event in events:
                    slug = event.get("slug", "")
                    # Only match-level events (contain date pattern)
                    if not slug.startswith(sport + "-"):
                        continue
                    for m in event.get("markets", [event]):
                        markets.append(self._parse_market(m, sport))
            except Exception as e:
                log.warning("Failed to fetch %s markets: %s", sport, e)
        log.info("Found %d active sports markets", len(markets))
        return markets

    def _get_sport_tags(self) -> dict[str, str]:
        """Fetch sport-to-tag_id mapping from /sports endpoint."""
        try:
            resp = self._session.get(f"{self.config.gamma_url}/sports", timeout=10)
            resp.raise_for_status()
            sports = resp.json()
            # Each sport has a 'tags' field like "1,82,306,100639"
            # Use the second tag as the sport-specific one (first is generic "Sports")
            tag_map = {}
            for s in sports:
                sport_key = s.get("sport", "")
                tags = s.get("tags", "").split(",")
                # Use the most specific tag (second one, or first non-"1" tag)
                for t in tags:
                    t = t.strip()
                    if t and t != "1":
                        tag_map[sport_key] = t
                        break
            return tag_map
        except Exception as e:
            log.warning("Failed to fetch sport tags: %s", e)
            return {}

    def get_market_by_condition(self, condition_id: str) -> Optional[dict]:
        """Fetch a single market by condition ID."""
        url = f"{self.config.gamma_url}/markets"
        resp = self._session.get(url, params={"condition_id": condition_id}, timeout=10)
        data = resp.json()
        if isinstance(data, list) and data:
            return data[0]
        return data if isinstance(data, dict) and data.get("id") else None

    def _parse_market(self, m: dict, sport: str) -> dict:
        """Normalize market data into a standard dict."""
        outcomes = m.get("outcomes", '["Yes","No"]')
        if isinstance(outcomes, str):
            import json
            outcomes = json.loads(outcomes)

        prices_raw = m.get("outcomePrices", '["0.5","0.5"]')
        if isinstance(prices_raw, str):
            import json
            prices_raw = json.loads(prices_raw)
        prices = [float(p) for p in prices_raw]

        token_ids_raw = m.get("clobTokenIds", "[]")
        if isinstance(token_ids_raw, str):
            import json
            token_ids_raw = json.loads(token_ids_raw)

        return {
            "condition_id": m.get("conditionId", ""),
            "question": m.get("question", ""),
            "slug": m.get("slug", ""),
            "sport": sport,
            "outcomes": outcomes,
            "prices": prices,
            "token_ids": token_ids_raw,
            "volume_24h": float(m.get("volume24hr", 0)),
            "liquidity": float(m.get("liquidity", 0)),
            "end_date": m.get("endDate", ""),
            "neg_risk": m.get("negRisk", False) or m.get("enableNegRisk", False),
            "active": m.get("active", True),
        }

    # ── Pricing ─────────────────────────────────────────────────────

    def get_orderbook(self, token_id: str) -> dict:
        """Get full orderbook for a token."""
        return self._clob.get_order_book(token_id)

    def get_midpoint(self, token_id: str) -> float:
        """Get midpoint price."""
        return float(self._clob.get_midpoint(token_id))

    def get_best_price(self, token_id: str, side: str = "BUY") -> float:
        """Get best available price for a side."""
        return float(self._clob.get_price(token_id, side))

    # ── Order Placement ─────────────────────────────────────────────

    def place_limit_order(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str = "BUY",
        neg_risk: bool = False,
    ) -> dict:
        """Place a GTC limit order (MAKER - no 3-second delay on sports).

        IMPORTANT: Sports markets have a 3-second delay on TAKER orders
        (anti-courtsiding). Limit orders that rest on the book are MAKER
        and execute without delay. Always use limit orders, not market orders.
        """
        if self.dry_run:
            log.info("[DRY RUN] Limit %s %.1f @ %.4f token=%s...", side, size, price, token_id[:20])
            return {"orderID": "dry-run", "status": "simulated"}

        tick_size = self._clob.get_tick_size(token_id)
        return self._clob.create_and_post_order(
            OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=BUY if side == "BUY" else SELL,
            ),
            options={"tick_size": str(tick_size), "neg_risk": neg_risk},
            order_type=OrderType.GTC,
        )

    def place_market_order(
        self,
        token_id: str,
        amount_usdc: float,
        side: str = "BUY",
    ) -> dict:
        """Place a FOK market order. amount_usdc = dollars to spend (BUY) or shares to sell (SELL)."""
        if self.dry_run:
            log.info("[DRY RUN] Market %s $%.2f token=%s...", side, amount_usdc, token_id[:20])
            return {"orderID": "dry-run", "status": "simulated"}

        return self._clob.create_and_post_order(
            MarketOrderArgs(
                token_id=token_id,
                amount=amount_usdc,
                side=BUY if side == "BUY" else SELL,
            ),
            order_type=OrderType.FOK,
        )

    def cancel_all_orders(self) -> dict:
        """Cancel all open orders."""
        if self.dry_run:
            log.info("[DRY RUN] Cancel all orders")
            return {}
        return self._clob.cancel_all()

    def get_open_orders(self) -> list:
        """Get all open orders."""
        return self._clob.get_open_orders()

    # ── Positions ───────────────────────────────────────────────────

    def merge_positions(self, condition_id: str, amount: int) -> dict:
        """Merge YES+NO token pairs back into USDC.

        Each YES+NO pair = $1.00 USDC. This is how RN1 realized profits
        without selling directly (synthetic sell via merge).
        """
        if self.dry_run:
            log.info("[DRY RUN] Merge %d pairs condition=%s...", amount, condition_id[:20])
            return {"status": "simulated", "amount": amount}

        return self._clob.merge(condition_id, amount)

    def get_tick_size_for_token(self, token_id: str) -> str:
        """Get tick size for a token."""
        return str(self._clob.get_tick_size(token_id))

    def get_positions(self, address: str) -> list[dict]:
        """Get current positions from data API."""
        url = f"{self.config.data_url}/positions"
        resp = self._session.get(url, params={"user": address, "sizeThreshold": 0}, timeout=15)
        return resp.json()
