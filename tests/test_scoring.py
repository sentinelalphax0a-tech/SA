"""Tests for the scoring engine v2."""

import math

from src.analysis.scoring import (
    calculate_score,
    _enforce_mutual_exclusion,
    _get_categories,
    _get_amount_multiplier,
    _get_diversity_multiplier,
    _score_to_stars,
    _validate_stars,
)
from src.database.models import FilterResult
from src import config


def _make_filter(filter_def: dict) -> FilterResult:
    return FilterResult(
        filter_id=filter_def["id"],
        filter_name=filter_def["name"],
        points=filter_def["points"],
        category=filter_def["category"],
    )


def _fr(fid: str, pts: int, cat: str) -> FilterResult:
    return FilterResult(filter_id=fid, filter_name=fid, points=pts, category=cat)


# ── _score_to_stars (new thresholds) ─────────────────────────


class TestScoreToStars:
    def test_star_5(self):
        assert _score_to_stars(220) == 5
        assert _score_to_stars(300) == 5

    def test_star_4(self):
        assert _score_to_stars(150) == 4
        assert _score_to_stars(219) == 4

    def test_star_3(self):
        assert _score_to_stars(100) == 3
        assert _score_to_stars(149) == 3

    def test_star_2(self):
        assert _score_to_stars(70) == 2
        assert _score_to_stars(99) == 2

    def test_star_1(self):
        assert _score_to_stars(40) == 1
        assert _score_to_stars(69) == 1

    def test_star_0(self):
        assert _score_to_stars(0) == 0
        assert _score_to_stars(39) == 0


# ── _get_amount_multiplier (logarithmic) ─────────────────────


class TestAmountMultiplier:
    def test_zero(self):
        assert _get_amount_multiplier(0) == 0.3

    def test_low_amount(self):
        # 0.18 * ln(100) - 0.37 ≈ 0.46
        result = _get_amount_multiplier(100)
        assert 0.44 <= result <= 0.48

    def test_medium_amount(self):
        # 0.18 * ln(1000) - 0.37 ≈ 0.87
        result = _get_amount_multiplier(1000)
        assert 0.85 <= result <= 0.89

    def test_high_amount(self):
        # 0.18 * ln(50000) - 0.37 ≈ 1.58
        result = _get_amount_multiplier(50_000)
        assert 1.55 <= result <= 1.60

    def test_5k(self):
        # 0.18 * ln(5000) - 0.37 ≈ 1.16
        result = _get_amount_multiplier(5_000)
        assert 1.14 <= result <= 1.18

    def test_500(self):
        # 0.18 * ln(500) - 0.37 ≈ 0.75
        result = _get_amount_multiplier(500)
        assert 0.73 <= result <= 0.77

    def test_monotonically_increasing(self):
        """Larger amounts should always produce larger multipliers."""
        amounts = [10, 100, 500, 1000, 5000, 10000, 50000, 100000]
        mults = [_get_amount_multiplier(a) for a in amounts]
        for i in range(len(mults) - 1):
            assert mults[i] < mults[i + 1], f"Failed at {amounts[i]} vs {amounts[i+1]}"

    def test_clamped_at_min(self):
        """Very small amounts should be clamped to 0.3."""
        assert _get_amount_multiplier(1) >= 0.3

    def test_clamped_at_max(self):
        """Very large amounts should be clamped to 2.0."""
        assert _get_amount_multiplier(1_000_000_000) <= 2.0


# ── _get_diversity_multiplier ─────────────────────────────────


class TestDiversityMultiplier:
    def test_none_is_neutral(self):
        assert _get_diversity_multiplier(None) == 1.0

    def test_sniper(self):
        """<=3 markets → x1.2 (focused)."""
        assert _get_diversity_multiplier(1) == 1.2
        assert _get_diversity_multiplier(3) == 1.2

    def test_normal(self):
        """4-9 markets → x1.0."""
        assert _get_diversity_multiplier(5) == 1.0
        assert _get_diversity_multiplier(9) == 1.0

    def test_shotgun(self):
        """>10 markets → x0.7."""
        assert _get_diversity_multiplier(11) == 0.7
        assert _get_diversity_multiplier(19) == 0.7

    def test_super_shotgun(self):
        """>= 20 markets → x0.5."""
        assert _get_diversity_multiplier(20) == 0.5
        assert _get_diversity_multiplier(50) == 0.5


# ── _get_categories ──────────────────────────────────────────


class TestGetCategories:
    def test_wallet_maps_to_accumulation(self):
        filters = [_fr("W01", 25, "wallet")]
        assert _get_categories(filters) == {"ACCUMULATION"}

    def test_behavior_maps_to_coordination(self):
        filters = [_fr("B01", 20, "behavior")]
        assert _get_categories(filters) == {"COORDINATION"}

    def test_market_maps_to_timing(self):
        filters = [_fr("M01", 15, "market")]
        assert _get_categories(filters) == {"TIMING"}

    def test_negative_not_counted(self):
        """Negative filters (points <= 0) don't contribute categories."""
        filters = [_fr("N03", -100, "negative")]
        assert _get_categories(filters) == set()

    def test_multiple_categories(self):
        filters = [
            _fr("W01", 25, "wallet"),
            _fr("B01", 20, "behavior"),
            _fr("M01", 15, "market"),
        ]
        assert _get_categories(filters) == {"ACCUMULATION", "COORDINATION", "TIMING"}


# ── _validate_stars ──────────────────────────────────────────


class TestValidateStars:
    def test_star_2_no_validation(self):
        """Stars <= 2 are never downgraded."""
        result = _validate_stars(2, set(), 0, [])
        assert result == 2

    def test_star_3_needs_2_categories(self):
        cats = {"ACCUMULATION"}
        result = _validate_stars(3, cats, 10_000, [])
        assert result == 2  # downgraded

    def test_star_3_passes_with_2_cats(self):
        cats = {"ACCUMULATION", "COORDINATION"}
        result = _validate_stars(3, cats, 10_000, [])
        assert result == 3

    def test_star_4_needs_amount(self):
        cats = {"ACCUMULATION", "COORDINATION"}
        result = _validate_stars(4, cats, 1_000, [])
        assert result == 3  # downgraded: needs $5K

    def test_star_4_passes(self):
        cats = {"ACCUMULATION", "COORDINATION"}
        result = _validate_stars(4, cats, 5_000, [])
        assert result == 4

    def test_star_5_needs_coord(self):
        cats = {"ACCUMULATION", "TIMING", "MARKET"}  # 3 cats but no COORD
        result = _validate_stars(5, cats, 20_000, [])
        assert result == 4  # downgraded

    def test_star_5_needs_3_cats(self):
        cats = {"ACCUMULATION", "COORDINATION"}  # only 2 cats
        result = _validate_stars(5, cats, 20_000, [])
        assert result == 4  # downgraded

    def test_star_5_needs_amount(self):
        cats = {"ACCUMULATION", "COORDINATION", "TIMING"}
        result = _validate_stars(5, cats, 5_000, [])
        assert result == 4  # downgraded: needs $10K

    def test_star_5_passes(self):
        cats = {"ACCUMULATION", "COORDINATION", "TIMING"}
        result = _validate_stars(5, cats, 10_000, [])
        assert result == 5


# ── calculate_score (integration) ────────────────────────────


class TestCalculateScore:
    def test_empty_filters(self):
        result = calculate_score([])
        assert result.score_raw == 0
        assert result.score_final == 0
        assert result.star_level == 0

    def test_single_filter_low_amount(self):
        """Low amount applies logarithmic multiplier."""
        filters = [_make_filter(config.FILTER_W01)]  # 25 pts
        result = calculate_score(filters, total_amount=100)
        assert result.score_raw == 25
        # 0.18 * ln(100) - 0.37 ≈ 0.46
        assert 0.44 <= result.multiplier <= 0.48
        assert result.score_final == round(25 * result.multiplier)
        assert result.star_level == 0

    def test_single_filter_high_amount(self):
        """High amount applies higher logarithmic multiplier."""
        filters = [_make_filter(config.FILTER_W01)]  # 25 pts
        result = calculate_score(filters, total_amount=60_000)
        assert result.score_raw == 25
        # 0.18 * ln(60000) - 0.37 ≈ 1.61
        assert 1.58 <= result.multiplier <= 1.64

    def test_negative_floors_at_zero(self):
        filters = [_make_filter(config.FILTER_N03)]  # -100 pts
        result = calculate_score(filters)
        assert result.score_raw == 0
        assert result.score_final == 0

    def test_mutual_exclusion(self):
        """B18a and B18d are in same group — only highest kept."""
        filters = [
            _make_filter(config.FILTER_B18A),  # 15 pts
            _make_filter(config.FILTER_B18D),  # 50 pts
        ]
        result = calculate_score(filters, total_amount=1000)
        # Only B18d (50 pts) should count
        assert result.score_raw == 50

    def test_star_validation_downgrade(self):
        """High score with single category gets downgraded."""
        # All wallet filters → only ACCUMULATION category
        filters = [
            _make_filter(config.FILTER_W01),  # 25
            _make_filter(config.FILTER_W04),  # 25
            _make_filter(config.FILTER_W09),  # 20
            _make_filter(config.FILTER_O01),  # 15
            _make_filter(config.FILTER_O03),  # 15
        ]
        result = calculate_score(filters, total_amount=60_000)
        # Only ACCUMULATION category → can't be 3+ stars
        assert result.star_level == 2

    def test_multi_category_high_score(self):
        """High score with multiple categories gets proper stars."""
        filters = [
            _make_filter(config.FILTER_W01),   # 25, wallet → ACCUMULATION
            _make_filter(config.FILTER_B18D),  # 50, behavior → COORDINATION
            _make_filter(config.FILTER_M01),   # 15, market → TIMING
        ]
        # raw = 90, amount=10K → log mult ≈ 1.29 → 116
        result = calculate_score(filters, total_amount=10_000)
        assert result.score_raw == 90
        # 3 categories → passes validation for 3 stars
        assert result.star_level >= 3

    def test_backwards_compatible_no_amount(self):
        """Calling without total_amount still works (defaults to 0 → x0.3)."""
        filters = [_make_filter(config.FILTER_W01)]
        result = calculate_score(filters)
        assert result.multiplier == 0.3

    def test_diversity_sniper_boosts(self):
        """Sniper wallet (<=3 markets) gets multiplier boost."""
        filters = [_make_filter(config.FILTER_W01)]  # 25 pts
        result_normal = calculate_score(filters, total_amount=1000)
        result_sniper = calculate_score(filters, total_amount=1000, wallet_market_count=2)
        assert result_sniper.score_final > result_normal.score_final

    def test_diversity_shotgun_penalizes(self):
        """Shotgun wallet (>10 markets) gets multiplier penalty."""
        filters = [_make_filter(config.FILTER_W01)]  # 25 pts
        result_normal = calculate_score(filters, total_amount=1000)
        result_shotgun = calculate_score(filters, total_amount=1000, wallet_market_count=15)
        assert result_shotgun.score_final < result_normal.score_final


# ── N09 star cap ──────────────────────────────────────────────


class TestObviousBetStarCap:
    def test_star_cap_n09a(self):
        """Score=200 with N09a → star capped at 2 (not 4)."""
        filters = [
            _make_filter(config.FILTER_W01),   # 25, wallet → ACCUMULATION
            _make_filter(config.FILTER_B18D),  # 50, behavior → COORDINATION
            _make_filter(config.FILTER_M01),   # 15, market → TIMING
            _make_filter(config.FILTER_N09A),  # -40, negative
        ]
        result = calculate_score(filters, total_amount=50_000)
        assert result.score_final > 0
        assert result.star_level <= 2

    def test_star_cap_n09b(self):
        """Score=200 with N09b → star capped at 3 (not 4)."""
        filters = [
            _make_filter(config.FILTER_W01),   # 25
            _make_filter(config.FILTER_B18D),  # 50
            _make_filter(config.FILTER_M01),   # 15
            _make_filter(config.FILTER_N09B),  # -25
        ]
        result = calculate_score(filters, total_amount=50_000)
        assert result.score_final > 0
        assert result.star_level <= 3

    def test_no_cap_without_n09(self):
        """Score=200 without N09 → normal star level (4)."""
        filters = [
            _make_filter(config.FILTER_W01),   # 25
            _make_filter(config.FILTER_B18D),  # 50
            _make_filter(config.FILTER_M01),   # 15
        ]
        result = calculate_score(filters, total_amount=50_000)
        # raw=90, mult ~1.57 → ~141 → 3★, but needs 2 cats → passes
        assert result.star_level >= 3

    def test_cap_doesnt_affect_low(self):
        """Score=60 with N09a → star=1 (already below cap of 2)."""
        filters = [
            _make_filter(config.FILTER_W01),   # 25
            _make_filter(config.FILTER_N09A),  # -40
        ]
        result = calculate_score(filters, total_amount=1_000)
        assert result.star_level <= 1
