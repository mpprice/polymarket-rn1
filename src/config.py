"""Configuration loaded from .env file."""
import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv


def load_config_env(env_file: str = None):
    """Load .env file. If env_file specified, load that instead of default."""
    if env_file:
        load_dotenv(env_file, override=True)
    else:
        load_dotenv()


load_config_env(os.getenv("ENV_FILE"))


def _env_float(key: str, default: str) -> float:
    return float(os.getenv(key, default))


def _env_int(key: str, default: str) -> int:
    return int(os.getenv(key, default))


def _env_bool(key: str, default: str) -> bool:
    return os.getenv(key, default).lower() == "true"


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
    # Priority: maximise trade count, always leave headroom for new high-edge trades
    bankroll_usdc: float = field(default_factory=lambda: _env_float("BANKROLL_USDC", "500"))
    max_position_usdc: float = field(default_factory=lambda: _env_float("MAX_POSITION_USDC", "8"))
    max_total_exposure_usdc: float = field(default_factory=lambda: _env_float("MAX_TOTAL_EXPOSURE_USDC", "200"))
    min_edge_pct: float = field(default_factory=lambda: _env_float("MIN_EDGE_PCT", "2.5"))
    max_edge_pct: float = field(default_factory=lambda: _env_float("MAX_EDGE_PCT", "25.0"))
    kelly_fraction: float = field(default_factory=lambda: _env_float("KELLY_FRACTION", "0.15"))

    # Entry price range (RN1's profitable range: 5-40c)
    min_entry_price: float = field(default_factory=lambda: _env_float("MIN_ENTRY_PRICE", "0.03"))
    max_entry_price: float = field(default_factory=lambda: _env_float("MAX_ENTRY_PRICE", "0.50"))

    # Data directory (paper vs live isolation)
    data_dir: str = field(default_factory=lambda: os.getenv("DATA_DIR", "data"))

    # Scan interval
    scan_interval_seconds: int = field(default_factory=lambda: _env_int("SCAN_INTERVAL", "300"))

    # Learning agent
    learning_enabled: bool = field(default_factory=lambda: _env_bool("LEARNING_ENABLED", "true"))
    min_learning_samples: int = field(default_factory=lambda: _env_int("MIN_LEARNING_SAMPLES", "20"))

    # Merge arbitrage
    merge_enabled: bool = field(default_factory=lambda: _env_bool("MERGE_ENABLED", "true"))
    min_merge_profit: float = field(default_factory=lambda: _env_float("MIN_MERGE_PROFIT", "0.02"))

    # Sports categories to monitor (matching Polymarket slugs)
    # Extended from RN1 analysis — most profitable sports first
    target_sports: list = field(default_factory=lambda: [
        # Top tier (highest volume and P&L for RN1)
        "epl", "bun", "lal", "ucl",
        # Second tier
        "sea", "fl1", "uel", "elc", "itsb",
        # Third tier
        "mex", "arg", "bl2", "por", "es2",
        # US sports
        "nba", "nfl", "nhl", "cbb", "cfb",
        # Tennis
        "atp", "wta",
        # Others
        "scop", "bra", "mls", "tur", "ere",
        # Not available on The Odds API: cs2, lol, dota2, val, codmw (esports)
    ])
