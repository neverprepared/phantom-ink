"""Guards for the phantom-session container's startup contract.

These live here because this is the suite CI runs for `docker/**` changes; a
shell-only harness does not exist and two files do not justify inventing one.

What they guard, and why each was a real failure:

* A wrong ``PROFILE_ENV_KEY`` used to leave CIPHERTEXT at ``~/.env``. ``openssl
  enc -d`` writes its ``-out`` file even on a bad decrypt and the old form
  guarded only the ``chmod`` with ``&&``. ``BASH_ENV=/home/developer/.env`` then
  made every shell source it, which is how binary bytes ended up being executed
  as commands in a task pane.
* The same swallowed error produced no ``.credentials.json`` ("Not logged in"),
  no ``~/.claude/settings.json`` (which is where the forced bypassPermissions
  mode lives, so an interactive Bypass Permissions dialog appeared), and no
  profile hooks — four unrelated-looking symptoms from one cause.
* ``brainbox-mcp`` is the ONLY source of ``channel_*``/task tools. It is not in
  the gateway catalog and cannot be, since its tools are scoped to the session's
  own bearer. It was installed in the image but never wired into the MCP config,
  so sessions silently had no way to talk to each other.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import stat

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2] / "docker" / "phantom-session"
_WRAPPER = _ROOT / "setup" / "ttyd-wrapper.sh"
_SCRIPT = _ROOT / "setup" / "materialize-mcp.py"


@pytest.fixture(scope="module")
def wrapper() -> str:
    assert _WRAPPER.is_file(), f"{_WRAPPER} is missing — the Dockerfile COPY would break"
    return _WRAPPER.read_text()


@pytest.fixture(scope="module")
def mod():
    assert _SCRIPT.is_file(), f"{_SCRIPT} is missing — the Dockerfile COPY would break"
    spec = importlib.util.spec_from_file_location("materialize_mcp", _SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class TestDecryptIsSafe:
    def test_env_is_never_decrypted_straight_into_place(self, wrapper):
        """A bad decrypt must not leave a sourceable file at ~/.env."""
        assert '-out "$HOME/.env"' not in wrapper, (
            "decrypting directly to ~/.env leaves ciphertext there on a bad key, "
            "and BASH_ENV makes every shell source it"
        )

    def test_decrypt_uses_a_temp_file(self, wrapper):
        assert "_env_tmp" in wrapper

    def test_decrypted_env_is_verified_as_text(self, wrapper):
        """The check that distinguishes plaintext exports from ciphertext."""
        assert "LC_ALL=C grep -qv" in wrapper

    def test_failure_is_reported_not_swallowed(self, wrapper):
        assert wrapper.count("FATAL bad decrypt") >= 2, (
            "both the env and the Claude credential bundle must report a bad decrypt"
        )

    def test_failure_blocks_the_agent_launch(self, wrapper):
        """Launching an unauthenticated agent with a corrupt environment burns a
        task and reports a confusing failure instead of the real cause."""
        assert "PROFILE_DECRYPT_FAILED" in wrapper
        assert "SESSION HALTED" in wrapper


class TestMcpWiring:
    def test_wrapper_invokes_the_materialize_script(self, wrapper):
        assert "materialize-mcp.py" in wrapper

    def test_materialize_runs_after_env_is_sourced(self, wrapper):
        """Local sessions get their env from ~/.env, so the call must come after
        that source or the variables are not yet in scope."""
        assert wrapper.index('. "${HOME}/.env"') < wrapper.index("materialize-mcp.py")

    def test_token_is_passed_by_env_not_argv(self, wrapper):
        line = next(ln for ln in wrapper.splitlines() if "materialize-mcp.py" in ln)
        assert "PHANTOM_GATEWAY_TOKEN" not in line and "BRAINBOX_TOKEN" not in line


class TestMaterialize:
    URL = "https://gateway.example.com/mcp"
    TOKEN = "header.payload.signature"
    HUB = "https://api.example.com"
    HUB_TOKEN = "tok-abc"

    def _env(self, **over):
        env = {
            "PHANTOM_GATEWAY_URL": self.URL,
            "PHANTOM_GATEWAY_TOKEN": self.TOKEN,
            "BRAINBOX_URL": self.HUB,
            "BRAINBOX_TOKEN_ID": self.HUB_TOKEN,
        }
        env.update(over)
        return {k: v for k, v in env.items() if v is not None}

    def test_writes_both_servers(self, mod):
        servers = mod.build_servers(self._env())
        assert sorted(servers) == ["brainbox", "phantom-gateway"]

    def test_no_placeholder_survives(self, mod, tmp_path):
        cfg = tmp_path / ".claude.json"
        mod.materialize(cfg, mod.build_servers(self._env()))
        blob = cfg.read_text()
        assert "${" not in blob
        s = json.loads(blob)["mcpServers"]
        assert s["phantom-gateway"]["headers"]["Authorization"] == f"Bearer {self.TOKEN}"
        # The guest reads BRAINBOX_TOKEN_ID, not BRAINBOX_TOKEN. Both exist in
        # the session env, which is what made wiring the wrong one easy.
        assert s["brainbox"]["env"]["BRAINBOX_TOKEN_ID"] == self.HUB_TOKEN

    def test_brainbox_is_stdio_not_http(self, mod):
        """It runs as a child process with the session's own bearer; it is not a
        gateway-reachable endpoint."""
        b = mod.build_servers(self._env())["brainbox"]
        assert b["type"] == "stdio"
        # Absolute path: an MCP server is spawned with whatever PATH the agent
        # inherited, and the uv tool shim is not guaranteed to be on it.
        assert b["command"] == mod.BRAINBOX_MCP_BIN
        assert b["command"].startswith("/")

    def test_public_hub_url_wins(self, mod):
        """Runner sessions reach the hub by its public name."""
        env = self._env(BRAINBOX_URL=None)
        env["BRAINBOX_HUB_URL_PUBLIC"] = "https://public.example.com"
        assert (
            mod.build_servers(env)["brainbox"]["env"]["BRAINBOX_URL"]
            == "https://public.example.com"
        )

    def test_each_server_is_independent(self, mod):
        """A session with only hub credentials still gets its channel tools."""
        only_hub = mod.build_servers(self._env(PHANTOM_GATEWAY_URL=None, PHANTOM_GATEWAY_TOKEN=None))
        assert sorted(only_hub) == ["brainbox"]
        only_gw = mod.build_servers(self._env(BRAINBOX_URL=None, BRAINBOX_TOKEN_ID=None))
        assert sorted(only_gw) == ["phantom-gateway"]

    def test_preserves_the_rest_of_the_config(self, mod, tmp_path):
        """The same file holds oauth state and the profile's other servers."""
        cfg = tmp_path / ".claude.json"
        cfg.write_text(
            json.dumps(
                {
                    "oauthAccount": {"accountUuid": "abc"},
                    "bypassPermissionsModeAccepted": True,
                    "mcpServers": {"other": {"command": "foo"}},
                }
            )
        )
        mod.materialize(cfg, mod.build_servers(self._env()))
        d = json.loads(cfg.read_text())
        assert d["oauthAccount"] == {"accountUuid": "abc"}
        assert d["bypassPermissionsModeAccepted"] is True
        assert d["mcpServers"]["other"] == {"command": "foo"}

    def test_config_is_not_world_readable(self, mod, tmp_path):
        cfg = tmp_path / ".claude.json"
        mod.materialize(cfg, mod.build_servers(self._env()))
        assert stat.S_IMODE(cfg.stat().st_mode) == 0o600

    def test_refuses_an_unparseable_config(self, mod, tmp_path):
        cfg = tmp_path / ".claude.json"
        cfg.write_text("not json")
        with pytest.raises(json.JSONDecodeError):
            mod.materialize(cfg, mod.build_servers(self._env()))
        assert cfg.read_text() == "not json"

    def test_refuses_with_no_servers(self, mod, tmp_path):
        cfg = tmp_path / ".claude.json"
        with pytest.raises(ValueError):
            mod.materialize(cfg, {})
        assert not cfg.exists()


class TestDockerfileContract:
    def test_dockerfile_ships_both_scripts(self):
        text = (_ROOT / "Dockerfile").read_text()
        assert "materialize-mcp.py" in text
        assert "ttyd-wrapper.sh" in text

    def test_brainbox_mcp_is_installed_and_verified(self):
        """Without it a session has no channel_* tools at all."""
        text = (_ROOT / "Dockerfile").read_text()
        assert "brainbox_mcp-" in text
        assert "command -v brainbox-mcp" in text

    def test_the_bloat_offenders_are_not_reintroduced(self):
        text = (_ROOT / "Dockerfile").read_text()
        for pkg in (
            "mcp-markdown-to-confluence",  # 3.8 GB, 3.4 GB of it @atlaskit
            "mcp-cloudflare-dns",  # already a gateway server
            "mcp-uptime-kuma",  # already a gateway server
            "mcp-remote",  # the gateway IS this
            "chrome-devtools-mcp",  # dropped by operator decision
        ):
            assert f"npm install -g {pkg}" not in text
            assert f"        {pkg} \\" not in text, f"{pkg} re-added to the image"

    def test_only_one_version_of_each_toolchain(self):
        text = (_ROOT / "Dockerfile").read_text()
        assert "nvm install 20" not in text
        assert "goenv install 1.21" not in text
        assert "uv python install 3.12 3.11" not in text


class TestMcpVersionPin:
    """mcp 2.x removed `mcp.server.fastmcp`.

    The session image was built when 1.x was current, so the unpinned
    `mcp[cli]>=1.9` resolved fine for weeks. The first fresh rebuild resolved
    2.3.0, brainbox-mcp exited 1 on import, and every session lost its
    `channel_*` tools with Claude Code reporting only `CONNECTION_CLOSED`.
    """

    def test_brainbox_mcp_pins_mcp_below_2(self):
        pyproject = (
            pathlib.Path(__file__).resolve().parents[2]
            / "packages" / "brainbox-mcp" / "pyproject.toml"
        ).read_text()
        assert '"mcp[cli]>=1.9,<2"' in pyproject, (
            "the upper bound is load-bearing: mcp 2.x removes mcp.server.fastmcp"
        )

    def test_image_verifies_the_import_not_just_the_binary(self):
        """`command -v brainbox-mcp` passed while the server was unstartable."""
        dockerfile = (_ROOT / "Dockerfile").read_text()
        assert "import mcp.server.fastmcp" in dockerfile
