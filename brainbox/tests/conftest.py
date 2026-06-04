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
        reset_store_for_tests()  # fresh in-memory DB per test

    _reset()
    yield
    _reset()


@pytest.fixture(autouse=True)
def _override_api_key_auth():
    """Disable API key auth for all tests by default.

    Individual test modules (like test_auth.py) can remove this override
    to test actual auth behavior.
    """
    try:
        from brainbox.api import app
        from brainbox.auth import require_api_key

        app.dependency_overrides[require_api_key] = lambda: None
        yield
        app.dependency_overrides.pop(require_api_key, None)
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
