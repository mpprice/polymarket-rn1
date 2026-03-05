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

    def get_active_sports_markets(self, limit: int = 100) -> list[dict]:
        """Fetch active sports markets from Gamma API."""
        markets = []
        for sport in self.config.target_sports:
            url = f"{self.config.gamma_url}/events"
            params = {
                "active": "true",
                "closed": "false",
                "limit": limit,
                "order": "volume_24hr",
            }
            try:
                resp = self._session.get(url, params=params, timeout=10)
                resp.raise_for_status()
                events = resp.json()
                for event in events:
                    slug = event.get("slug", "")
                    if slug.startswith(sport + "-"):
                        for m in event.get("markets", [event]):
                            markets.append(self._parse_market(m, sport))
            except Exception as e:
                log.warning("Failed to fetch %s markets: %s", sport, e)
        log.info("Found %d active sports markets", len(markets))
        return markets

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
        """Place a GTC limit order. Returns order response."""
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

    def get_positions(self, address: str) -> list[dict]:
        """Get current positions from data API."""
        url = f"{self.config.data_url}/positions"
        resp = self._session.get(url, params={"user": address, "sizeThreshold": 0}, timeout=15)
        return resp.json()
