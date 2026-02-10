"""Tests for the scoring engine."""

from src.analysis.scoring import calculate_score, _matches_pattern, _score_to_stars
from src.database.models import FilterResult
from src import config


def _make_filter(filter_def: dict) -> FilterResult:
    return FilterResult(**filter_def)


class TestScoreToStars:
    def test_star_5(self):
        assert _score_to_stars(120) == 5
        assert _score_to_stars(200) == 5

    def test_star_4(self):
        assert _score_to_stars(90) == 4
        assert _score_to_stars(119) == 4

    def test_star_3(self):
        assert _score_to_stars(70) == 3
        assert _score_to_stars(89) == 3

    def test_star_2(self):
        assert _score_to_stars(50) == 2
        assert _score_to_stars(69) == 2

    def test_star_1(self):
        assert _score_to_stars(30) == 1
        assert _score_to_stars(49) == 1

    def test_star_0(self):
        assert _score_to_stars(0) == 0
        assert _score_to_stars(29) == 0


class TestCalculateScore:
    def test_empty_filters(self):
        result = calculate_score([])
        assert result.score_raw == 0
        assert result.multiplier == 1.0
        assert result.score_final == 0
        assert result.star_level == 0

    def test_single_filter(self):
        filters = [_make_filter(config.FILTER_W01)]  # 25 pts
        result = calculate_score(filters)
        assert result.score_raw == 25
        assert result.score_final == 25
        assert result.star_level == 0

    def test_negative_floors_at_zero(self):
        filters = [_make_filter(config.FILTER_N03)]  # -100 pts
        result = calculate_score(filters)
        assert result.score_raw == 0
        assert result.score_final == 0

    def test_multiplier_p1_insider(self):
        """P1: W01 + W09 + O03 + one of C01/C02/C04/C07 → x1.3"""
        filters = [
            _make_filter(config.FILTER_W01),   # 25
            _make_filter(config.FILTER_W09),   # 20
            _make_filter(config.FILTER_O03),   # 15
            _make_filter(config.FILTER_C01),   # 25
        ]
        result = calculate_score(filters)
        assert result.multiplier == 1.3
        assert result.score_raw == 85
        assert result.score_final == int(85 * 1.3)

    def test_multiplier_p6_distribution(self):
        """P6: C07 + one of W01/W02/W03 → x1.4"""
        filters = [
            _make_filter(config.FILTER_C07),   # 60
            _make_filter(config.FILTER_W01),   # 25
        ]
        result = calculate_score(filters)
        assert result.multiplier == 1.4
        assert result.score_final == int(85 * 1.4)


class TestMatchesPattern:
    def test_required_match(self):
        pattern = {"required": {"A", "B"}, "multiplier": 1.2}
        assert _matches_pattern({"A", "B", "C"}, pattern) is True

    def test_required_no_match(self):
        pattern = {"required": {"A", "B"}, "multiplier": 1.2}
        assert _matches_pattern({"A", "C"}, pattern) is False

    def test_any_of_match(self):
        pattern = {"any_of": {"X", "Y"}, "multiplier": 1.1}
        assert _matches_pattern({"X"}, pattern) is True

    def test_none_of_blocks(self):
        pattern = {"required": {"A"}, "none_of": {"Z"}, "multiplier": 1.1}
        assert _matches_pattern({"A", "Z"}, pattern) is False
