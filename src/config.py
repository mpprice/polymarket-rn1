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

    # Risk limits
    max_position_usdc: float = float(os.getenv("MAX_POSITION_USDC", "500"))
    max_total_exposure_usdc: float = float(os.getenv("MAX_TOTAL_EXPOSURE_USDC", "5000"))
    min_edge_pct: float = float(os.getenv("MIN_EDGE_PCT", "3.0"))

    # Sports categories to monitor (matching Polymarket slugs)
    target_sports: list = field(default_factory=lambda: [
        "epl", "bun", "sea", "fl1", "lal",  # Football leagues
        "ucl", "uel",                         # European cups
        "nba", "nfl",                         # US sports
        "cs2",                                # Esports
        "atp", "wta",                         # Tennis
    ])
