"""Unit tests for the cancellation registry and the SSE event shape of
run_agent's history handling (no LLM calls -- graph's client config is
loaded at import, so these only test pieces that don't need an endpoint)."""

from agent import cancellation


class TestCancellation:
    def test_mark_and_check(self):
        cancellation.mark_cancelled("req-1")
        try:
            assert cancellation.is_cancelled("req-1")
            assert not cancellation.is_cancelled("req-2")
            assert not cancellation.is_cancelled(None)
            assert not cancellation.is_cancelled("")
        finally:
            cancellation.clear("req-1")

    def test_clear(self):
        cancellation.mark_cancelled("req-1")
        cancellation.clear("req-1")
        assert not cancellation.is_cancelled("req-1")

    def test_clear_unknown_is_noop(self):
        cancellation.clear("never-registered")
