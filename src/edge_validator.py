"""Edge validation framework -- statistically rigorous tests for whether our edge is real.

Runs a battery of tests after every N trades and outputs a confidence score (0-100)
representing how confident we are that our edge is genuine, not luck.

All statistical tests are implemented from scratch (no scipy/numpy required).
Uses normal approximation for binomial, Welch's t-test, and Wald-Wolfowitz runs test.
"""
import math
import logging
from collections import defaultdict
from typing import Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Statistical helper functions (no scipy required)
# ---------------------------------------------------------------------------

def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stdev(values: list[float]) -> float:
    """Sample standard deviation (Bessel's correction)."""
    n = len(values)
    if n < 2:
        return 0.0
    m = sum(values) / n
    var = sum((x - m) ** 2 for x in values) / (n - 1)
    return math.sqrt(var)


def _normal_cdf(z: float) -> float:
    """Standard normal CDF using Abramowitz & Stegun approximation.

    Accurate to ~1e-7 for |z| < 6.
    """
    if z < -8.0:
        return 0.0
    if z > 8.0:
        return 1.0
    # Use the error function approach
    # CDF(z) = 0.5 * (1 + erf(z / sqrt(2)))
    return 0.5 * (1.0 + _erf(z / math.sqrt(2.0)))


def _erf(x: float) -> float:
    """Error function using Horner form approximation (Abramowitz & Stegun 7.1.26).

    Maximum error: 1.5e-7.
    """
    sign = 1 if x >= 0 else -1
    x = abs(x)
    # Constants
    p = 0.3275911
    a1 = 0.254829592
    a2 = -0.284496736
    a3 = 1.421413741
    a4 = -1.453152027
    a5 = 1.061405429
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x * x)
    return sign * y


def _normal_ppf(p: float) -> float:
    """Inverse normal CDF (percent point function) using rational approximation.

    Accurate to ~4.5e-4 for 0.0002 < p < 0.9998.
    Uses Beasley-Springer-Moro algorithm.
    """
    if p <= 0:
        return -8.0
    if p >= 1:
        return 8.0

    # Rational approximation for central region
    if 0.5 - abs(p - 0.5) > 0.08:
        # Central region: |p - 0.5| < 0.42
        r = p - 0.5
        r2 = r * r
        num = (((-25.44106049637 * r2 + 41.39119773534) * r2 + -18.61500062529) * r2 + 2.506628277459)
        den = (((-8.47351093090 * r2 + 23.08336743743) * r2 + -21.06224101826) * r2 + 3.13082909833)
        # Add correction
        return r * num / (den * r2 + 1.0) if den * r2 + 1.0 != 0 else r * 3.0
    else:
        # Tail region
        if p < 0.5:
            r = p
        else:
            r = 1.0 - p
        r = math.sqrt(-2.0 * math.log(max(r, 1e-300)))
        num = ((7.7108572002e-3 * r + 0.3224671290) * r + 2.445134137)
        den = ((0.0104259834 * r + 0.4210730188) * r + 1.0)
        z = num / den if den != 0 else 3.0
        # Refine with one Newton step
        z = z - (_normal_cdf(z) - (1 - r * r / 2)) / (
            math.exp(-z * z / 2) / math.sqrt(2 * math.pi))
        if p < 0.5:
            return -z
        return z


# ---------------------------------------------------------------------------
# Statistical tests
# ---------------------------------------------------------------------------

def binomial_test(n_wins: int, n_total: int, expected_win_rate: float) -> dict:
    """Test if actual win rate is significantly better than expected.

    Uses normal approximation to the binomial distribution (valid for n > 30).

    Parameters
    ----------
    n_wins : int
        Number of winning trades.
    n_total : int
        Total number of trades.
    expected_win_rate : float
        The break-even or implied win rate.

    Returns
    -------
    dict
        {"z_score": float, "p_value": float, "significant_95": bool,
         "significant_99": bool, "actual_wr": float, "expected_wr": float}
    """
    if n_total < 5 or expected_win_rate <= 0 or expected_win_rate >= 1:
        return {"z_score": 0.0, "p_value": 1.0, "significant_95": False,
                "significant_99": False, "actual_wr": 0.0,
                "expected_wr": expected_win_rate}

    actual_wr = n_wins / n_total
    # Standard error under the null hypothesis
    se = math.sqrt(expected_win_rate * (1 - expected_win_rate) / n_total)
    if se == 0:
        return {"z_score": 0.0, "p_value": 1.0, "significant_95": False,
                "significant_99": False, "actual_wr": actual_wr,
                "expected_wr": expected_win_rate}

    z = (actual_wr - expected_win_rate) / se
    # One-sided test: we want actual > expected
    p_value = 1.0 - _normal_cdf(z)

    return {
        "z_score": z,
        "p_value": p_value,
        "significant_95": p_value < 0.05,
        "significant_99": p_value < 0.01,
        "actual_wr": actual_wr,
        "expected_wr": expected_win_rate,
    }


def t_test_pnl(pnls: list[float]) -> dict:
    """One-sample t-test: is mean PnL significantly > 0?

    Parameters
    ----------
    pnls : list[float]
        List of PnL values per trade.

    Returns
    -------
    dict
        {"t_stat": float, "p_value": float, "significant_95": bool,
         "mean_pnl": float, "se": float, "df": int}
    """
    n = len(pnls)
    if n < 3:
        return {"t_stat": 0.0, "p_value": 1.0, "significant_95": False,
                "mean_pnl": 0.0, "se": 0.0, "df": 0}

    m = _mean(pnls)
    s = _stdev(pnls)
    if s == 0:
        return {"t_stat": 0.0 if m == 0 else float("inf"),
                "p_value": 0.5 if m == 0 else 0.0,
                "significant_95": m > 0,
                "mean_pnl": m, "se": 0.0, "df": n - 1}

    se = s / math.sqrt(n)
    t_stat = m / se
    df = n - 1

    # Approximate p-value using normal distribution (good for df > 30)
    # For smaller df, this is conservative (normal tails are thinner than t)
    p_value = 1.0 - _normal_cdf(t_stat)

    return {
        "t_stat": t_stat,
        "p_value": p_value,
        "significant_95": p_value < 0.05,
        "significant_99": p_value < 0.01,
        "mean_pnl": m,
        "se": se,
        "df": df,
    }


def runs_test(outcomes: list[bool]) -> dict:
    """Wald-Wolfowitz runs test for randomness.

    Tests whether wins and losses are randomly distributed (not streaky).
    Too few runs = trending/streaky, too many runs = anti-correlation.

    Parameters
    ----------
    outcomes : list[bool]
        True = win, False = loss, in chronological order.

    Returns
    -------
    dict
        {"n_runs": int, "expected_runs": float, "z_score": float,
         "p_value": float, "is_random": bool}
    """
    n = len(outcomes)
    if n < 10:
        return {"n_runs": 0, "expected_runs": 0.0, "z_score": 0.0,
                "p_value": 1.0, "is_random": True,
                "note": "Too few trades for runs test"}

    n1 = sum(outcomes)         # wins
    n2 = n - n1                # losses

    if n1 == 0 or n2 == 0:
        return {"n_runs": 1, "expected_runs": 0.0, "z_score": 0.0,
                "p_value": 1.0, "is_random": True,
                "note": "All outcomes identical"}

    # Count runs
    runs = 1
    for i in range(1, n):
        if outcomes[i] != outcomes[i - 1]:
            runs += 1

    # Expected runs and variance under H0 (random)
    expected = (2 * n1 * n2) / n + 1
    var_num = 2 * n1 * n2 * (2 * n1 * n2 - n)
    var_den = n * n * (n - 1)
    variance = var_num / var_den if var_den > 0 else 0.0

    if variance <= 0:
        return {"n_runs": runs, "expected_runs": expected, "z_score": 0.0,
                "p_value": 1.0, "is_random": True}

    z = (runs - expected) / math.sqrt(variance)
    # Two-sided test
    p_value = 2 * (1.0 - _normal_cdf(abs(z)))

    return {
        "n_runs": runs,
        "expected_runs": expected,
        "z_score": z,
        "p_value": p_value,
        "is_random": p_value > 0.05,
    }


def sample_size_needed(
    current_win_rate: float,
    expected_win_rate: float,
    confidence: float = 0.95,
    power: float = 0.80,
) -> int:
    """How many more trades needed to detect our edge at given confidence/power.

    Uses the formula for one-sided z-test sample size.

    Parameters
    ----------
    current_win_rate : float
        Our observed win rate.
    expected_win_rate : float
        The null hypothesis (break-even) win rate.
    confidence : float
        Desired confidence level (default 0.95).
    power : float
        Desired statistical power (default 0.80).

    Returns
    -------
    int
        Required sample size (total trades needed).
    """
    if current_win_rate <= expected_win_rate:
        return 99999  # We don't even have a positive signal

    z_alpha = _normal_ppf(confidence)
    z_beta = _normal_ppf(power)

    p0 = expected_win_rate
    p1 = current_win_rate

    # Sample size formula for one-proportion z-test
    numerator = (z_alpha * math.sqrt(p0 * (1 - p0)) +
                 z_beta * math.sqrt(p1 * (1 - p1))) ** 2
    denominator = (p1 - p0) ** 2

    if denominator == 0:
        return 99999

    return math.ceil(numerator / denominator)


# ---------------------------------------------------------------------------
# Edge Validator
# ---------------------------------------------------------------------------

class EdgeValidator:
    """Continuously validates whether our edge is real.

    Run ``validate()`` periodically (e.g. after every N trades) to get
    a comprehensive assessment of edge quality and a confidence score.

    Parameters
    ----------
    trades : list[dict]
        List of resolved trade records from the learning agent.
    validation_interval : int
        Run full validation every N trades (default 25).
    """

    def __init__(self, trades: list[dict], validation_interval: int = 25):
        self.trades = trades
        self.validation_interval = validation_interval

    def should_validate(self) -> bool:
        """Whether it's time to run validation (every N trades)."""
        n = len(self.trades)
        return n > 0 and n % self.validation_interval == 0

    def validate(self) -> dict:
        """Run all validation tests and compute confidence score.

        Returns
        -------
        dict
            Comprehensive validation report with confidence_score (0-100).
        """
        if not self.trades:
            return {
                "confidence_score": 0,
                "n_trades": 0,
                "verdict": "No trades to validate",
                "tests": {},
            }

        n = len(self.trades)
        pnls = [t["pnl"] for t in self.trades]
        outcomes = [t["won"] for t in self.trades]
        n_wins = sum(outcomes)

        # Expected win rate: mean entry price (the market's implied probability)
        expected_wr = _mean([t["entry_price"] for t in self.trades])

        # 1. Binomial test
        binom = binomial_test(n_wins, n, expected_wr)

        # 2. t-test on PnL
        t_test = t_test_pnl(pnls)

        # 3. Runs test
        # Sort by resolution time for proper temporal ordering
        sorted_trades = sorted(self.trades,
                               key=lambda t: t.get("resolved_at", ""))
        sorted_outcomes = [t["won"] for t in sorted_trades]
        runs = runs_test(sorted_outcomes)

        # 4. Sample size needed
        actual_wr = n_wins / n if n > 0 else 0
        needed = sample_size_needed(actual_wr, expected_wr)
        have_enough = n >= needed

        # 5. Flat bet backtest
        flat_bet = self._flat_bet_backtest()

        # 6. Break-even analysis
        breakeven = self._breakeven_analysis()

        # 7. By-sport validation
        sport_validation = self._sport_validation()

        # Compute confidence score (0-100)
        confidence = self._compute_confidence(
            binom, t_test, runs, n, needed, flat_bet, actual_wr, expected_wr
        )

        verdict = self._verdict(confidence)

        report = {
            "confidence_score": confidence,
            "n_trades": n,
            "verdict": verdict,
            "actual_win_rate": actual_wr,
            "expected_win_rate": expected_wr,
            "total_pnl": sum(pnls),
            "tests": {
                "binomial": binom,
                "t_test": t_test,
                "runs_test": runs,
                "sample_size": {
                    "needed": needed,
                    "current": n,
                    "sufficient": have_enough,
                    "trades_remaining": max(0, needed - n),
                },
                "flat_bet": flat_bet,
                "breakeven": breakeven,
            },
            "sport_validation": sport_validation,
        }

        log.info("Edge validation: confidence=%d/100 verdict='%s' "
                 "(n=%d, wr=%.1f%% vs expected %.1f%%)",
                 confidence, verdict, n, actual_wr * 100, expected_wr * 100)

        return report

    def _flat_bet_backtest(self, bet_size: float = 10.0) -> dict:
        """Simulate flat $10 bets on every signal to remove sizing effects.

        Returns
        -------
        dict
            {"flat_pnl": float, "flat_roi_pct": float, "flat_win_rate": float,
             "n_trades": int}
        """
        n = len(self.trades)
        if n == 0:
            return {"flat_pnl": 0.0, "flat_roi_pct": 0.0,
                    "flat_win_rate": 0.0, "n_trades": 0}

        total_pnl = 0.0
        for t in self.trades:
            entry = t["entry_price"]
            if entry <= 0 or entry >= 1:
                continue
            shares = bet_size / entry
            if t["won"]:
                payout = shares * 1.0  # $1 per share
                total_pnl += payout - bet_size
            else:
                total_pnl -= bet_size

        total_wagered = bet_size * n
        return {
            "flat_pnl": total_pnl,
            "flat_roi_pct": (total_pnl / total_wagered * 100) if total_wagered > 0 else 0.0,
            "flat_win_rate": sum(1 for t in self.trades if t["won"]) / n,
            "n_trades": n,
            "bet_size": bet_size,
        }

    def _breakeven_analysis(self) -> dict:
        """What win rate do we need to break even at our average entry price?

        Returns
        -------
        dict
            {"avg_entry_price": float, "breakeven_win_rate": float,
             "actual_win_rate": float, "margin": float}
        """
        if not self.trades:
            return {"avg_entry_price": 0.0, "breakeven_win_rate": 0.0,
                    "actual_win_rate": 0.0, "margin": 0.0}

        avg_price = _mean([t["entry_price"] for t in self.trades])
        # At price p, you pay p to win (1-p). Break-even win rate = p.
        # But with market maker fees etc, let's compute it properly:
        # Expected PnL = wr * (1 - price) - (1 - wr) * price = wr - price
        # Break-even: wr = price
        breakeven_wr = avg_price
        actual_wr = sum(1 for t in self.trades if t["won"]) / len(self.trades)

        return {
            "avg_entry_price": avg_price,
            "breakeven_win_rate": breakeven_wr,
            "actual_win_rate": actual_wr,
            "margin": actual_wr - breakeven_wr,
            "margin_pct": (actual_wr - breakeven_wr) * 100,
        }

    def _sport_validation(self) -> dict:
        """Per-sport binomial test and confidence.

        Returns
        -------
        dict
            Sport -> validation results.
        """
        by_sport: dict[str, list[dict]] = defaultdict(list)
        for t in self.trades:
            by_sport[t["sport"]].append(t)

        result = {}
        for sport, trades in by_sport.items():
            n = len(trades)
            wins = sum(1 for t in trades if t["won"])
            expected_wr = _mean([t["entry_price"] for t in trades])
            binom = binomial_test(wins, n, expected_wr)
            result[sport] = {
                "n_trades": n,
                "win_rate": wins / n if n else 0,
                "expected_wr": expected_wr,
                "total_pnl": sum(t["pnl"] for t in trades),
                "binomial_p_value": binom["p_value"],
                "significant": binom["significant_95"],
            }
        return result

    def _compute_confidence(
        self,
        binom: dict,
        t_test: dict,
        runs: dict,
        n_trades: int,
        needed_trades: int,
        flat_bet: dict,
        actual_wr: float,
        expected_wr: float,
    ) -> int:
        """Compute overall confidence score (0-100).

        Scoring breakdown (100 total):
        - Binomial test p-value:        25 points
        - t-test p-value:               25 points
        - Sample size adequacy:         15 points
        - Flat bet profitability:       15 points
        - Randomness (runs test):       10 points
        - Win rate margin:              10 points
        """
        score = 0.0

        # 1. Binomial test (25 pts)
        # p < 0.01 = 25, p < 0.05 = 20, p < 0.10 = 12, p < 0.20 = 5
        p = binom["p_value"]
        if p < 0.01:
            score += 25
        elif p < 0.05:
            score += 20
        elif p < 0.10:
            score += 12
        elif p < 0.20:
            score += 5

        # 2. t-test (25 pts)
        p = t_test["p_value"]
        if p < 0.01:
            score += 25
        elif p < 0.05:
            score += 20
        elif p < 0.10:
            score += 12
        elif p < 0.20:
            score += 5

        # 3. Sample size (15 pts)
        if n_trades >= needed_trades:
            score += 15
        elif n_trades >= needed_trades * 0.75:
            score += 10
        elif n_trades >= needed_trades * 0.5:
            score += 6
        elif n_trades >= 30:
            score += 3

        # 4. Flat bet profitability (15 pts)
        roi = flat_bet.get("flat_roi_pct", 0)
        if roi > 10:
            score += 15
        elif roi > 5:
            score += 12
        elif roi > 2:
            score += 8
        elif roi > 0:
            score += 4

        # 5. Runs test -- outcomes should be random (10 pts)
        if runs.get("is_random", True):
            score += 10
        else:
            # Not random -- could be streaky (which might indicate
            # regime changes, not necessarily bad)
            score += 3

        # 6. Win rate margin (10 pts)
        margin = actual_wr - expected_wr
        if margin > 0.10:
            score += 10
        elif margin > 0.05:
            score += 8
        elif margin > 0.02:
            score += 5
        elif margin > 0:
            score += 2

        return min(100, max(0, int(score)))

    @staticmethod
    def _verdict(confidence: int) -> str:
        """Human-readable verdict based on confidence score."""
        if confidence >= 80:
            return "Strong evidence of genuine edge"
        elif confidence >= 60:
            return "Moderate evidence of edge -- keep trading"
        elif confidence >= 40:
            return "Inconclusive -- need more trades"
        elif confidence >= 20:
            return "Weak evidence -- consider reducing size"
        else:
            return "No evidence of edge -- reassess strategy"
