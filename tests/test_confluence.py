"""Tests for the confluence detector (C filters)."""

from unittest.mock import MagicMock

from src.analysis.confluence_detector import ConfluenceDetector


class TestConfluenceDetector:
    def test_init(self):
        db = MagicMock()
        detector = ConfluenceDetector(db)
        assert detector.db is db
