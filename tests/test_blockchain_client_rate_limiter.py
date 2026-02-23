"""Tests for BlockchainClient Alchemy rate limiter and retry logic."""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

import src.scanner.blockchain_client as bc_module
from src.scanner.blockchain_client import (
    BlockchainClient,
    _ALCHEMY_MAX_CONCURRENT,
    _ALCHEMY_MIN_SPACING,
    _BACKOFF_DELAYS,
    _is_rate_limited,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_client() -> BlockchainClient:
    """Instantiate BlockchainClient without a real Web3 connection."""
    client = BlockchainClient.__new__(BlockchainClient)
    client.w3 = MagicMock()
    client.w3.is_connected.return_value = True
    client._age_cache = {}
    client._first_pm_cache = {}
    client._balance_cache = {}
    client._funding_cache = {}
    client._calls_ok = 0
    client._rl_hits = 0
    client._calls_failed = 0
    return client


@pytest.fixture(autouse=True)
def reset_rate_limiter_state():
    """Reset module-level rate limiter between tests to avoid cross-test bleed."""
    bc_module._ALCHEMY_LAST_CALL = 0.0
    yield
    bc_module._ALCHEMY_LAST_CALL = 0.0


# ── _is_rate_limited ─────────────────────────────────────────────────────────


class TestIsRateLimited:
    def test_detects_429_in_message(self):
        assert _is_rate_limited(Exception("HTTP Error 429 Too Many Requests"))

    def test_detects_rate_limit_string(self):
        assert _is_rate_limited(Exception("rate limit exceeded"))

    def test_detects_too_many_requests(self):
        assert _is_rate_limited(Exception("Too Many Requests"))

    def test_case_insensitive(self):
        assert _is_rate_limited(Exception("RATE LIMIT"))

    def test_ignores_unrelated_errors(self):
        assert not _is_rate_limited(ValueError("connection timeout"))
        assert not _is_rate_limited(Exception("500 internal server error"))


# ── _alchemy_request — retry logic ───────────────────────────────────────────


class TestAlchemyRequestRetry:
    """Test retry/backoff via _alchemy_request with a mocked _rate_limited_alchemy."""

    def _patched_client(self, side_effects):
        """Return a client where _rate_limited_alchemy replays *side_effects*."""
        client = _make_client()
        call_iter = iter(side_effects)

        def fake_rate_limited(fn):
            result = next(call_iter)
            if isinstance(result, Exception):
                raise result
            return result

        client._rate_limited_alchemy = fake_rate_limited  # type: ignore[method-assign]
        return client

    # ── success ──────────────────────────────────────────────────────────────

    def test_success_returns_result(self):
        client = self._patched_client([{"ok": True}])
        with patch("time.sleep"):
            result = client._alchemy_request(lambda: None)
        assert result == {"ok": True}
        assert client._calls_ok == 1
        assert client._rl_hits == 0
        assert client._calls_failed == 0

    # ── 429 retry → eventual success ─────────────────────────────────────────

    def test_retries_429_and_eventually_succeeds(self):
        client = self._patched_client([
            Exception("429 Too Many Requests"),
            Exception("429 Too Many Requests"),
            {"ok": True},
        ])
        sleep_calls = []
        with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            result = client._alchemy_request(lambda: None)

        assert result == {"ok": True}
        assert client._calls_ok == 1
        assert client._rl_hits == 2
        assert client._calls_failed == 0
        # Slept with the first two backoff values
        assert _BACKOFF_DELAYS[0] in sleep_calls
        assert _BACKOFF_DELAYS[1] in sleep_calls

    # ── 429 exhausted ────────────────────────────────────────────────────────

    def test_exhausted_429_returns_none(self):
        n_attempts = len(_BACKOFF_DELAYS) + 1
        client = self._patched_client(
            [Exception("429 rate limit")] * n_attempts
        )
        with patch("time.sleep"):
            result = client._alchemy_request(lambda: None)

        assert result is None
        assert client._rl_hits == n_attempts
        assert client._calls_ok == 0
        assert client._calls_failed == 0

    # ── non-429 error → no retry ─────────────────────────────────────────────

    def test_non_429_error_no_retry(self):
        client = self._patched_client([
            ValueError("unexpected EOF"),
        ])
        with patch("time.sleep"):
            result = client._alchemy_request(lambda: None)

        assert result is None
        assert client._calls_failed == 1
        assert client._rl_hits == 0
        assert client._calls_ok == 0

    def test_non_429_does_not_sleep_for_backoff(self):
        client = self._patched_client([Exception("connection reset")])
        sleep_calls = []
        with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            client._alchemy_request(lambda: None)

        # No backoff sleeps should have been triggered
        backoff_sleeps = [s for s in sleep_calls if s in _BACKOFF_DELAYS]
        assert backoff_sleeps == []

    # ── counter accumulation across multiple calls ────────────────────────────

    def test_counters_accumulate_across_calls(self):
        client = _make_client()

        call_no = [0]
        n_attempts = len(_BACKOFF_DELAYS) + 1  # e.g. 4

        def fake_rl(fn):
            call_no[0] += 1
            if call_no[0] == 1:
                return {"ok": True}   # 1st external call succeeds immediately
            raise Exception("429")    # all attempts of the 2nd call fail

        client._rate_limited_alchemy = fake_rl  # type: ignore[method-assign]

        with patch("time.sleep"):
            client._alchemy_request(lambda: None)   # call 1 → ok
            client._alchemy_request(lambda: None)   # call 2 → 429 exhausted

        # 1st call ok; 2nd call exhausts all attempts with 429
        assert client._calls_ok == 1
        assert client._rl_hits == n_attempts  # every attempt of 2nd call was 429
        assert client._calls_failed == 0


# ── _rate_limited_alchemy — spacing enforcement ───────────────────────────────


class TestRateLimitedAlchemySpacing:
    def test_no_sleep_when_last_call_long_ago(self):
        """No sleep when plenty of time has elapsed since the last call."""
        client = _make_client()
        fn = MagicMock(return_value="ok")

        # last call was 10 seconds ago — no wait required
        bc_module._ALCHEMY_LAST_CALL = time.monotonic() - 10.0

        with patch("time.sleep") as mock_sleep:
            result = client._rate_limited_alchemy(fn)

        assert result == "ok"
        mock_sleep.assert_not_called()

    def test_sleeps_for_remaining_gap(self):
        """Sleeps exactly for (MIN_SPACING - elapsed) when called too soon."""
        client = _make_client()
        fn = MagicMock(return_value="ok")

        fake_now = 5000.0
        elapsed = 0.05  # 50 ms since last call
        bc_module._ALCHEMY_LAST_CALL = fake_now - elapsed

        with patch("time.monotonic", return_value=fake_now), \
             patch("time.sleep") as mock_sleep:
            client._rate_limited_alchemy(fn)

        expected_wait = _ALCHEMY_MIN_SPACING - elapsed
        mock_sleep.assert_called_once_with(pytest.approx(expected_wait, abs=0.002))

    def test_books_slot_so_next_thread_waits(self):
        """After a call, _ALCHEMY_LAST_CALL is updated so the next caller waits."""
        client = _make_client()
        fn = MagicMock(return_value="ok")

        fake_now = 3000.0
        bc_module._ALCHEMY_LAST_CALL = 0.0  # long ago

        with patch("time.monotonic", return_value=fake_now), \
             patch("time.sleep"):
            client._rate_limited_alchemy(fn)

        # Slot should be booked at fake_now (max(now, 0 + spacing) = now)
        assert bc_module._ALCHEMY_LAST_CALL == pytest.approx(fake_now, abs=0.001)

    def test_sequential_bookings_stack(self):
        """Two rapid sequential calls each get their own time slot 200 ms apart."""
        client = _make_client()
        fn = MagicMock(return_value="ok")

        fake_now = 7000.0
        bc_module._ALCHEMY_LAST_CALL = 0.0

        with patch("time.monotonic", return_value=fake_now), \
             patch("time.sleep"):
            client._rate_limited_alchemy(fn)
            # First call books slot at fake_now
            first_booking = bc_module._ALCHEMY_LAST_CALL

        with patch("time.monotonic", return_value=fake_now), \
             patch("time.sleep") as mock_sleep2:
            client._rate_limited_alchemy(fn)

        # Second call should book slot at first_booking + MIN_SPACING
        assert bc_module._ALCHEMY_LAST_CALL == pytest.approx(
            first_booking + _ALCHEMY_MIN_SPACING, abs=0.001
        )
        # And must sleep for the difference
        mock_sleep2.assert_called_once()
        sleep_arg = mock_sleep2.call_args[0][0]
        assert sleep_arg == pytest.approx(_ALCHEMY_MIN_SPACING, abs=0.002)


# ── _ALCHEMY_SEMAPHORE — concurrency cap ─────────────────────────────────────


class TestConcurrencyCap:
    def test_at_most_max_concurrent_calls_in_flight(self):
        """At most _ALCHEMY_MAX_CONCURRENT calls execute simultaneously."""
        client = _make_client()

        max_concurrent = [0]
        current_concurrent = [0]
        counter_lock = threading.Lock()

        def slow_fn():
            with counter_lock:
                current_concurrent[0] += 1
                if current_concurrent[0] > max_concurrent[0]:
                    max_concurrent[0] = current_concurrent[0]
            time.sleep(0.08)  # hold slot for 80 ms to create overlap
            with counter_lock:
                current_concurrent[0] -= 1
            return {"ok": True}

        # Reset spacing so all threads can start without spacing delays
        bc_module._ALCHEMY_LAST_CALL = 0.0

        n_threads = _ALCHEMY_MAX_CONCURRENT + 3  # e.g. 6 threads
        threads = [
            threading.Thread(target=client._alchemy_request, args=(slow_fn,))
            for _ in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        assert 1 <= max_concurrent[0] <= _ALCHEMY_MAX_CONCURRENT


# ── log_stats ────────────────────────────────────────────────────────────────


class TestLogStats:
    def test_log_stats_contains_all_counters(self, caplog):
        import logging
        client = _make_client()
        client._calls_ok = 42
        client._rl_hits = 7
        client._calls_failed = 2

        with caplog.at_level(logging.INFO, logger="src.scanner.blockchain_client"):
            client.log_stats()

        assert "42" in caplog.text
        assert "7" in caplog.text
        assert "2" in caplog.text

    def test_log_stats_zero_on_fresh_client(self, caplog):
        import logging
        client = _make_client()

        with caplog.at_level(logging.INFO, logger="src.scanner.blockchain_client"):
            client.log_stats()

        # Should log something without crashing
        assert caplog.text  # non-empty
