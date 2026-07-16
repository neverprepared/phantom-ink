"""Shared test fixtures."""

import pytest


@pytest.fixture(autouse=True)
def reset_hub_state():
    """Reset all module-level hub state before and after each test.

    Covers: auth, registry, runners, channels, router. Hub background tasks
    are never started in unit tests so they don't need resetting.
    """
    import brainbox.auth as _auth
    import brainbox.channels as _ch
    import brainbox.registry as _reg
    import brainbox.router as _router
    from brainbox.runners import reset_registry_for_tests
    from brainbox.store import reset_store_for_tests

    from brainbox.scheduler import reset_for_tests as _reset_scheduler
    from brainbox.loop_runner import reset_for_tests as _reset_loop_runner
    from brainbox.event_rules import reset_for_tests as _reset_event_rules
    from brainbox.os_sink import reset_for_tests as _reset_os_sink

    def _reset():
        _auth._api_key = ""
        _reg._agents.clear()
        _reg._tokens.clear()
        _reg._role_prompts.clear()
        reset_registry_for_tests()  # clears _singleton + _pairing_singleton
        _ch._channels.clear()
        _ch._messages.clear()
        _ch._listeners.clear()
        _ch._ollama_last_read.clear()
        _router._tasks.clear()
        _router._listeners.clear()
        _reset_scheduler()
        _reset_loop_runner()  # clears _instances + _child_to_loop
        _reset_event_rules()  # clears wakeup/rate windows/inflight
        _reset_os_sink()  # clears sink task/listener/client cache
        reset_store_for_tests()  # fresh in-memory DB per test

    _reset()
    yield
    _reset()


@pytest.fixture(autouse=True)
def _disable_rate_limiter():
    """Turn off the slowapi limiter for tests — create-heavy suites otherwise
    trip the 10/min /api/create cap and 429 mid-run (the limit is per-IP and
    shared across the whole test session, not reset between tests)."""
    try:
        from brainbox.rate_limit import limiter
        prev = limiter.enabled
        limiter.enabled = False
        yield
        limiter.enabled = prev
    except ImportError:
        yield


@pytest.fixture(autouse=True)
def _override_api_key_auth():
    """Disable API key auth for all tests by default.

    Individual test modules (like test_auth.py) can remove this override
    to test actual auth behavior.

    Also neutralizes the ``agent_events:write`` capability guard on the ingest
    route (T11 moved it off ``require_api_key`` onto ``require_capability``), so
    the many existing ingest tests that send no auth keep passing — restoring the
    pre-T11 open-in-tests posture for that one route only. Tests that need the
    real capability path (test_profile_tokens) pop this override explicitly.
    """
    try:
        from brainbox.api import app, _require_agent_events_write
        from brainbox.auth import require_api_key

        app.dependency_overrides[require_api_key] = lambda: None
        app.dependency_overrides[_require_agent_events_write] = lambda: None
        yield
        app.dependency_overrides.pop(require_api_key, None)
        app.dependency_overrides.pop(_require_agent_events_write, None)
    except ImportError:
        # If brainbox.api can't be imported (e.g., missing optional deps),
        # skip the override — tests that don't import app won't need it
        yield


@pytest.fixture()
def client():
    """Shared async HTTP client targeting the brainbox FastAPI app."""
    from httpx import ASGITransport, AsyncClient
    from brainbox.api import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
