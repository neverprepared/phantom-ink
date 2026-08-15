"""Tests for the brainbox.llm seam (the complete() LLM-request plane).

Covers: strategy resolution (the quality×allow_paid table + provider pins),
fallback + attempt recording, the paid gate, metering emission, and the three
backends' behavior with transport faked out (no network, no real sessions).
"""

from __future__ import annotations

import pytest

from brainbox import llm
from brainbox.config import settings
from brainbox.llm import CallCtx, Completion, CompletionPolicy, LlmError, Usage
from brainbox.models import ModelTarget


# --------------------------------------------------------------------------- #
# Fakes                                                                        #
# --------------------------------------------------------------------------- #


class FakeBackend:
    def __init__(self, name, *, paid=False, answer=None, raises=None, usage=None):
        self.name = name
        self._paid = paid
        self._answer = answer if answer is not None else f"{name}-answer"
        self._raises = raises
        self._usage = usage or Usage()
        self.calls = []

    def estimates_cost(self):
        return self._paid

    async def complete(self, messages, *, model, ctx, profile):
        self.calls.append({"messages": messages, "model": model, "profile": profile})
        if self._raises is not None:
            raise self._raises
        return Completion(text=self._answer, backend=self.name, model=model or "m", usage=self._usage)


# --------------------------------------------------------------------------- #
# strategy.chain_names — the table + pins                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "provider,quality,allow_paid,expected",
    [
        (None, "cheap", False, ["ollama"]),
        (None, "cheap", True, ["ollama", "claude_api"]),
        (None, "high", False, ["claude_oauth"]),
        (None, "high", True, ["claude_oauth", "claude_api"]),
        ("ollama", "high", True, ["ollama"]),        # pin overrides quality
        ("claude", "cheap", False, ["claude_oauth"]),  # claude pin never → ollama
        ("claude", "cheap", True, ["claude_oauth", "claude_api"]),
        ("codex", "cheap", False, ["codex"]),
    ],
)
def test_chain_names(provider, quality, allow_paid, expected):
    policy = CompletionPolicy(quality=quality, allow_paid=allow_paid)
    assert llm.chain_names(provider, policy) == expected


def test_resolve_chain_filters_to_registry():
    # codex isn't registered → resolves to an empty chain (→ LlmError at call time)
    registry = {"ollama": FakeBackend("ollama")}
    chain = llm.resolve_chain(ModelTarget(provider="codex"), CompletionPolicy(), registry)
    assert chain == []


# --------------------------------------------------------------------------- #
# complete() — routing, fallback, gating, metering                            #
# --------------------------------------------------------------------------- #


async def test_complete_happy_path_and_metering():
    ollama = FakeBackend("ollama", usage=Usage(tokens_out=42))
    llm.set_registry({"ollama": ollama})
    records = []
    llm.on_completion(records.append)

    comp = await llm.complete(
        "hello", profile="personal", policy=CompletionPolicy(quality="cheap"),
        ctx=CallCtx(caller="unit"),
    )

    assert comp.text == "ollama-answer"
    assert comp.backend == "ollama"
    assert ollama.calls[0]["messages"] == [{"role": "user", "content": "hello"}]
    # one metering record, ok, carrying tokens + a generated trace id
    assert len(records) == 1
    rec = records[0]
    assert rec.ok and rec.backend == "ollama" and rec.caller == "unit"
    assert rec.profile == "personal" and rec.tokens_out == 42
    assert rec.trace_id  # auto-filled
    assert [a.ok for a in comp.attempts] == [True]


async def test_complete_fallback_records_attempts():
    failing = FakeBackend("ollama", raises=LlmError("ollama down"))
    ok = FakeBackend("claude_api", paid=True, answer="paid-answer")
    llm.set_registry({"ollama": failing, "claude_api": ok})
    records = []
    llm.on_completion(records.append)

    comp = await llm.complete(
        "hi", profile="p", policy=CompletionPolicy(quality="cheap", allow_paid=True),
        ctx=CallCtx(caller="unit"),
    )

    assert comp.backend == "claude_api" and comp.text == "paid-answer"
    # attempts capture the fall-through then the success
    assert [(a.backend, a.ok) for a in comp.attempts] == [("ollama", False), ("claude_api", True)]
    assert comp.attempts[0].reason == "ollama down"
    assert records[0].backend == "claude_api" and records[0].ok


async def test_complete_paid_gate_skips_paid_backend():
    # A paid backend reachable in the registry must be skipped when allow_paid is
    # false — even if it's the only option (→ LlmError).
    paid = FakeBackend("claude_api", paid=True)
    llm.set_registry({"claude_api": paid})
    records = []
    llm.on_completion(records.append)

    with pytest.raises(LlmError):
        # force the paid backend into the chain via an explicit claude pin + high,
        # but allow_paid stays false → claude_oauth requested, not in registry,
        # and claude_api gated. Use a custom chain by pinning provider=claude.
        await llm.complete(
            "x", profile="p",
            target=ModelTarget(provider="claude"),
            policy=CompletionPolicy(quality="high", allow_paid=False),
            ctx=CallCtx(caller="unit"),
        )
    assert paid.calls == []  # never invoked
    assert records and records[-1].ok is False


async def test_complete_all_fail_raises_and_meters_failure():
    llm.set_registry({"ollama": FakeBackend("ollama", raises=LlmError("boom"))})
    records = []
    llm.on_completion(records.append)

    with pytest.raises(LlmError) as ei:
        await llm.complete("x", profile="p", ctx=CallCtx(caller="unit"))
    assert "boom" in str(ei.value)
    assert records[-1].ok is False and records[-1].backend == ""


async def test_complete_default_policy_uses_settings_default(monkeypatch):
    # allow_paid defaults from settings.llm.allow_paid_default
    monkeypatch.setattr(settings.llm, "allow_paid_default", False, raising=False)
    ollama = FakeBackend("ollama")
    llm.set_registry({"ollama": ollama, "claude_api": FakeBackend("claude_api", paid=True)})
    comp = await llm.complete("x", profile="p")  # no policy → cheap + allow_paid=False
    assert comp.backend == "ollama"


async def test_complete_message_list_passthrough():
    ollama = FakeBackend("ollama")
    llm.set_registry({"ollama": ollama})
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "u"}]
    await llm.complete(msgs, profile="p", ctx=CallCtx(caller="unit"))
    assert ollama.calls[0]["messages"] == msgs


# --------------------------------------------------------------------------- #
# OllamaBackend — wraps the pool + achat (faked)                              #
# --------------------------------------------------------------------------- #


async def test_ollama_backend_uses_pool_and_achat(monkeypatch):
    from brainbox.llm.backends import OllamaBackend
    from brainbox import ollama as ollama_mod
    from brainbox import ollama_pool as pool_mod

    class FakeInst:
        url = "http://fake:11434"
        healthy = True
        verify_tls = True
        in_flight = 0

        def request_headers(self):
            return {}

    class FakePool:
        def __init__(self):
            self.acquired = 0
            self.released = 0

        def pick(self, *, runner_name=None):
            return FakeInst()

        def acquire(self, inst):
            self.acquired += 1

        def release(self, inst):
            self.released += 1

    fake_pool = FakePool()
    monkeypatch.setattr(pool_mod, "get_pool", lambda: fake_pool)

    async def fake_achat(messages, model=None, base_url=None, *, headers=None, verify=True):
        assert base_url == "http://fake:11434"
        return ollama_mod.ChatResult(
            model=model or "qwen", message=ollama_mod.ChatMessage(role="assistant", content="hi from ollama"),
            eval_count=7,
        )

    monkeypatch.setattr(ollama_mod, "achat", fake_achat)

    comp = await OllamaBackend().complete(
        [{"role": "user", "content": "q"}], model="qwen", ctx=CallCtx(caller="unit"), profile="p"
    )
    assert comp.text == "hi from ollama" and comp.usage.tokens_out == 7
    assert fake_pool.acquired == 1 and fake_pool.released == 1  # in-flight balanced


async def test_ollama_backend_no_instance_raises(monkeypatch):
    from brainbox.llm.backends import OllamaBackend
    from brainbox import ollama_pool as pool_mod

    class EmptyPool:
        def pick(self, *, runner_name=None):
            return None

    monkeypatch.setattr(pool_mod, "get_pool", lambda: EmptyPool())
    with pytest.raises(LlmError):
        await OllamaBackend().complete([{"role": "user", "content": "q"}], model=None, ctx=CallCtx(caller="u"), profile="p")


# --------------------------------------------------------------------------- #
# ClaudeApiBackend — key gate + request shaping (httpx faked)                 #
# --------------------------------------------------------------------------- #


async def test_claude_api_no_key_raises(monkeypatch):
    from brainbox.llm.backends import ClaudeApiBackend

    monkeypatch.setattr(settings.llm, "anthropic_api_key", "", raising=False)
    with pytest.raises(LlmError) as ei:
        await ClaudeApiBackend().complete([{"role": "user", "content": "q"}], model=None, ctx=CallCtx(caller="u"), profile="p")
    assert "no API key" in str(ei.value)


async def test_claude_api_shapes_request_and_parses(monkeypatch):
    from brainbox.llm import backends as be

    captured = {}

    class FakeResp:
        status_code = 200
        text = ""

        def json(self):
            return {
                "model": "claude-opus-4-8",
                "content": [{"type": "text", "text": "answer"}],
                "usage": {"input_tokens": 10, "output_tokens": 20},
            }

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["body"] = json
            captured["headers"] = headers
            return FakeResp()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(settings.llm, "cost_per_mtok_in", 3.0, raising=False)
    monkeypatch.setattr(settings.llm, "cost_per_mtok_out", 15.0, raising=False)

    backend = be.ClaudeApiBackend(key_provider=lambda profile: "sk-test")
    comp = await backend.complete(
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "u"}],
        model="claude-opus-4-8", ctx=CallCtx(caller="u", max_tokens=100), profile="p",
    )

    # system hoisted out of the turn list
    assert captured["body"]["system"] == "sys"
    assert captured["body"]["messages"] == [{"role": "user", "content": "u"}]
    assert captured["body"]["max_tokens"] == 100
    assert captured["headers"]["x-api-key"] == "sk-test"
    assert comp.text == "answer" and comp.usage.tokens_in == 10 and comp.usage.tokens_out == 20
    # 10/1e6*3 + 20/1e6*15 = 3.3e-4
    assert comp.usage.cost_estimate_usd == pytest.approx(10 / 1e6 * 3.0 + 20 / 1e6 * 15.0)


# --------------------------------------------------------------------------- #
# ClaudeOAuthBackend — relocated session flow (local API faked)               #
# --------------------------------------------------------------------------- #


async def test_claude_oauth_backend_runner_flow(monkeypatch):
    """Runner-backed path skips the readiness wait; drives create→query→stop→delete."""
    from brainbox.llm import backends as be

    calls = []

    class FakeResp:
        def __init__(self, payload=None):
            self._payload = payload or {}
            self.status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, path, json=None, headers=None):
            calls.append(path)
            if path.endswith("/query"):
                return FakeResp({"output": "session says hi"})
            return FakeResp({})

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(be, "_load_local_api_key", lambda: "k")

    comp = await be.ClaudeOAuthBackend().complete(
        [{"role": "user", "content": "do the thing"}],
        model=None,
        ctx=CallCtx(caller="u", runner="mac-1"),  # runner → no readiness wait
        profile="personal",
    )

    assert comp.text == "session says hi"
    assert comp.backend == "claude_oauth"
    # full lifecycle, in order, no /exec readiness poll for runner-backed sessions
    assert any(p == "/api/create" for p in calls)
    assert any(p.endswith("/query") for p in calls)
    assert any(p == "/api/stop" for p in calls)
    assert any(p == "/api/delete" for p in calls)
    assert not any(p.endswith("/exec") for p in calls)


def test_compose_prompt_single_user_roundtrips():
    from brainbox.llm.backends import _compose_prompt

    assert _compose_prompt([{"role": "user", "content": "just this"}]) == "just this"


def test_compose_prompt_merges_system_and_turns():
    from brainbox.llm.backends import _compose_prompt

    out = _compose_prompt([
        {"role": "system", "content": "S"},
        {"role": "user", "content": "U"},
        {"role": "assistant", "content": "A"},
    ])
    assert "S" in out and "U" in out and "[assistant] A" in out
