"""Rigorous, research-backed edge estimation framework for sports betting
on prediction markets (Polymarket).

This module replaces the naive ``fair_prob = implied / sum(implied)`` calculation
with a suite of methods grounded in the academic sports-betting and prediction-
market literature. It provides:

1. **Multiple overround removal methods** — Proportional, Shin (1991),
   Power (Clarke et al.), Odds-Ratio (Cheung 2015), MWPO.
2. **Fair probability estimation** — multi-book weighted consensus, orderbook
   midpoint, Bayesian combination.
3. **Edge confidence scoring** — liquidity, book agreement, time-to-event,
   market type, historical accuracy.
4. **Proper Kelly criterion** — standard, fractional, simultaneous, and
   estimation-error-adjusted variants.
5. **Expected value calculations** — simple EV, risk-adjusted EV, expected
   growth rate, ROI projection.
6. **Closing Line Value (CLV) tracking** — the gold-standard profitability
   predictor in sharp sports betting.
7. **Edge decay model** — time-based discounting of edges discovered far from
   game start.

Academic References
-------------------
- Shin, H.S. (1991). "Optimal Betting Odds Against Insider Traders."
  *Economic Journal*, 101(408), 1179-1185.
- Shin, H.S. (1993). "Measuring the Incidence of Insider Trading in a
  Market for State-Contingent Claims." *Economic Journal*, 103(420), 1141-1153.
- Clarke, S., Krase, S., Statman, M. (2017). "Removing the Favourite-Longshot
  Bias from Bookmaker Odds." *Journal of Gambling Studies*.
- Cheung, K. (2015). "A Comparison of Methods for Removing the Margin from
  Bookmaker Odds." *Journal of Prediction Markets*.
- Thorp, E.O. (2006). "The Kelly Criterion in Blackjack, Sports Betting, and
  the Stock Market." In *Handbook of Asset and Liability Management*, Vol. 1.
- Kelly, J.L. (1956). "A New Interpretation of Information Rate." *Bell
  System Technical Journal*, 35(4), 917-926.
- Brier, G.W. (1950). "Verification of Forecasts Expressed in Terms of
  Probability." *Monthly Weather Review*, 78(1), 1-3.
- Manski, C.F. (2006). "Interpreting the Predictions of Prediction Markets."
  *Economics Letters*, 91(3), 425-429.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .edge_config import (
    EdgeModelConfig,
    OverroundMethod,
    SPORT_OVERROUND_DEFAULTS,
    BOOK_EFFICIENCY_WEIGHTS,
    MARKET_TYPE_RELIABILITY,
)

log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
#  1. OVERROUND REMOVAL
# ══════════════════════════════════════════════════════════════════════════════

class OverroundRemoval:
    """Static methods for removing bookmaker overround (vig/juice) from
    implied probabilities to recover fair probabilities.

    All methods accept a list of raw implied probabilities (which sum > 1
    due to the bookmaker's margin) and return fair probabilities that sum
    to exactly 1.0.

    The choice of method matters: different methods make different assumptions
    about how the bookmaker distributes margin across outcomes. Using the
    wrong method introduces systematic bias in fair probability estimates.
    """

    @staticmethod
    def proportional(implied_probs: list[float]) -> list[float]:
        """Proportional (basic normalization) overround removal.

        Divides each implied probability by the total implied probability
        (the overround). Assumes the bookmaker adds equal proportional
        margin to every outcome.

        This is the simplest method and is adequate when the market is
        tight (overround < 3%) or when outcomes have similar probabilities.
        However, it systematically **underestimates** longshot probabilities
        and **overestimates** favourite probabilities — the classic
        favourite-longshot bias (FLB).

        Parameters
        ----------
        implied_probs : list[float]
            Raw implied probabilities from decimal odds (1/odds).
            Must sum to > 1.0 (otherwise no overround to remove).

        Returns
        -------
        list[float]
            Fair probabilities summing to 1.0.

        References
        ----------
        - Widely used baseline; see Štrumbelj (2014) for comparison.
        """
        total = sum(implied_probs)
        if total <= 0:
            return [0.0] * len(implied_probs)
        return [p / total for p in implied_probs]

    @staticmethod
    def shin(
        implied_probs: list[float],
        max_iter: int = 100,
        tol: float = 1e-8,
    ) -> list[float]:
        """Shin's model (1991, 1993) for overround removal.

        Shin's model assumes the bookmaker sets odds to protect against a
        fraction *z* of informed (insider) bettors in the market. The margin
        is not distributed equally: longshots carry proportionally more
        margin because the bookmaker faces higher adverse-selection risk
        on them.

        The model solves for the insider-trading fraction z such that the
        resulting fair probabilities sum to 1. The key equation for each
        outcome i with n outcomes is:

            fair_prob_i = (sqrt(z^2 + 4*(1-z) * ip_i^2 / S) - z) / (2*(1-z))

        where S = sum(implied_probs) and ip_i is the raw implied probability.

        This method is **strongly recommended for soccer** (3-way markets
        where FLB is empirically significant) and any market with a clear
        favourite/longshot structure.

        Parameters
        ----------
        implied_probs : list[float]
            Raw implied probabilities from decimal odds.
        max_iter : int
            Maximum iterations for the bisection solver.
        tol : float
            Convergence tolerance for z.

        Returns
        -------
        list[float]
            Fair probabilities summing to ~1.0.

        References
        ----------
        - Shin, H.S. (1991). "Optimal Betting Odds Against Insider Traders."
          *Economic Journal*, 101(408), 1179-1185.
        - Shin, H.S. (1993). "Measuring the Incidence of Insider Trading."
          *Economic Journal*, 103(420), 1141-1153.
        - Jullien, B. & Salanié, B. (1994). "Measuring the incidence of
          insider trading: A comment on Shin." *Economic Journal*, 104, 1418-1419.
        """
        n = len(implied_probs)
        if n == 0:
            return []
        total = sum(implied_probs)
        if total <= 0:
            return [0.0] * n

        # If already fair (no overround), return proportional
        if abs(total - 1.0) < tol:
            return list(implied_probs)

        def _fair_probs_for_z(z: float) -> list[float]:
            """Compute Shin fair probs given insider fraction z."""
            fps = []
            for ip in implied_probs:
                discriminant = z * z + 4.0 * (1.0 - z) * (ip * ip) / total
                if discriminant < 0:
                    fps.append(ip / total)  # fallback
                else:
                    fp = (math.sqrt(discriminant) - z) / (2.0 * (1.0 - z))
                    fps.append(max(fp, 0.0))
            return fps

        # Bisection: find z in (0, 1) such that sum(fair_probs) = 1
        z_lo, z_hi = 1e-10, 1.0 - 1e-10
        z_best = 0.0

        for _ in range(max_iter):
            z_mid = (z_lo + z_hi) / 2.0
            fps = _fair_probs_for_z(z_mid)
            s = sum(fps)

            if abs(s - 1.0) < tol:
                z_best = z_mid
                break

            # When z increases, fair probs decrease (more margin allocated
            # to longshots), so total decreases. When z decreases, total
            # increases.
            if s > 1.0:
                z_lo = z_mid
            else:
                z_hi = z_mid
            z_best = z_mid

        fair = _fair_probs_for_z(z_best)

        # Final normalization to handle numerical drift
        s = sum(fair)
        if s > 0 and abs(s - 1.0) > 1e-6:
            fair = [p / s for p in fair]

        return fair

    @staticmethod
    def power(
        implied_probs: list[float],
        max_iter: int = 100,
        tol: float = 1e-8,
    ) -> list[float]:
        """Power method for overround removal.

        Each raw implied probability is raised to a power k > 1 such that
        the resulting probabilities sum to 1:

            fair_prob_i = implied_prob_i^k  where sum(implied_prob_i^k) = 1

        The exponent k is found via bisection. This method assumes the
        bookmaker's margin is related to the "curvature" of odds — it
        compresses probabilities toward 0.5 (reducing the gap between
        favourites and longshots). Empirically, it performs well for
        2-outcome markets (tennis, basketball, NFL).

        Parameters
        ----------
        implied_probs : list[float]
            Raw implied probabilities.
        max_iter : int
            Maximum bisection iterations.
        tol : float
            Convergence tolerance.

        Returns
        -------
        list[float]
            Fair probabilities summing to ~1.0.

        References
        ----------
        - Clarke, S., Krase, S., Statman, M. (2017). "Removing the
          Favourite-Longshot Bias from Bookmaker Odds."
        """
        n = len(implied_probs)
        if n == 0:
            return []
        total = sum(implied_probs)
        if total <= 0:
            return [0.0] * n
        if abs(total - 1.0) < tol:
            return list(implied_probs)

        # Normalize to probabilities first (basic proportional)
        normed = [p / total for p in implied_probs]

        # Bisect for k such that sum(normed_i^k) = 1
        # k > 1 makes larger probs larger and smaller probs smaller
        # k < 1 does the opposite. We need k >= 1 since total > 1.
        k_lo, k_hi = 0.5, 10.0

        # Verify the function brackets 1.0
        def _sum_pow(k: float) -> float:
            return sum(p ** k for p in normed if p > 0)

        # _sum_pow(1.0) = sum(normed) = 1.0 exactly since normed sums to 1
        # We actually need to work with the un-normalized implied_probs
        # and find k such that sum(implied_prob_i^k) = 1
        def _sum_pow_raw(k: float) -> float:
            return sum(p ** k for p in implied_probs if p > 0)

        # At k=1: sum = total > 1. As k -> inf: sum -> max(p)^k which goes
        # to 0 for max(p) < 1. So the root is in (1, large_k).
        k_lo = 1.0
        k_hi = 50.0

        # Make sure k_hi gives sum < 1
        while _sum_pow_raw(k_hi) > 1.0 and k_hi < 1000:
            k_hi *= 2

        for _ in range(max_iter):
            k_mid = (k_lo + k_hi) / 2.0
            s = _sum_pow_raw(k_mid)
            if abs(s - 1.0) < tol:
                break
            if s > 1.0:
                k_lo = k_mid
            else:
                k_hi = k_mid

        k_final = (k_lo + k_hi) / 2.0
        fair = [p ** k_final if p > 0 else 0.0 for p in implied_probs]

        # Normalize for numerical safety
        s = sum(fair)
        if s > 0:
            fair = [p / s for p in fair]

        return fair

    @staticmethod
    def odds_ratio(implied_probs: list[float]) -> list[float]:
        """Odds-ratio method for overround removal.

        Instead of adjusting probabilities, this method adjusts the *odds*
        (in odds-against form) by equal amounts. The fair odds for outcome i
        satisfy:

            odds_fair_i = odds_raw_i * c

        where c is chosen so that the resulting probabilities sum to 1.
        In probability space:

            fair_prob_i = 1 / (1 + c * (1/implied_prob_i - 1))

        The constant c is found by solving:
            sum_i [1 / (1 + c * (1/p_i - 1))] = 1

        This method distributes margin equally in the *odds* domain rather
        than the *probability* domain, producing different results especially
        at extreme probabilities.

        Parameters
        ----------
        implied_probs : list[float]
            Raw implied probabilities.

        Returns
        -------
        list[float]
            Fair probabilities summing to ~1.0.

        References
        ----------
        - Cheung, K. (2015). "A Comparison of Methods for Removing the
          Margin from Bookmaker Odds."
        """
        n = len(implied_probs)
        if n == 0:
            return []
        total = sum(implied_probs)
        if total <= 0:
            return [0.0] * n
        if abs(total - 1.0) < 1e-8:
            return list(implied_probs)

        # Bisect for c
        # At c=1: sum = total > 1
        # As c -> inf: each term -> 0, so sum -> 0
        c_lo, c_hi = 0.001, 100.0

        def _sum_for_c(c: float) -> float:
            s = 0.0
            for p in implied_probs:
                if p <= 0 or p >= 1:
                    s += p  # degenerate
                else:
                    s += 1.0 / (1.0 + c * (1.0 / p - 1.0))
            return s

        for _ in range(200):
            c_mid = (c_lo + c_hi) / 2.0
            s = _sum_for_c(c_mid)
            if abs(s - 1.0) < 1e-8:
                break
            if s > 1.0:
                c_lo = c_mid
            else:
                c_hi = c_mid

        c_final = (c_lo + c_hi) / 2.0
        fair = []
        for p in implied_probs:
            if p <= 0 or p >= 1:
                fair.append(p)
            else:
                fair.append(1.0 / (1.0 + c_final * (1.0 / p - 1.0)))

        # Normalize
        s = sum(fair)
        if s > 0:
            fair = [fp / s for fp in fair]

        return fair

    @staticmethod
    def mwpo(implied_probs: list[float]) -> list[float]:
        """Margin Weights Proportional to Odds (MWPO).

        Assumes the bookmaker distributes margin proportionally to the
        decimal odds (inversely proportional to probability). Longshots
        carry more absolute margin. The formula is:

            fair_prob_i = implied_prob_i - w_i * M

        where M = sum(implied_probs) - 1 (total margin) and
        w_i = (1/implied_prob_i) / sum(1/implied_prob_j) — weight
        proportional to odds.

        This method is suitable for tight 2-way markets (NBA, NFL) where
        the bookmaker shades margin toward the less liquid side.

        Parameters
        ----------
        implied_probs : list[float]
            Raw implied probabilities.

        Returns
        -------
        list[float]
            Fair probabilities summing to ~1.0.

        References
        ----------
        - Empirical studies on NBA/NFL market microstructure suggest margin
          loading is proportional to odds length.
        """
        n = len(implied_probs)
        if n == 0:
            return []
        total = sum(implied_probs)
        if total <= 0:
            return [0.0] * n
        if abs(total - 1.0) < 1e-8:
            return list(implied_probs)

        margin = total - 1.0

        # Weights proportional to decimal odds (= 1/p)
        inverse_probs = [1.0 / p if p > 0 else 0.0 for p in implied_probs]
        sum_inverse = sum(inverse_probs)
        if sum_inverse <= 0:
            return OverroundRemoval.proportional(implied_probs)

        weights = [ip / sum_inverse for ip in inverse_probs]

        fair = [p - w * margin for p, w in zip(implied_probs, weights)]

        # Clamp negatives (can happen if margin is very large relative to
        # a longshot probability)
        fair = [max(fp, 1e-6) for fp in fair]

        # Normalize
        s = sum(fair)
        if s > 0:
            fair = [fp / s for fp in fair]

        return fair

    @classmethod
    def remove(
        cls,
        implied_probs: list[float],
        method: str = OverroundMethod.PROPORTIONAL,
        **kwargs,
    ) -> list[float]:
        """Dispatch to the appropriate overround removal method.

        Parameters
        ----------
        implied_probs : list[float]
            Raw implied probabilities.
        method : str
            One of: 'proportional', 'shin', 'power', 'odds_ratio', 'mwpo'.
        **kwargs
            Passed to the underlying method (e.g., max_iter, tol for Shin).

        Returns
        -------
        list[float]
            Fair probabilities.
        """
        dispatch = {
            OverroundMethod.PROPORTIONAL: cls.proportional,
            OverroundMethod.SHIN: cls.shin,
            OverroundMethod.POWER: cls.power,
            OverroundMethod.ODDS_RATIO: cls.odds_ratio,
            OverroundMethod.MWPO: cls.mwpo,
        }
        fn = dispatch.get(method)
        if fn is None:
            log.warning("Unknown overround method '%s', falling back to proportional", method)
            fn = cls.proportional

        # Only pass kwargs to methods that accept them
        if method in (OverroundMethod.SHIN, OverroundMethod.POWER):
            return fn(implied_probs, **kwargs)
        return fn(implied_probs)


# ══════════════════════════════════════════════════════════════════════════════
#  2. FAIR PROBABILITY ESTIMATOR
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class BookOdds:
    """Odds from a single bookmaker for one outcome.

    Attributes
    ----------
    bookmaker : str
        Bookmaker identifier (e.g. 'pinnacle').
    decimal_odds : float
        Decimal odds (e.g. 2.50 means +150 American).
    implied_prob : float
        Raw implied probability (1 / decimal_odds).
    """
    bookmaker: str
    decimal_odds: float
    implied_prob: float


class FairProbEstimator:
    """Combines multiple signals into a fair probability estimate.

    Signals (in order of priority):
    1. Multi-book consensus from sharp bookmakers (Pinnacle, Betfair, etc.)
    2. Polymarket orderbook midpoint (bid-ask midpoint vs. last trade)
    3. Bayesian combination with own model confidence

    The estimator supports single-book and multi-book modes. In single-book
    mode (when only one sharp book is available), it uses the configured
    overround removal method. In multi-book mode, it takes an efficiency-
    weighted average of fair probabilities from each book.
    """

    def __init__(self, config: EdgeModelConfig | None = None):
        self.config = config or EdgeModelConfig()

    def fair_prob_single_book(
        self,
        outcome_index: int,
        all_implied_probs: list[float],
        sport: str = "",
        method_override: str | None = None,
    ) -> float:
        """Compute fair probability for one outcome from a single bookmaker.

        Parameters
        ----------
        outcome_index : int
            Index of the target outcome in the implied_probs list.
        all_implied_probs : list[float]
            Raw implied probabilities for ALL outcomes in the market.
        sport : str
            Sport key for sport-specific overround method selection.
        method_override : str or None
            Force a specific overround removal method.

        Returns
        -------
        float
            Fair probability for the target outcome.
        """
        if outcome_index < 0 or outcome_index >= len(all_implied_probs):
            return 0.0

        method = method_override or SPORT_OVERROUND_DEFAULTS.get(
            sport, self.config.default_overround_method
        )

        fair_probs = OverroundRemoval.remove(
            all_implied_probs,
            method=method,
            max_iter=self.config.shin_max_iterations,
            tol=self.config.shin_tolerance,
        )

        return fair_probs[outcome_index] if outcome_index < len(fair_probs) else 0.0

    def fair_prob_multi_book(
        self,
        outcome_name: str,
        books_data: list[dict],
        sport: str = "",
    ) -> float:
        """Compute fair probability from multiple bookmakers using an
        efficiency-weighted average.

        Each bookmaker's fair probability is computed individually (using the
        appropriate overround removal method), then combined using weights
        from ``BOOK_EFFICIENCY_WEIGHTS``.

        Parameters
        ----------
        outcome_name : str
            Name of the target outcome (e.g. "Arsenal", "Over").
        books_data : list[dict]
            List of bookmaker data, each containing:
            - 'bookmaker': str (key like 'pinnacle')
            - 'outcomes': dict mapping outcome_name -> {'decimal_odds': float}
        sport : str
            Sport key.

        Returns
        -------
        float
            Efficiency-weighted fair probability.
        """
        weighted_sum = 0.0
        weight_total = 0.0

        for book in books_data:
            bk_name = book.get("bookmaker", "")
            outcomes = book.get("outcomes", {})
            if outcome_name not in outcomes:
                continue

            # Build implied probs for all outcomes in this book
            all_names = sorted(outcomes.keys())
            all_implied = []
            target_idx = -1
            for i, name in enumerate(all_names):
                odds = outcomes[name].get("decimal_odds", 0)
                ip = 1.0 / odds if odds > 0 else 0.0
                all_implied.append(ip)
                if name == outcome_name:
                    target_idx = i

            if target_idx < 0 or not all_implied:
                continue

            fp = self.fair_prob_single_book(target_idx, all_implied, sport=sport)
            weight = BOOK_EFFICIENCY_WEIGHTS.get(bk_name, 0.5)

            weighted_sum += fp * weight
            weight_total += weight

        if weight_total <= 0:
            return 0.0

        return weighted_sum / weight_total

    def fair_prob_orderbook_midpoint(
        self,
        best_bid: float,
        best_ask: float,
        last_trade: float | None = None,
    ) -> float:
        """Estimate fair probability from Polymarket orderbook.

        The midpoint of the best bid and best ask is generally the best
        estimate of the market's consensus fair value in a continuous
        double auction.

        If the last trade price deviates significantly from the midpoint,
        we give slight weight to the trade price (it reflects actual
        willingness to transact).

        Parameters
        ----------
        best_bid : float
            Highest bid price (0-1).
        best_ask : float
            Lowest ask price (0-1).
        last_trade : float or None
            Most recent trade price.

        Returns
        -------
        float
            Estimated fair probability (0-1).
        """
        if best_bid <= 0 and best_ask <= 0:
            return last_trade if last_trade and last_trade > 0 else 0.0

        if best_bid <= 0:
            midpoint = best_ask
        elif best_ask <= 0:
            midpoint = best_bid
        else:
            midpoint = (best_bid + best_ask) / 2.0

        if last_trade is not None and last_trade > 0:
            # Weight: 80% midpoint, 20% last trade
            return 0.8 * midpoint + 0.2 * last_trade

        return midpoint

    def bayesian_combine(
        self,
        sharp_fair_prob: float,
        model_fair_prob: float,
        sharp_weight: float = 0.75,
        model_weight: float = 0.25,
    ) -> float:
        """Bayesian combination of sharp book fair value and own model estimate.

        Uses a simple weighted average as an approximation to proper Bayesian
        updating. The weights reflect relative trust in each signal.

        In practice, the sharp book line (especially Pinnacle) is extremely
        efficient, so ``sharp_weight`` should dominate unless your model has
        demonstrated consistent CLV.

        Parameters
        ----------
        sharp_fair_prob : float
            Fair probability from sharp book(s).
        model_fair_prob : float
            Fair probability from proprietary model or learning agent.
        sharp_weight : float
            Weight for sharp book signal (default 0.75).
        model_weight : float
            Weight for own model signal (default 0.25).

        Returns
        -------
        float
            Combined fair probability estimate.

        References
        ----------
        - Manski, C.F. (2006). "Interpreting the Predictions of Prediction
          Markets." *Economics Letters*, 91(3), 425-429.
        """
        total_w = sharp_weight + model_weight
        if total_w <= 0:
            return sharp_fair_prob

        combined = (sharp_fair_prob * sharp_weight + model_fair_prob * model_weight) / total_w
        return max(0.0, min(1.0, combined))


# ══════════════════════════════════════════════════════════════════════════════
#  3. EDGE CONFIDENCE SCORING
# ══════════════════════════════════════════════════════════════════════════════

class EdgeConfidence:
    """Score the confidence in an edge estimate.

    Not all "5% edges" are created equal. A 5% edge backed by multiple
    sharp books, deep liquidity, and near game start is much more reliable
    than a 5% edge from a single soft book, thin market, 3 days out.

    The confidence score is a weighted combination of individual factor
    scores, each mapped to [0, 1]. The final score multiplies the raw edge
    for position sizing purposes.
    """

    def __init__(self, config: EdgeModelConfig | None = None):
        self.config = config or EdgeModelConfig()
        self.weights = self.config.confidence_weights

    def score(
        self,
        liquidity_usd: float = 0.0,
        num_agreeing_books: int = 1,
        total_books: int = 1,
        hours_to_event: float = 24.0,
        market_type: str = "h2h",
        historical_win_rate: float | None = None,
        historical_sample_size: int = 0,
    ) -> float:
        """Compute an overall confidence score for an edge.

        Parameters
        ----------
        liquidity_usd : float
            Total orderbook depth in USD on the target side.
        num_agreeing_books : int
            Number of sharp books that agree on the direction of the edge.
        total_books : int
            Total number of books checked.
        hours_to_event : float
            Hours until the event starts.
        market_type : str
            One of 'h2h', 'spread', 'total'.
        historical_win_rate : float or None
            Win rate from learning agent for similar trades, if available.
        historical_sample_size : int
            Number of historical trades in the matching bucket.

        Returns
        -------
        float
            Confidence score in [0, 1]. Multiply raw edge by this for sizing.
        """
        factors = {}

        # 1. Liquidity score: log-scale, caps at $100k
        if liquidity_usd > 0:
            factors["liquidity"] = min(1.0, math.log1p(liquidity_usd) / math.log1p(100_000))
        else:
            factors["liquidity"] = 0.3  # unknown liquidity gets low-but-nonzero

        # 2. Book agreement: what fraction of checked books show the edge
        if total_books > 0:
            agreement_ratio = num_agreeing_books / total_books
            # Boost: if 3+ books agree, score high
            factors["book_agreement"] = min(1.0, agreement_ratio * 1.2)
        else:
            factors["book_agreement"] = 0.5

        # 3. Time to event: closer = better. Exponential decay.
        if hours_to_event <= 2.0:
            factors["time_to_event"] = 1.0  # near close, maximum confidence
        elif hours_to_event <= 6.0:
            factors["time_to_event"] = 0.85
        elif hours_to_event <= 24.0:
            factors["time_to_event"] = 0.65
        elif hours_to_event <= 48.0:
            factors["time_to_event"] = 0.45
        else:
            factors["time_to_event"] = 0.30

        # 4. Market type reliability
        factors["market_type"] = MARKET_TYPE_RELIABILITY.get(market_type, 0.50)

        # 5. Historical accuracy from learning agent
        if historical_win_rate is not None and historical_sample_size >= 20:
            # Compare realized win rate to expected (entry price as proxy)
            # If historically profitable, boost confidence
            factors["historical_accuracy"] = min(1.0, max(0.2, historical_win_rate * 1.5))
        else:
            factors["historical_accuracy"] = 0.5  # neutral prior

        # Weighted average (not geometric mean for simplicity and interpretability)
        w = self.weights
        weight_map = {
            "liquidity": w.liquidity,
            "book_agreement": w.book_agreement,
            "time_to_event": w.time_to_event,
            "market_type": w.market_type,
            "historical_accuracy": w.historical_accuracy,
        }

        total_weight = sum(weight_map.values())
        if total_weight <= 0:
            return 0.5

        confidence = sum(
            factors[k] * weight_map[k]
            for k in factors
            if k in weight_map
        ) / total_weight

        return max(0.0, min(1.0, confidence))


# ══════════════════════════════════════════════════════════════════════════════
#  4. KELLY CRITERION
# ══════════════════════════════════════════════════════════════════════════════

class KellyCriterion:
    """Proper Kelly criterion implementation with multiple variants.

    The Kelly criterion (Kelly 1956) maximizes the expected logarithm of
    wealth (geometric growth rate). It is optimal for a bettor with log
    utility and accurate probability estimates.

    In practice, the full Kelly fraction is dangerously aggressive because:
    1. Our probability estimates have non-trivial estimation error
    2. We're making multiple simultaneous bets (not sequential)
    3. Ruin risk is non-zero and our bankroll is finite

    This class provides fractional Kelly, estimation-error-adjusted Kelly
    (Thorp 2006), and simultaneous Kelly for multiple concurrent bets.

    References
    ----------
    - Kelly, J.L. (1956). "A New Interpretation of Information Rate."
    - Thorp, E.O. (2006). "The Kelly Criterion in Blackjack, Sports
      Betting, and the Stock Market."
    - MacLean, L.C., Thorp, E.O., Ziemba, W.T. (2011). "The Kelly Capital
      Growth Investment Criterion."
    """

    def __init__(self, config: EdgeModelConfig | None = None):
        self.config = config or EdgeModelConfig()
        self.kelly_cfg = self.config.kelly

    def full_kelly(self, fair_prob: float, price: float) -> float:
        """Standard full Kelly fraction.

        f* = (b * p - q) / b

        where:
        - b = (1 / price) - 1 = net decimal odds (payout per unit staked)
        - p = fair_prob (true win probability)
        - q = 1 - p (true loss probability)

        Parameters
        ----------
        fair_prob : float
            Estimated true probability of winning (0-1).
        price : float
            Entry price / cost per share on Polymarket (0-1).

        Returns
        -------
        float
            Optimal fraction of bankroll to wager (can be negative if
            edge is negative — we return 0 in that case).
        """
        if price <= 0 or price >= 1 or fair_prob <= 0 or fair_prob >= 1:
            return 0.0

        b = (1.0 / price) - 1.0  # net odds
        q = 1.0 - fair_prob

        if b <= 0:
            return 0.0

        f = (b * fair_prob - q) / b
        return max(0.0, f)

    def fractional_kelly(
        self,
        fair_prob: float,
        price: float,
        fraction: float | None = None,
    ) -> float:
        """Fractional Kelly — scales down the full Kelly by a constant.

        Common choices:
        - Half-Kelly (0.50): Captures 75% of the growth rate at 50% of the variance
        - Quarter-Kelly (0.25): Captures 56% of the growth rate at 25% of the variance

        Parameters
        ----------
        fair_prob : float
            True win probability.
        price : float
            Entry price.
        fraction : float
            Fraction of full Kelly (default from config, typically 0.25).

        Returns
        -------
        float
            Fractional Kelly bet size as fraction of bankroll.
        """
        if fraction is None:
            fraction = self.kelly_cfg.default_fraction

        f_full = self.full_kelly(fair_prob, price)
        return f_full * fraction

    def estimation_error_kelly(
        self,
        fair_prob: float,
        price: float,
        edge_uncertainty: float = 0.0,
        fraction: float | None = None,
    ) -> float:
        """Kelly with estimation error adjustment per Thorp (2006).

        When the edge estimate has uncertainty (which it always does), the
        optimal Kelly fraction is reduced. Thorp showed that if the edge
        has standard deviation sigma, the adjusted fraction is:

            f_adj = f_kelly * (1 - penalty * sigma / edge)

        where penalty is a tuning parameter. When sigma is large relative
        to edge, we should bet very conservatively or not at all.

        Parameters
        ----------
        fair_prob : float
            Estimated true probability.
        price : float
            Entry price.
        edge_uncertainty : float
            Standard deviation of the edge estimate (in probability units).
            E.g., if edge = 5% and uncertainty = 2%, pass 0.02.
        fraction : float or None
            Base Kelly fraction before estimation-error adjustment.

        Returns
        -------
        float
            Adjusted Kelly fraction.

        References
        ----------
        - Thorp, E.O. (2006). Section 4: "Estimation error and the
          practical Kelly criterion."
        """
        if fraction is None:
            fraction = self.kelly_cfg.default_fraction

        f_base = self.full_kelly(fair_prob, price)
        if f_base <= 0:
            return 0.0

        edge = fair_prob - price
        if edge <= 0:
            return 0.0

        penalty = self.kelly_cfg.estimation_error_penalty

        if edge_uncertainty > 0 and edge > 0:
            shrinkage = 1.0 - penalty * (edge_uncertainty / edge)
            shrinkage = max(0.0, shrinkage)
        else:
            shrinkage = 1.0

        return f_base * fraction * shrinkage

    def simultaneous_kelly(
        self,
        bets: list[dict],
        bankroll: float,
    ) -> list[float]:
        """Approximate simultaneous Kelly for multiple concurrent bets.

        When placing N bets at once, the independent Kelly fractions overstate
        total exposure because they each assume the full bankroll is available.
        The exact simultaneous Kelly requires solving a constrained optimization;
        here we use an approximation:

        1. Compute independent Kelly fractions for each bet.
        2. If the sum of fractions exceeds a threshold (e.g. 0.5), scale them
           down proportionally so total = threshold.
        3. Apply a correlation penalty: if bets are correlated (e.g. same game),
           reduce further.

        Parameters
        ----------
        bets : list[dict]
            Each bet dict must have:
            - 'fair_prob': float
            - 'price': float
            - 'confidence': float (0-1, from EdgeConfidence)
        bankroll : float
            Current bankroll in USDC.

        Returns
        -------
        list[float]
            USDC amount to bet on each opportunity.
        """
        if not bets or bankroll <= 0:
            return [0.0] * len(bets)

        fractions = []
        for bet in bets:
            fp = bet.get("fair_prob", 0)
            px = bet.get("price", 0)
            conf = bet.get("confidence", 1.0)

            f = self.fractional_kelly(fp, px)
            # Scale by confidence
            f *= conf
            fractions.append(f)

        # Cap total exposure
        max_total = self.kelly_cfg.max_fraction
        total_f = sum(fractions)

        if total_f > max_total and total_f > 0:
            scale = max_total / total_f
            fractions = [f * scale for f in fractions]

        # Apply correlation penalty
        rho = self.kelly_cfg.simultaneous_correlation
        if len(bets) > 1 and rho > 0:
            # Heuristic: reduce each fraction by rho * (N-1) / N
            n = len(bets)
            penalty = 1.0 - rho * (n - 1) / n
            penalty = max(0.3, penalty)  # floor at 30%
            fractions = [f * penalty for f in fractions]

        # Convert to USDC amounts
        amounts = []
        for f in fractions:
            usdc = f * bankroll
            usdc = min(usdc, self.kelly_cfg.max_bet_usdc)
            if usdc < self.kelly_cfg.min_bet_usdc:
                usdc = 0.0
            amounts.append(round(usdc, 2))

        return amounts

    def position_size(
        self,
        fair_prob: float,
        price: float,
        bankroll: float,
        confidence: float = 1.0,
        edge_uncertainty: float = 0.0,
        fraction: float | None = None,
    ) -> float:
        """All-in-one position sizing: Kelly + confidence + estimation error.

        Parameters
        ----------
        fair_prob : float
            Estimated true probability.
        price : float
            Entry price on Polymarket.
        bankroll : float
            Current bankroll in USDC.
        confidence : float
            Edge confidence score (0-1).
        edge_uncertainty : float
            Standard deviation of edge estimate.
        fraction : float or None
            Kelly fraction override.

        Returns
        -------
        float
            USDC amount to bet.
        """
        f = self.estimation_error_kelly(
            fair_prob, price, edge_uncertainty, fraction
        )

        # Apply confidence scaling
        f *= confidence

        usdc = f * bankroll
        usdc = min(usdc, self.kelly_cfg.max_bet_usdc)

        if usdc < self.kelly_cfg.min_bet_usdc:
            return 0.0

        return round(usdc, 2)


# ══════════════════════════════════════════════════════════════════════════════
#  5. EXPECTED VALUE CALCULATIONS
# ══════════════════════════════════════════════════════════════════════════════

class EVCalculator:
    """Expected value calculations for prediction market trades.

    Provides simple EV, risk-adjusted EV (Sharpe-like), expected growth
    rate (what Kelly maximizes), and ROI projections.
    """

    @staticmethod
    def simple_ev(fair_prob: float, price: float, stake: float = 1.0) -> float:
        """Simple expected value of a binary bet.

        EV = fair_prob * (payout - stake) - (1 - fair_prob) * stake
           = fair_prob * (1/price - 1) * stake - (1 - fair_prob) * stake

        On Polymarket, buying at price p and winning pays 1.0 per share.
        Cost per share = price. Profit if win = (1 - price). Loss if lose = price.

        Parameters
        ----------
        fair_prob : float
            True probability of the outcome.
        price : float
            Entry price per share (0-1).
        stake : float
            Number of USDC wagered.

        Returns
        -------
        float
            Expected profit in USDC.
        """
        if price <= 0 or price >= 1:
            return 0.0

        profit_if_win = (1.0 - price) * stake / price  # shares * (1 - price)
        # Actually: we buy (stake / price) shares. If win, each share pays $1.
        # Total payout = stake / price. Profit = stake / price - stake = stake * (1/price - 1)
        shares = stake / price
        payout_win = shares * 1.0
        profit_win = payout_win - stake
        loss_lose = stake

        ev = fair_prob * profit_win - (1.0 - fair_prob) * loss_lose
        return ev

    @staticmethod
    def risk_adjusted_ev(fair_prob: float, price: float) -> float:
        """Risk-adjusted EV: EV per unit of standard deviation.

        This is a Sharpe-like metric for a single trade. Higher values
        indicate better risk/reward.

        RAE = EV / sqrt(Var) = (p * profit - q * loss) / sqrt(p*q*(profit+loss)^2)

        Parameters
        ----------
        fair_prob : float
            True probability.
        price : float
            Entry price.

        Returns
        -------
        float
            Risk-adjusted EV (dimensionless).
        """
        if price <= 0 or price >= 1 or fair_prob <= 0 or fair_prob >= 1:
            return 0.0

        profit = (1.0 / price) - 1.0  # net profit per $1 if win
        loss = 1.0                      # loss per $1 if lose
        p = fair_prob
        q = 1.0 - p

        ev = p * profit - q * loss
        variance = p * q * (profit + loss) ** 2
        if variance <= 0:
            return 0.0

        return ev / math.sqrt(variance)

    @staticmethod
    def expected_growth_rate(
        fair_prob: float,
        price: float,
        kelly_fraction: float,
    ) -> float:
        """Expected log-growth rate (what Kelly maximizes).

        G(f) = p * log(1 + f * b) + q * log(1 - f)

        where f = fraction of bankroll wagered, b = net odds.

        At the full Kelly fraction f*, G is maximized. At zero or full
        bankroll, G = 0 or -inf respectively.

        Parameters
        ----------
        fair_prob : float
            True probability.
        price : float
            Entry price.
        kelly_fraction : float
            Fraction of bankroll wagered.

        Returns
        -------
        float
            Expected log-growth rate per bet.

        References
        ----------
        - Kelly (1956); Thorp (2006).
        """
        if price <= 0 or price >= 1 or fair_prob <= 0 or fair_prob >= 1:
            return 0.0
        if kelly_fraction <= 0 or kelly_fraction >= 1:
            return 0.0

        b = (1.0 / price) - 1.0
        p = fair_prob
        q = 1.0 - p
        f = kelly_fraction

        # Guard against log of non-positive
        arg1 = 1.0 + f * b
        arg2 = 1.0 - f
        if arg1 <= 0 or arg2 <= 0:
            return float("-inf")

        return p * math.log(arg1) + q * math.log(arg2)

    @staticmethod
    def roi_per_dollar(fair_prob: float, price: float) -> float:
        """Expected return per $1 risked.

        ROI = (fair_prob / price) - 1

        This is the percentage return you'd expect on average per dollar
        placed at this price given the true probability.

        Parameters
        ----------
        fair_prob : float
            True probability.
        price : float
            Entry price.

        Returns
        -------
        float
            Expected ROI as a decimal (0.05 = 5%).
        """
        if price <= 0:
            return 0.0
        return (fair_prob / price) - 1.0

    @staticmethod
    def time_adjusted_roi(
        fair_prob: float,
        price: float,
        hours_to_resolution: float,
    ) -> float:
        """Annualized ROI accounting for resolution time.

        Useful for comparing trades with different time horizons.
        A 5% edge that resolves in 2 hours is much better than 5% in 7 days.

        Parameters
        ----------
        fair_prob : float
            True probability.
        price : float
            Entry price.
        hours_to_resolution : float
            Expected hours until the market resolves.

        Returns
        -------
        float
            Annualized ROI.
        """
        roi = EVCalculator.roi_per_dollar(fair_prob, price)
        if hours_to_resolution <= 0:
            return roi

        # Annualize assuming continuous compounding
        periods_per_year = 8760.0 / hours_to_resolution  # 8760 hours/year
        return (1.0 + roi) ** periods_per_year - 1.0


# ══════════════════════════════════════════════════════════════════════════════
#  6. CLOSING LINE VALUE (CLV) TRACKER
# ══════════════════════════════════════════════════════════════════════════════

class CLVTracker:
    """Track Closing Line Value — the gold standard for measuring long-term
    sports betting profitability.

    CLV measures whether you consistently get better odds than the market's
    final (closing) line. Positive CLV over a large sample is the single
    best predictor of a profitable bettor, more reliable than short-term
    win rate or P&L.

    CLV = (entry_fair_prob - closing_fair_prob) / closing_fair_prob

    Interpretation:
    - CLV > 0: You're beating the closing line (sharp bettor)
    - CLV < 0: You're losing to the closing line (the market was ahead of you)
    - CLV ~ 0: You're roughly in line with the market (break-even before vig)

    Usage
    -----
    1. When opening a position, call ``record_entry()`` with the current
       sharp book fair probability.
    2. Periodically (or just before game start), call ``record_snapshot()``
       to capture intermediate line movements.
    3. Just before the game starts, call ``record_closing()`` with the final
       sharp book fair probability.
    4. After the game, call ``get_clv()`` to compute CLV for that trade.

    References
    ----------
    - Pinnacle Sports (2019). "Closing Line Value: The Most Important
      Metric for Sports Bettors."
    - Buchdahl, J. (2016). *Squares and Sharps, Suckers and Sharks*.
    """

    def __init__(self, config: EdgeModelConfig | None = None, data_dir: str = "data"):
        self.config = config or EdgeModelConfig()
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, dict] = {}  # token_id -> CLV record
        self._clv_history_path = self.data_dir / "clv_history.json"
        self._clv_history: list[dict] = []
        self._load_history()

    def _load_history(self):
        """Load CLV history from disk."""
        if self._clv_history_path.exists():
            try:
                with open(self._clv_history_path, "r") as f:
                    data = json.load(f)
                self._clv_history = data.get("records", [])
            except (json.JSONDecodeError, KeyError):
                self._clv_history = []

    def _save_history(self):
        """Persist CLV history."""
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "count": len(self._clv_history),
            "records": self._clv_history,
        }
        tmp = self._clv_history_path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(payload, f, indent=2)
        tmp.replace(self._clv_history_path)

    def record_entry(
        self,
        token_id: str,
        entry_fair_prob: float,
        entry_price: float,
        sport: str = "",
        bookmaker: str = "",
        commence_time: str = "",
    ):
        """Record the sharp book fair probability at the time of entry.

        Parameters
        ----------
        token_id : str
            Polymarket token ID.
        entry_fair_prob : float
            Fair probability from sharp book at entry time.
        entry_price : float
            Price paid on Polymarket.
        sport : str
            Sport key.
        bookmaker : str
            Sharp book used.
        commence_time : str
            ISO-8601 game start time.
        """
        self._records[token_id] = {
            "token_id": token_id,
            "entry_fair_prob": entry_fair_prob,
            "entry_price": entry_price,
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "sport": sport,
            "bookmaker": bookmaker,
            "commence_time": commence_time,
            "snapshots": [],
            "closing_fair_prob": None,
            "closing_time": None,
        }
        log.debug("CLV entry recorded for %s: fair=%.4f price=%.4f",
                   token_id, entry_fair_prob, entry_price)

    def record_snapshot(self, token_id: str, current_fair_prob: float):
        """Record an intermediate fair probability snapshot.

        Parameters
        ----------
        token_id : str
            Polymarket token ID.
        current_fair_prob : float
            Current sharp book fair probability.
        """
        rec = self._records.get(token_id)
        if rec is None:
            return

        rec["snapshots"].append({
            "time": datetime.now(timezone.utc).isoformat(),
            "fair_prob": current_fair_prob,
        })

    def record_closing(self, token_id: str, closing_fair_prob: float):
        """Record the closing line (final sharp book fair prob before game start).

        Parameters
        ----------
        token_id : str
            Polymarket token ID.
        closing_fair_prob : float
            Fair probability from sharp book at or just before game start.
        """
        rec = self._records.get(token_id)
        if rec is None:
            return

        rec["closing_fair_prob"] = closing_fair_prob
        rec["closing_time"] = datetime.now(timezone.utc).isoformat()
        log.debug("CLV closing recorded for %s: closing=%.4f (entry was %.4f)",
                   token_id, closing_fair_prob, rec["entry_fair_prob"])

    def get_clv(self, token_id: str) -> float | None:
        """Compute CLV for a trade.

        CLV = (entry_fair_prob - closing_fair_prob) / closing_fair_prob

        Positive CLV means we got a better line than closing.

        Parameters
        ----------
        token_id : str
            Polymarket token ID.

        Returns
        -------
        float or None
            CLV as a decimal (0.05 = 5% CLV), or None if closing line
            has not been recorded.
        """
        rec = self._records.get(token_id)
        if rec is None or rec["closing_fair_prob"] is None:
            return None

        closing = rec["closing_fair_prob"]
        if closing <= 0:
            return None

        return (rec["entry_fair_prob"] - closing) / closing

    def finalize(self, token_id: str, won: bool | None = None):
        """Move a completed trade from active records to history.

        Parameters
        ----------
        token_id : str
            Polymarket token ID.
        won : bool or None
            Whether the bet won (for correlation analysis).
        """
        rec = self._records.pop(token_id, None)
        if rec is None:
            return

        clv = None
        if rec["closing_fair_prob"] is not None and rec["closing_fair_prob"] > 0:
            clv = (rec["entry_fair_prob"] - rec["closing_fair_prob"]) / rec["closing_fair_prob"]

        rec["clv"] = clv
        rec["won"] = won
        self._clv_history.append(rec)
        self._save_history()

        log.info("CLV finalized for %s: CLV=%.4f%% won=%s",
                 token_id, (clv or 0) * 100, won)

    def aggregate_clv(self) -> dict:
        """Compute aggregate CLV statistics from history.

        Returns
        -------
        dict
            Keys: 'mean_clv', 'median_clv', 'count', 'positive_pct',
            'by_sport', 'by_bookmaker'.
        """
        records_with_clv = [r for r in self._clv_history if r.get("clv") is not None]
        if not records_with_clv:
            return {"mean_clv": 0.0, "median_clv": 0.0, "count": 0,
                    "positive_pct": 0.0, "by_sport": {}, "by_bookmaker": {}}

        clvs = [r["clv"] for r in records_with_clv]
        clvs_sorted = sorted(clvs)
        n = len(clvs)

        # By sport
        sport_clvs: dict[str, list[float]] = {}
        for r in records_with_clv:
            sport = r.get("sport", "unknown")
            sport_clvs.setdefault(sport, []).append(r["clv"])

        by_sport = {}
        for sport, sclvs in sport_clvs.items():
            by_sport[sport] = {
                "mean_clv": sum(sclvs) / len(sclvs),
                "count": len(sclvs),
                "positive_pct": sum(1 for c in sclvs if c > 0) / len(sclvs),
            }

        # By bookmaker
        bk_clvs: dict[str, list[float]] = {}
        for r in records_with_clv:
            bk = r.get("bookmaker", "unknown")
            bk_clvs.setdefault(bk, []).append(r["clv"])

        by_bk = {}
        for bk, bclvs in bk_clvs.items():
            by_bk[bk] = {
                "mean_clv": sum(bclvs) / len(bclvs),
                "count": len(bclvs),
            }

        return {
            "mean_clv": sum(clvs) / n,
            "median_clv": clvs_sorted[n // 2],
            "count": n,
            "positive_pct": sum(1 for c in clvs if c > 0) / n,
            "by_sport": by_sport,
            "by_bookmaker": by_bk,
        }


# ══════════════════════════════════════════════════════════════════════════════
#  7. EDGE DECAY MODEL
# ══════════════════════════════════════════════════════════════════════════════

class EdgeDecay:
    """Model for time-based edge decay.

    Edges discovered far from game start are less reliable because:
    1. Sharp books will continue to adjust their lines as new information
       arrives (injuries, weather, lineup changes).
    2. Polymarket prices will converge toward fair value as more informed
       traders enter.
    3. The current mispricing might be *correct* given information not yet
       reflected in the sharp books.

    The decay function is exponential with a close-window override:

        if hours_to_start <= close_window:
            decay_factor = 1.0
        else:
            excess = hours_to_start - close_window
            decay_factor = min_factor + (1 - min_factor) * exp(-excess / half_life)

    This produces:
    - factor ~1.0 near game start (edges are most reliable)
    - factor ~0.6-0.7 at 6 hours out
    - factor ~0.4 at 24+ hours out (edges are least reliable)
    """

    def __init__(self, config: EdgeModelConfig | None = None):
        self.config = config or EdgeModelConfig()
        self.decay_cfg = self.config.decay

    def decay_factor(self, hours_to_start: float) -> float:
        """Compute the edge decay factor.

        Parameters
        ----------
        hours_to_start : float
            Hours until the event starts. Can be negative (game already
            started, but market still open) — returns 1.0 in that case.

        Returns
        -------
        float
            Decay factor in [min_factor, 1.0].
        """
        if hours_to_start <= self.decay_cfg.close_window_hours:
            return 1.0

        excess = hours_to_start - self.decay_cfg.close_window_hours
        hl = self.decay_cfg.half_life_hours
        mn = self.decay_cfg.min_factor

        return mn + (1.0 - mn) * math.exp(-excess / hl)

    def adjusted_edge(
        self,
        raw_edge_pct: float,
        hours_to_start: float,
    ) -> float:
        """Apply time decay to a raw edge percentage.

        Parameters
        ----------
        raw_edge_pct : float
            Raw edge in percent.
        hours_to_start : float
            Hours until event.

        Returns
        -------
        float
            Decayed edge in percent.
        """
        return raw_edge_pct * self.decay_factor(hours_to_start)


# ══════════════════════════════════════════════════════════════════════════════
#  8. MAIN EDGE CALCULATOR (INTEGRATION CLASS)
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class EdgeResult:
    """Complete result from the edge calculator for a single outcome.

    Attributes
    ----------
    outcome : str
        Outcome name (e.g. "Arsenal", "Yes", "Over").
    token_id : str
        Polymarket token ID.
    polymarket_price : float
        Current Polymarket price.
    fair_prob : float
        Estimated fair probability.
    raw_edge : float
        Raw edge = fair_prob - polymarket_price.
    raw_edge_pct : float
        Raw edge as percentage: (fair_prob - price) / price * 100.
    decayed_edge_pct : float
        Edge after time decay.
    confidence : float
        Edge confidence score (0-1).
    effective_edge_pct : float
        Final edge used for sizing: decayed_edge_pct * confidence.
    size_usdc : float
        Recommended position size in USDC.
    side : str
        "BUY" or "SELL".
    market_type : str
        "h2h", "spread", or "total".
    ev_per_dollar : float
        Simple expected value per $1 risked.
    risk_adjusted_ev : float
        Risk-adjusted EV (Sharpe-like per trade).
    expected_growth_rate : float
        Expected log-growth rate at the recommended Kelly fraction.
    kelly_fraction : float
        Recommended Kelly fraction of bankroll.
    overround_method : str
        Which overround removal method was used.
    clv : float or None
        Closing line value if available.
    line : float or None
        Spread/total line if applicable.
    """
    outcome: str
    token_id: str
    polymarket_price: float
    fair_prob: float
    raw_edge: float
    raw_edge_pct: float
    decayed_edge_pct: float
    confidence: float
    effective_edge_pct: float
    size_usdc: float
    side: str
    market_type: str
    ev_per_dollar: float
    risk_adjusted_ev: float
    expected_growth_rate: float
    kelly_fraction: float
    overround_method: str
    clv: float | None = None
    line: float | None = None


class EdgeCalculator:
    """Main integration class that produces edge + confidence + sizing.

    Replaces the inline edge calculation in ``matcher.py`` with a rigorous,
    configurable pipeline:

    1. Remove overround using the appropriate method for the sport
    2. Estimate fair probability (single or multi-book)
    3. Compute raw edge
    4. Apply time decay
    5. Score edge confidence
    6. Calculate effective edge (decayed * confidence)
    7. Size using Kelly criterion with estimation error adjustment
    8. Compute EV metrics

    Usage
    -----
    >>> calc = EdgeCalculator()
    >>> results = calc.calculate_h2h_edges(
    ...     pm_prices=[0.35, 0.55, 0.15],
    ...     pm_outcomes=["Home", "Away", "Draw"],
    ...     pm_token_ids=["tok1", "tok2", "tok3"],
    ...     odds_outcomes={"Home": 2.40, "Away": 1.90, "Draw": 3.50},
    ...     sport="epl",
    ...     bankroll=500.0,
    ...     hours_to_start=6.0,
    ... )
    """

    def __init__(self, config: EdgeModelConfig | None = None):
        self.config = config or EdgeModelConfig()
        self.estimator = FairProbEstimator(self.config)
        self.confidence_scorer = EdgeConfidence(self.config)
        self.kelly = KellyCriterion(self.config)
        self.decay = EdgeDecay(self.config)
        self.ev = EVCalculator()

    def calculate_h2h_edges(
        self,
        pm_prices: list[float],
        pm_outcomes: list[str],
        pm_token_ids: list[str],
        odds_outcomes: dict[str, float],
        sport: str = "",
        bankroll: float = 500.0,
        hours_to_start: float = 24.0,
        liquidity_usd: float = 0.0,
        num_agreeing_books: int = 1,
        total_books: int = 1,
        historical_win_rate: float | None = None,
        historical_sample_size: int = 0,
    ) -> list[EdgeResult]:
        """Calculate edges for all outcomes in an h2h market.

        Parameters
        ----------
        pm_prices : list[float]
            Polymarket prices for each outcome.
        pm_outcomes : list[str]
            Outcome names.
        pm_token_ids : list[str]
            Token IDs for each outcome.
        odds_outcomes : dict[str, float]
            Mapping of outcome name -> decimal odds from sharp book.
        sport : str
            Sport key (for overround method selection).
        bankroll : float
            Current bankroll in USDC.
        hours_to_start : float
            Hours until game starts.
        liquidity_usd : float
            Orderbook depth.
        num_agreeing_books : int
            Number of sharp books showing the edge.
        total_books : int
            Total books checked.
        historical_win_rate : float or None
            From learning agent.
        historical_sample_size : int
            Sample size for historical win rate.

        Returns
        -------
        list[EdgeResult]
            Edge results for outcomes that pass the minimum edge filter.
        """
        if not pm_prices or not odds_outcomes:
            return []

        # Step 1: Build implied probabilities from odds
        # Match odds outcomes to pm outcomes
        odds_names = list(odds_outcomes.keys())
        implied_probs = [1.0 / odds_outcomes[n] if odds_outcomes[n] > 0 else 0.0
                         for n in odds_names]

        # Step 2: Determine overround method and compute fair probs
        method = SPORT_OVERROUND_DEFAULTS.get(sport, self.config.default_overround_method)
        fair_probs_list = OverroundRemoval.remove(
            implied_probs,
            method=method,
            max_iter=self.config.shin_max_iterations,
            tol=self.config.shin_tolerance,
        )

        # Build fair prob lookup
        fair_prob_map: dict[str, float] = {}
        for i, name in enumerate(odds_names):
            if i < len(fair_probs_list):
                fair_prob_map[name] = fair_probs_list[i]

        # Step 3: For each Polymarket outcome, find matching fair prob and compute edge
        results = []
        for idx in range(len(pm_prices)):
            if idx >= len(pm_outcomes) or idx >= len(pm_token_ids):
                break

            pm_price = pm_prices[idx]
            pm_outcome = pm_outcomes[idx]
            token_id = pm_token_ids[idx]

            if pm_price <= 0 or pm_price >= 1:
                continue

            # Find matching fair prob
            fair_prob = self._match_outcome_to_fair_prob(
                pm_outcome, fair_prob_map
            )
            if fair_prob is None or fair_prob <= 0:
                continue

            result = self._compute_edge_result(
                outcome=pm_outcome,
                token_id=token_id,
                pm_price=pm_price,
                fair_prob=fair_prob,
                market_type="h2h",
                method=method,
                sport=sport,
                bankroll=bankroll,
                hours_to_start=hours_to_start,
                liquidity_usd=liquidity_usd,
                num_agreeing_books=num_agreeing_books,
                total_books=total_books,
                historical_win_rate=historical_win_rate,
                historical_sample_size=historical_sample_size,
            )
            if result is not None:
                results.append(result)

        return results

    def calculate_edge_from_fair_prob(
        self,
        outcome: str,
        token_id: str,
        pm_price: float,
        fair_prob: float,
        market_type: str = "h2h",
        sport: str = "",
        bankroll: float = 500.0,
        hours_to_start: float = 24.0,
        liquidity_usd: float = 0.0,
        num_agreeing_books: int = 1,
        total_books: int = 1,
        historical_win_rate: float | None = None,
        historical_sample_size: int = 0,
        line: float | None = None,
    ) -> EdgeResult | None:
        """Calculate edge given a pre-computed fair probability.

        This is the simplest entry point when you already have a fair
        probability (e.g. from the existing matcher.py pipeline). It
        wraps the full confidence/decay/Kelly/EV pipeline.

        Parameters
        ----------
        outcome : str
            Outcome name.
        token_id : str
            Polymarket token ID.
        pm_price : float
            Polymarket price.
        fair_prob : float
            Pre-computed fair probability.
        market_type : str
            Market type.
        sport : str
            Sport key.
        bankroll : float
            Current bankroll.
        hours_to_start : float
            Hours to event.
        liquidity_usd : float
            Orderbook depth.
        num_agreeing_books : int
            Agreeing sharp books.
        total_books : int
            Total books.
        historical_win_rate : float or None
            From learning agent.
        historical_sample_size : int
            Sample size.
        line : float or None
            Spread/total line.

        Returns
        -------
        EdgeResult or None
            Full edge result, or None if below minimum threshold.
        """
        method = SPORT_OVERROUND_DEFAULTS.get(sport, self.config.default_overround_method)
        return self._compute_edge_result(
            outcome=outcome,
            token_id=token_id,
            pm_price=pm_price,
            fair_prob=fair_prob,
            market_type=market_type,
            method=method,
            sport=sport,
            bankroll=bankroll,
            hours_to_start=hours_to_start,
            liquidity_usd=liquidity_usd,
            num_agreeing_books=num_agreeing_books,
            total_books=total_books,
            historical_win_rate=historical_win_rate,
            historical_sample_size=historical_sample_size,
            line=line,
        )

    def _compute_edge_result(
        self,
        outcome: str,
        token_id: str,
        pm_price: float,
        fair_prob: float,
        market_type: str,
        method: str,
        sport: str,
        bankroll: float,
        hours_to_start: float,
        liquidity_usd: float,
        num_agreeing_books: int,
        total_books: int,
        historical_win_rate: float | None,
        historical_sample_size: int,
        line: float | None = None,
    ) -> EdgeResult | None:
        """Core computation: edge + confidence + decay + Kelly + EV."""

        # Raw edge
        raw_edge = fair_prob - pm_price
        raw_edge_pct = (raw_edge / pm_price * 100) if pm_price > 0 else 0.0
        side = "BUY" if raw_edge > 0 else "SELL"

        # Filter
        if abs(raw_edge_pct) < self.config.min_edge_pct:
            return None
        if abs(raw_edge_pct) > self.config.max_edge_pct:
            return None

        # Time decay
        decayed_edge_pct = self.decay.adjusted_edge(abs(raw_edge_pct), hours_to_start)
        if side == "SELL":
            decayed_edge_pct = -decayed_edge_pct

        # Confidence
        confidence = self.confidence_scorer.score(
            liquidity_usd=liquidity_usd,
            num_agreeing_books=num_agreeing_books,
            total_books=total_books,
            hours_to_event=hours_to_start,
            market_type=market_type,
            historical_win_rate=historical_win_rate,
            historical_sample_size=historical_sample_size,
        )

        # Effective edge
        effective_edge_pct = abs(decayed_edge_pct) * confidence
        if side == "SELL":
            effective_edge_pct = -effective_edge_pct

        # Kelly sizing (only for BUY side)
        kelly_f = 0.0
        size_usdc = 0.0
        if side == "BUY" and effective_edge_pct > 0:
            # Reconstruct adjusted fair prob for Kelly
            adjusted_fair = pm_price * (1.0 + effective_edge_pct / 100.0)
            adjusted_fair = min(adjusted_fair, 0.99)

            kelly_f = self.kelly.fractional_kelly(adjusted_fair, pm_price)
            size_usdc = self.kelly.position_size(
                fair_prob=adjusted_fair,
                price=pm_price,
                bankroll=bankroll,
                confidence=confidence,
            )

        # EV metrics
        ev_per_dollar = self.ev.roi_per_dollar(fair_prob, pm_price)
        risk_adj_ev = self.ev.risk_adjusted_ev(fair_prob, pm_price)
        growth_rate = 0.0
        if kelly_f > 0:
            growth_rate = self.ev.expected_growth_rate(fair_prob, pm_price, kelly_f)

        return EdgeResult(
            outcome=outcome,
            token_id=token_id,
            polymarket_price=pm_price,
            fair_prob=fair_prob,
            raw_edge=raw_edge,
            raw_edge_pct=raw_edge_pct,
            decayed_edge_pct=decayed_edge_pct,
            confidence=confidence,
            effective_edge_pct=effective_edge_pct,
            size_usdc=size_usdc,
            side=side,
            market_type=market_type,
            ev_per_dollar=ev_per_dollar,
            risk_adjusted_ev=risk_adj_ev,
            expected_growth_rate=growth_rate,
            kelly_fraction=kelly_f,
            overround_method=method,
            line=line,
        )

    @staticmethod
    def _match_outcome_to_fair_prob(
        pm_outcome: str,
        fair_prob_map: dict[str, float],
    ) -> float | None:
        """Match a Polymarket outcome name to a fair probability from the odds map.

        Uses case-insensitive matching and common alias resolution.
        """
        pm_lower = pm_outcome.lower().strip()

        # Direct match
        for name, fp in fair_prob_map.items():
            if name.lower().strip() == pm_lower:
                return fp

        # Partial/fuzzy match
        from difflib import SequenceMatcher
        best_match = None
        best_ratio = 0.0
        for name, fp in fair_prob_map.items():
            ratio = SequenceMatcher(None, pm_lower, name.lower().strip()).ratio()
            if ratio > best_ratio and ratio >= 0.6:
                best_ratio = ratio
                best_match = fp

        return best_match

    # ── Convenience: backwards-compatible edge dict ───────────────────────

    @staticmethod
    def result_to_legacy_dict(result: EdgeResult) -> dict:
        """Convert an EdgeResult to the legacy dict format used by matcher.py.

        This enables drop-in replacement: matcher.py can call the new
        EdgeCalculator and still return dicts that strategy.py expects.

        Parameters
        ----------
        result : EdgeResult
            Edge calculation result.

        Returns
        -------
        dict
            Legacy-format edge dict.
        """
        return {
            "outcome": result.outcome,
            "token_id": result.token_id,
            "polymarket_price": result.polymarket_price,
            "fair_prob": result.fair_prob,
            "edge": result.raw_edge,
            "edge_pct": result.raw_edge_pct,
            "side": result.side,
            "market_type": result.market_type,
            "line": result.line,
            # New fields (strategy.py will ignore if not expected)
            "decayed_edge_pct": result.decayed_edge_pct,
            "confidence": result.confidence,
            "effective_edge_pct": result.effective_edge_pct,
            "size_usdc": result.size_usdc,
            "ev_per_dollar": result.ev_per_dollar,
            "risk_adjusted_ev": result.risk_adjusted_ev,
            "kelly_fraction": result.kelly_fraction,
            "overround_method": result.overround_method,
        }
