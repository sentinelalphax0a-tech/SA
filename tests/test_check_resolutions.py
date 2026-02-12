"""Tests for the B21 reversion checker."""

from src.analysis.reversion_checker import check_reversion


class TestB21Reversion:
    def test_no_current_odds(self):
        result = check_reversion("YES", 0.30, None)
        assert result is None

    def test_no_odds_at_alert(self):
        result = check_reversion("YES", None, 0.40)
        assert result is None

    def test_moderate_reversion_yes(self):
        # YES direction, price went from 0.30 to 0.38 (+0.08 = +8%)
        result = check_reversion("YES", 0.30, 0.38)
        assert result is not None
        assert result.filter_id == "B21"
        assert result.points == 10

    def test_strong_reversion_yes(self):
        # YES direction, price went from 0.30 to 0.50 (+0.20 = +20%)
        result = check_reversion("YES", 0.30, 0.50)
        assert result is not None
        assert result.points == 20

    def test_away_yes(self):
        # YES direction, price went from 0.30 to 0.15 (-0.15 = -15% away)
        result = check_reversion("YES", 0.30, 0.15)
        assert result is not None
        assert result.points == -15

    def test_moderate_reversion_no(self):
        # NO direction, price went from 0.70 to 0.60 (-0.10 = favorable for NO)
        result = check_reversion("NO", 0.70, 0.60)
        assert result is not None
        assert result.points == 10

    def test_strong_reversion_no(self):
        # NO direction, price went from 0.80 to 0.50 (-0.30 = strong favorable for NO)
        result = check_reversion("NO", 0.80, 0.50)
        assert result is not None
        assert result.points == 20

    def test_away_no(self):
        # NO direction, price went from 0.30 to 0.50 (+0.20 = against NO)
        result = check_reversion("NO", 0.30, 0.50)
        assert result is not None
        assert result.points == -15

    def test_small_move_no_trigger(self):
        # Move too small to trigger anything
        result = check_reversion("YES", 0.30, 0.32)
        assert result is None

    def test_no_change_no_trigger(self):
        result = check_reversion("YES", 0.50, 0.50)
        assert result is None
