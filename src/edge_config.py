"""Configuration for the edge estimation model.

Sport-specific overround removal methods, confidence weights, Kelly parameters,
and edge decay curves. All values are sensible defaults backed by sports betting
literature and can be overridden at runtime.

References
----------
- Shin, H.S. (1991). "Optimal Betting Odds Against Insider Traders."
  *Economic Journal*, 101(408), 1179-1185.
- Clarke, S., Krase, S., Statman, M. (2017). "Removing the Favourite-Longshot
  Bias from Bookmaker Odds." *Journal of Gambling Studies*.
- Cheung, K. (2015). "A Comparison of Methods for Removing the Margin from
  Bookmaker Odds."
- Thorp, E.O. (2006). "The Kelly Criterion in Blackjack, Sports Betting, and
  the Stock Market." *Handbook of Asset and Liability Management*.
"""
from dataclasses import dataclass, field


# ── Overround Removal Methods ────────────────────────────────────────────────

class OverroundMethod:
    """Enum-like constants for overround removal methods."""
    PROPORTIONAL = "proportional"
    SHIN = "shin"
    POWER = "power"
    ODDS_RATIO = "odds_ratio"
    MWPO = "mwpo"  # Margin Weights Proportional to Odds


# Default overround method by sport category
# Shin's model is superior for soccer (accounts for favourite-longshot bias).
# Proportional / MWPO is adequate for US sports (tighter markets, less FLB).
SPORT_OVERROUND_DEFAULTS: dict[str, str] = {
    # Soccer — Shin's model (favourite-longshot bias is significant in 3-way)
    "epl": OverroundMethod.SHIN,
    "bun": OverroundMethod.SHIN,
    "lal": OverroundMethod.SHIN,
    "sea": OverroundMethod.SHIN,
    "fl1": OverroundMethod.SHIN,
    "ucl": OverroundMethod.SHIN,
    "uel": OverroundMethod.SHIN,
    "elc": OverroundMethod.SHIN,
    "itsb": OverroundMethod.SHIN,
    "bl2": OverroundMethod.SHIN,
    "por": OverroundMethod.SHIN,
    "es2": OverroundMethod.SHIN,
    "ere": OverroundMethod.SHIN,
    "scop": OverroundMethod.SHIN,
    "mex": OverroundMethod.SHIN,
    "arg": OverroundMethod.SHIN,
    "bra": OverroundMethod.SHIN,
    "mls": OverroundMethod.SHIN,
    "tur": OverroundMethod.SHIN,
    "col": OverroundMethod.SHIN,
    "spl": OverroundMethod.SHIN,
    # Tier 1 expansion — all soccer, use Shin's
    "aus": OverroundMethod.SHIN,
    "efa": OverroundMethod.SHIN,
    "den": OverroundMethod.SHIN,
    "fr2": OverroundMethod.SHIN,
    "cdr": OverroundMethod.SHIN,
    "uef": OverroundMethod.SHIN,
    # Tier 2 expansion
    "bel": OverroundMethod.SHIN,
    "aut": OverroundMethod.SHIN,
    "gre": OverroundMethod.SHIN,
    "nor": OverroundMethod.SHIN,
    "swe": OverroundMethod.SHIN,
    "swi": OverroundMethod.SHIN,
    "pol": OverroundMethod.SHIN,
    "jap": OverroundMethod.SHIN,
    "ja2": OverroundMethod.SHIN,
    "kor": OverroundMethod.SHIN,
    "dfb": OverroundMethod.SHIN,
    "efl": OverroundMethod.SHIN,
    "el1": OverroundMethod.SHIN,
    "el2": OverroundMethod.SHIN,
    "bl3": OverroundMethod.SHIN,
    "lib": OverroundMethod.SHIN,
    # US sports — MWPO (tight 2-way markets, minimal FLB)
    "nba": OverroundMethod.MWPO,
    "cbb": OverroundMethod.MWPO,
    "nfl": OverroundMethod.MWPO,
    "nhl": OverroundMethod.MWPO,
    # Rugby — 2-way, similar to US sports
    "rusixnat": OverroundMethod.MWPO,
    "ruprem": OverroundMethod.MWPO,
    "rutopft": OverroundMethod.MWPO,
    "rueuchamp": OverroundMethod.MWPO,
    "ruurc": OverroundMethod.MWPO,
    "ruchamp": OverroundMethod.MWPO,
    # Cricket — 2-way match winner
    "ipl": OverroundMethod.MWPO,
    "crint": OverroundMethod.MWPO,
    "cricipl": OverroundMethod.MWPO,
    "cricpsl": OverroundMethod.MWPO,
    "cricpakt20cup": OverroundMethod.MWPO,
    # Non-soccer expansion
    "mlb": OverroundMethod.MWPO,
    "mma": OverroundMethod.POWER,  # 2-way, significant FLB
    # Tennis — Power method (2-way, significant FLB at extremes)
    "atp": OverroundMethod.POWER,
    "wta": OverroundMethod.POWER,
    # Esports — 2-way match winner, tight Pinnacle margins
    "cs2": OverroundMethod.MWPO,
    "dota2": OverroundMethod.MWPO,
}


# ── Edge Confidence Weights ──────────────────────────────────────────────────

@dataclass
class ConfidenceWeights:
    """Weights for edge confidence scoring. Each factor maps [0, 1] and
    the final confidence is the weighted geometric mean.

    Attributes
    ----------
    liquidity : float
        Weight for orderbook depth signal.
    book_agreement : float
        Weight for multi-book consensus (more books agreeing = higher).
    time_to_event : float
        Weight for proximity to game start (closer = more reliable).
    market_type : float
        Weight for market type reliability (h2h > spread > total).
    historical_accuracy : float
        Weight for learning agent calibration data.
    """
    liquidity: float = 0.15
    book_agreement: float = 0.30
    time_to_event: float = 0.25
    market_type: float = 0.15
    historical_accuracy: float = 0.15


# Market type reliability priors (h2h is most efficient, totals least)
MARKET_TYPE_RELIABILITY: dict[str, float] = {
    "h2h": 0.90,
    "spread": 0.75,
    "total": 0.60,
}


# ── Kelly Criterion Configuration ────────────────────────────────────────────

@dataclass
class KellyConfig:
    """Parameters for Kelly criterion position sizing.

    Attributes
    ----------
    default_fraction : float
        Default fraction of Kelly to use (0.25 = quarter-Kelly).
    max_fraction : float
        Hard cap on Kelly fraction even with high confidence.
    min_bet_usdc : float
        Minimum bet size in USDC (below this, skip — not worth gas).
    max_bet_usdc : float
        Maximum single bet regardless of Kelly output.
    estimation_error_penalty : float
        Discount factor for estimation uncertainty per Thorp (2006).
        Applied as f* = f_kelly * (1 - penalty * sigma_edge / edge).
    simultaneous_correlation : float
        Assumed correlation between simultaneous bets for portfolio Kelly.
        0 = independent, 1 = perfectly correlated.
    """
    default_fraction: float = 0.25
    max_fraction: float = 0.50
    min_bet_usdc: float = 2.0
    max_bet_usdc: float = 25.0
    estimation_error_penalty: float = 0.5
    simultaneous_correlation: float = 0.15


# ── Edge Decay Configuration ────────────────────────────────────────────────

@dataclass
class EdgeDecayConfig:
    """Parameters for time-based edge decay.

    Edges discovered far from game start are less reliable because:
    1. More information will arrive before the game
    2. Sharp books will adjust their lines
    3. Polymarket prices will converge to fair value

    The decay function is: decay(h) = min_factor + (1 - min_factor) * exp(-h / half_life)
    where h = hours to game start.

    Attributes
    ----------
    half_life_hours : float
        Hours at which edge decays to ~50% of near-close reliability.
    min_factor : float
        Floor on decay factor (never discount below this).
    close_window_hours : float
        Within this many hours of game start, no decay applied (factor = 1.0).
    """
    half_life_hours: float = 12.0
    min_factor: float = 0.40
    close_window_hours: float = 2.0


# ── CLV Tracking Configuration ──────────────────────────────────────────────

@dataclass
class CLVConfig:
    """Parameters for Closing Line Value tracking.

    Attributes
    ----------
    snapshot_interval_minutes : int
        How often to re-check the sharp line for open positions.
    pre_close_window_minutes : int
        Capture the "closing line" this many minutes before game start.
    clv_ema_alpha : float
        Exponential moving average smoothing for running CLV estimate.
    """
    snapshot_interval_minutes: int = 30
    pre_close_window_minutes: int = 15
    clv_ema_alpha: float = 0.1


# ── Book Efficiency Weights ─────────────────────────────────────────────────

# Historical efficiency rating for sharp books (higher = more efficient/reliable).
# Used for multi-book weighted consensus.
# Source: Empirical closing line accuracy studies (Pinnacle widely regarded as sharpest).
BOOK_EFFICIENCY_WEIGHTS: dict[str, float] = {
    "pinnacle": 1.00,
    "betfair_ex_eu": 0.95,
    "matchbook": 0.85,
    "betcris": 0.75,
    "bet365": 0.60,
    "williamhill": 0.55,
    "unibet": 0.50,
}


# ── Master Configuration ────────────────────────────────────────────────────

@dataclass
class EdgeModelConfig:
    """Top-level configuration for the entire edge estimation framework.

    Attributes
    ----------
    default_overround_method : str
        Fallback overround removal method when sport is unknown.
    shin_max_iterations : int
        Maximum iterations for Shin's model numerical solver.
    shin_tolerance : float
        Convergence tolerance for Shin's z parameter.
    power_max_iterations : int
        Maximum iterations for the power method bisection solver.
    power_tolerance : float
        Convergence tolerance for the power method exponent k.
    min_edge_pct : float
        Minimum edge (%) to consider a trade opportunity.
    max_edge_pct : float
        Maximum edge (%) — above this is likely a matching error.
    confidence_weights : ConfidenceWeights
        Weights for edge confidence scoring.
    kelly : KellyConfig
        Kelly criterion parameters.
    decay : EdgeDecayConfig
        Edge decay parameters.
    clv : CLVConfig
        Closing Line Value tracking parameters.
    """
    default_overround_method: str = OverroundMethod.PROPORTIONAL
    shin_max_iterations: int = 100
    shin_tolerance: float = 1e-8
    power_max_iterations: int = 100
    power_tolerance: float = 1e-8
    min_edge_pct: float = 1.0
    max_edge_pct: float = 200.0
    confidence_weights: ConfidenceWeights = field(default_factory=ConfidenceWeights)
    kelly: KellyConfig = field(default_factory=KellyConfig)
    decay: EdgeDecayConfig = field(default_factory=EdgeDecayConfig)
    clv: CLVConfig = field(default_factory=CLVConfig)
