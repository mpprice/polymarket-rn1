"""Configuration loaded from .env file."""
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # Polymarket
    private_key: str = field(default_factory=lambda: os.getenv("POLYMARKET_PRIVATE_KEY", ""))
    api_key: str = field(default_factory=lambda: os.getenv("POLYMARKET_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("POLYMARKET_API_SECRET", ""))
    api_passphrase: str = field(default_factory=lambda: os.getenv("POLYMARKET_API_PASSPHRASE", ""))
    chain_id: int = 137
    clob_url: str = "https://clob.polymarket.com"
    gamma_url: str = "https://gamma-api.polymarket.com"
    data_url: str = "https://data-api.polymarket.com"

    # Odds data
    odds_api_key: str = field(default_factory=lambda: os.getenv("ODDS_API_KEY", ""))

    # Risk limits — calibrated for $500 test wallet
    bankroll_usdc: float = float(os.getenv("BANKROLL_USDC", "500"))
    max_position_usdc: float = float(os.getenv("MAX_POSITION_USDC", "25"))  # 5% of bankroll
    max_total_exposure_usdc: float = float(os.getenv("MAX_TOTAL_EXPOSURE_USDC", "300"))  # 60% of bankroll
    min_edge_pct: float = float(os.getenv("MIN_EDGE_PCT", "3.0"))
    max_edge_pct: float = float(os.getenv("MAX_EDGE_PCT", "25.0"))  # lowered from 200; >25% edges are matching errors
    kelly_fraction: float = float(os.getenv("KELLY_FRACTION", "0.25"))  # quarter Kelly

    # Entry price range (RN1's profitable range: 5-40c)
    min_entry_price: float = float(os.getenv("MIN_ENTRY_PRICE", "0.03"))
    max_entry_price: float = float(os.getenv("MAX_ENTRY_PRICE", "0.50"))

    # Scan interval
    scan_interval_seconds: int = int(os.getenv("SCAN_INTERVAL", "300"))

    # Learning agent
    learning_enabled: bool = os.getenv("LEARNING_ENABLED", "true").lower() == "true"
    min_learning_samples: int = int(os.getenv("MIN_LEARNING_SAMPLES", "20"))

    # Merge arbitrage
    merge_enabled: bool = os.getenv("MERGE_ENABLED", "true").lower() == "true"
    min_merge_profit: float = float(os.getenv("MIN_MERGE_PROFIT", "0.02"))  # $0.02/pair minimum

    # Sports categories to monitor (matching Polymarket slugs)
    # Extended from RN1 analysis — most profitable sports first
    target_sports: list = field(default_factory=lambda: [
        # Top tier (highest volume and P&L for RN1)
        "epl", "bun", "lal", "cs2", "ucl",
        # Second tier
        "sea", "fl1", "uel", "elc", "itsb",
        # Third tier
        "mex", "arg", "bl2", "por", "es2",
        # US sports
        "nba", "nfl",
        # Tennis
        "atp", "wta",
        # Others
        "scop", "bra", "mls", "tur",
    ])
