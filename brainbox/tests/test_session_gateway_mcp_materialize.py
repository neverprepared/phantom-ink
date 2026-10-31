"""Tests for docker/brainbox/setup/materialize-gateway-mcp.py.

That script runs inside every session container, from ttyd-wrapper.sh, and is
what puts a usable `phantom-gateway` MCP entry in front of Claude Code. It
lives under docker/ rather than brainbox/, but it is tested here because this
is the suite CI already runs for container changes (build-containers.yml and
test-brainbox.yml both watch docker/**) — a shell-only test harness does not
exist and one file does not justify inventing one.

The bug being guarded: the image bakes the entry with ``${PHANTOM_GATEWAY_URL}``
and ``Bearer ${PHANTOM_GATEWAY_TOKEN}`` and assumed Claude Code expands both at
connect time. It expands the url and NOT the headers, so the bearer reached the
gateway as that literal string, the gateway answered 401 invalid_token, and
every gateway tool vanished from the container with no explanation anywhere in
the guest.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import stat

import pytest

_SCRIPT = (
    pathlib.Path(__file__).resolve().parents[2]
    / "docker"
    / "brainbox"
    / "setup"
    / "materialize-gateway-mcp.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("materialize_gateway_mcp", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    assert _SCRIPT.is_file(), f"{_SCRIPT} is missing — the Dockerfile COPY would break"
    return _load()


URL = "https://gateway.example.com/mcp"
TOKEN = "header.payload.signature"


def _entry(path: pathlib.Path) -> dict:
    return json.loads(path.read_text())["mcpServers"]["phantom-gateway"]


class TestMaterialize:
    def test_substitutes_real_values_for_the_placeholders(self, mod, tmp_path):
        """The regression: no ``$`` may survive into url or Authorization."""
        cfg = tmp_path / ".claude.json"
        cfg.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "phantom-gateway": {
                            "type": "http",
                            "url": "${PHANTOM_GATEWAY_URL}",
                            "headers": {
                                "Authorization": "Bearer ${PHANTOM_GATEWAY_TOKEN}"
                            },
                        }
                    }
                }
            )
        )
        mod.materialize(cfg, URL, TOKEN)

        entry = _entry(cfg)
        assert entry["url"] == URL
        assert entry["headers"]["Authorization"] == f"Bearer {TOKEN}"
        assert "$" not in entry["url"]
        assert "$" not in entry["headers"]["Authorization"]

    def test_preserves_everything_else_in_the_file(self, mod, tmp_path):
        """~/.claude.json also holds oauth state and the profile's own servers.

        Losing those costs the guest its login, which is worse than no gateway.
        """
        cfg = tmp_path / ".claude.json"
        cfg.write_text(
            json.dumps(
                {
                    "oauthAccount": {"accountUuid": "abc-123"},
                    "mcpServers": {"other": {"command": "foo", "args": ["--bar"]}},
                    "tipsHistory": {"x": 1},
                }
            )
        )
        mod.materialize(cfg, URL, TOKEN)

        doc = json.loads(cfg.read_text())
        assert doc["oauthAccount"] == {"accountUuid": "abc-123"}
        assert doc["mcpServers"]["other"] == {"command": "foo", "args": ["--bar"]}
        assert doc["tipsHistory"] == {"x": 1}
        assert "phantom-gateway" in doc["mcpServers"]

    def test_creates_the_file_when_absent(self, mod, tmp_path):
        cfg = tmp_path / "nested" / ".claude.json"
        mod.materialize(cfg, URL, TOKEN)
        assert _entry(cfg)["url"] == URL

    def test_is_idempotent(self, mod, tmp_path):
        """ttyd can reattach and re-run the wrapper."""
        cfg = tmp_path / ".claude.json"
        mod.materialize(cfg, URL, TOKEN)
        first = cfg.read_text()
        mod.materialize(cfg, URL, TOKEN)
        assert cfg.read_text() == first

    def test_rewrites_a_stale_token(self, mod, tmp_path):
        """Each session gets a freshly minted bearer; the old one must go."""
        cfg = tmp_path / ".claude.json"
        mod.materialize(cfg, URL, "old.token.here")
        mod.materialize(cfg, URL, TOKEN)
        assert _entry(cfg)["headers"]["Authorization"] == f"Bearer {TOKEN}"

    def test_file_is_not_world_readable(self, mod, tmp_path):
        """It now carries a live bearer."""
        cfg = tmp_path / ".claude.json"
        mod.materialize(cfg, URL, TOKEN)
        assert stat.S_IMODE(cfg.stat().st_mode) == 0o600

    def test_leaves_no_temp_file_behind(self, mod, tmp_path):
        cfg = tmp_path / ".claude.json"
        mod.materialize(cfg, URL, TOKEN)
        assert [p.name for p in tmp_path.iterdir()] == [".claude.json"]


class TestRefusesRatherThanCorrupt:
    def test_malformed_config_is_left_untouched(self, mod, tmp_path):
        cfg = tmp_path / ".claude.json"
        cfg.write_text("not json at all")
        with pytest.raises(json.JSONDecodeError):
            mod.materialize(cfg, URL, TOKEN)
        assert cfg.read_text() == "not json at all"

    def test_non_object_config_is_left_untouched(self, mod, tmp_path):
        cfg = tmp_path / ".claude.json"
        cfg.write_text("[1, 2]")
        with pytest.raises(ValueError):
            mod.materialize(cfg, URL, TOKEN)
        assert cfg.read_text() == "[1, 2]"

    @pytest.mark.parametrize(
        "url,token", [("", TOKEN), (URL, ""), ("", "")], ids=["no-url", "no-token", "neither"]
    )
    def test_missing_inputs_write_nothing(self, mod, tmp_path, url, token):
        """Better no entry than one Claude Code fails the whole server on."""
        cfg = tmp_path / ".claude.json"
        with pytest.raises(ValueError):
            mod.materialize(cfg, url, token)
        assert not cfg.exists()


class TestEntryPoint:
    def test_main_reads_the_env_contract(self, mod, tmp_path, monkeypatch):
        cfg = tmp_path / ".claude.json"
        monkeypatch.setenv("CLAUDE_JSON", str(cfg))
        monkeypatch.setenv("PHANTOM_GATEWAY_URL", URL)
        monkeypatch.setenv("PHANTOM_GATEWAY_TOKEN", TOKEN)
        assert mod.main() == 0
        assert _entry(cfg)["url"] == URL

    def test_main_fails_without_a_target(self, mod, monkeypatch):
        monkeypatch.delenv("CLAUDE_JSON", raising=False)
        assert mod.main() == 2

    def test_main_fails_softly_without_the_token(self, mod, tmp_path, monkeypatch):
        """The wrapper only calls this when both vars are set, but a non-zero
        exit (not a traceback into the terminal) is what it handles."""
        monkeypatch.setenv("CLAUDE_JSON", str(tmp_path / ".claude.json"))
        monkeypatch.setenv("PHANTOM_GATEWAY_URL", URL)
        monkeypatch.delenv("PHANTOM_GATEWAY_TOKEN", raising=False)
        assert mod.main() == 1

    def test_token_never_appears_in_an_error_message(self, mod, tmp_path, capsys):
        """Errors land in container logs; a bearer must not ride along."""
        cfg = tmp_path / ".claude.json"
        cfg.write_text("not json")
        os.environ["CLAUDE_JSON"] = str(cfg)
        os.environ["PHANTOM_GATEWAY_URL"] = URL
        os.environ["PHANTOM_GATEWAY_TOKEN"] = TOKEN
        try:
            assert mod.main() == 1
        finally:
            for k in ("CLAUDE_JSON", "PHANTOM_GATEWAY_URL", "PHANTOM_GATEWAY_TOKEN"):
                os.environ.pop(k, None)
        assert TOKEN not in capsys.readouterr().err


class TestWrapperWiring:
    """The script is useless if the image never ships or calls it."""

    def test_dockerfile_copies_the_script(self):
        dockerfile = _SCRIPT.parents[1] / "Dockerfile"
        assert "materialize-gateway-mcp.py" in dockerfile.read_text()

    def test_wrapper_invokes_it_after_sourcing_env(self):
        """Local sessions get the pair via ~/.env, so the call must come after
        that source or the variables are not yet in scope."""
        wrapper = (_SCRIPT.parent / "ttyd-wrapper.sh").read_text()
        assert "materialize-gateway-mcp.py" in wrapper
        source_at = wrapper.index('. "${HOME}/.env"')
        call_at = wrapper.index("materialize-gateway-mcp.py")
        assert source_at < call_at

    def test_wrapper_passes_the_token_by_env_not_argv(self):
        """argv is visible in `ps`; the environment is not."""
        wrapper = (_SCRIPT.parent / "ttyd-wrapper.sh").read_text()
        call_line = next(
            ln for ln in wrapper.splitlines() if "materialize-gateway-mcp.py" in ln
        )
        assert "PHANTOM_GATEWAY_TOKEN" not in call_line
