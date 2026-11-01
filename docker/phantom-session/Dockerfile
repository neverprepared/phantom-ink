# syntax=docker/dockerfile:1

# =============================================================================
# phantom-session-base — the shared base for every session container.
#
# Built as an ALLOWLIST: only what the wrapper invokes, the agent runtimes, the
# platform CLIs, and one version of each language the repos agents work in
# actually use. The predecessor (docker/brainbox/Dockerfile) grew to 9.22 GB in
# a SINGLE layer, which exceeds the registry proxy's 2 GB body cap and made the
# image unpushable; see
# docs/superpowers/specs/2026-10-31-session-image-slimming-design.md.
#
# TWO RULES when editing this file:
#   1. No single RUN may produce a layer over 2 GB. One RUN per concern.
#   2. MCP servers belong here ONLY if they need the session's own filesystem,
#      identity, or network namespace. Everything else goes in the gateway
#      catalog (phantom-platform/config/mcp-catalog.json).
#
# The old brainbox/brainbox-profile images are deliberately left alone as the
# rollback path. Nothing here overwrites them.
# =============================================================================

# -----------------------------------------------------------------------------
# Builder stages: compile the private neverprepared CLIs from local checkouts.
# Source arrives via buildx NAMED BUILD CONTEXTS, so nothing is cloned in-build
# (no PAT) and nothing is hosted. REQUIRES BuildKit:
#   --build-context phantombrain=../phantom-brain
#   --build-context prouterctl=../prouterctl        <-- its OWN repo, NOT inside
#                                                       phantom-router
# Omitting these makes BuildKit resolve the names as Docker Hub images and fail
# with a misleading "insufficient_scope: authorization failed".
# -----------------------------------------------------------------------------
FROM golang:1.26 AS pbrainctl-build
ARG PBRAINCTL_VERSION=container
ENV CGO_ENABLED=1 GOTOOLCHAIN=auto
COPY --from=phantombrain . /src
WORKDIR /src
RUN --mount=type=cache,target=/root/.cache/go-build \
    --mount=type=cache,target=/go/pkg/mod \
    go build -tags=sqlite_fts5 \
      -ldflags="-s -w -X github.com/neverprepared/phantom-brain/internal/version.Version=${PBRAINCTL_VERSION}" \
      -o /out/pbrainctl ./cmd/pbrainctl

FROM golang:1.26 AS prouterctl-build
ENV CGO_ENABLED=0 GOTOOLCHAIN=auto
COPY --from=prouterctl . /src
WORKDIR /src
RUN --mount=type=cache,target=/root/.cache/go-build \
    --mount=type=cache,target=/go/pkg/mod \
    go build -ldflags="-s -w" -o /out/prouterctl .

# =============================================================================
# Final image
# =============================================================================
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=en_US.UTF-8 \
    LANGUAGE=en_US:en \
    LC_ALL=en_US.UTF-8 \
    HOMEBREW_NO_AUTO_UPDATE=1 \
    HOMEBREW_ALLOWED_TAPS="hashicorp/tap neverprepared/tap" \
    NVM_DIR=/home/developer/.nvm \
    GOENV_ROOT=/home/developer/.goenv \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    OLLAMA_BASE_URL=http://localhost:11434 \
    OLLAMA_HOST=http://host.docker.internal:11434 \
    BASH_ENV=/home/developer/.env \
    DISABLE_AUTOUPDATER=1 \
    PATH="/home/linuxbrew/.linuxbrew/bin:/home/linuxbrew/.linuxbrew/sbin:/home/developer/.local/bin:/home/developer/.nvm/default_bin:/home/developer/.goenv/bin:/home/developer/.goenv/shims:/home/developer/.cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

COPY --chown=1001:1001 docker/phantom-session/hooks-requirements.txt /tmp/hooks-requirements.txt
COPY --chown=1001:1001 packages/brainbox-mcp/dist                    /tmp/brainbox-mcp-dist

# -----------------------------------------------------------------------------
# LAYER 1 — system packages, user, cloud CLIs, static binaries
# -----------------------------------------------------------------------------
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
      build-essential ca-certificates curl file git jq less \
      libssl-dev locales openssh-client pkg-config procps \
      sudo tmux ttyd unzip vim wget zip zstd \
    && locale-gen en_US.UTF-8 \
    && rm -rf /usr/share/doc /usr/share/man /usr/share/info \
    && useradd -m -s /bin/bash developer \
    && echo 'developer ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/developer \
    && mkdir -p /home/linuxbrew/.linuxbrew \
    && chown -R developer:developer /home/linuxbrew \
    && mkdir -p /home/developer/.cache /home/developer/.local/bin \
    && chown -R developer:developer /home/developer/.cache /home/developer/.local \
    && mkdir -p /ms-playwright && chmod 777 /ms-playwright \
    # AWS CLI
    && ARCH=$(uname -m) \
    && curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-${ARCH}.zip" -o /tmp/awscliv2.zip \
    && unzip -q /tmp/awscliv2.zip -d /tmp/aws-install \
    && /tmp/aws-install/aws/install \
    && rm -rf /tmp/awscliv2.zip /tmp/aws-install \
    # Azure CLI
    && curl -sL https://aka.ms/InstallAzureCLIDeb | bash \
    # websocat — SSH agent WebSocket relay for remote Docker mode
    && WSARCH=$(case "$(uname -m)" in \
         x86_64)  echo x86_64-unknown-linux-musl ;; \
         aarch64) echo aarch64-unknown-linux-musl ;; \
         *) echo "Unsupported arch: $(uname -m)" >&2 && exit 1 ;; \
       esac) \
    && curl -fsSL "https://github.com/vi/websocat/releases/latest/download/websocat.${WSARCH}" \
       -o /usr/local/bin/websocat \
    && chmod +x /usr/local/bin/websocat \
    # Ollama CLI (client only; sessions talk to the HOST daemon via OLLAMA_HOST)
    && OARCH=$(dpkg --print-architecture) \
    && curl -fsSL "https://github.com/ollama/ollama/releases/latest/download/ollama-linux-${OARCH}.tar.zst" \
       | tar --zstd -x -C /usr/local --wildcards '*bin/ollama' \
    && chmod 755 /usr/local/bin/ollama

# -----------------------------------------------------------------------------
# Developer-owned static assets
# -----------------------------------------------------------------------------
COPY --chown=developer:developer docker/phantom-session/setup/.bashrc         /tmp/.bashrc.extra
COPY --chown=developer:developer docker/phantom-session/setup/ttyd-wrapper.sh /home/developer/ttyd-wrapper.sh
# Substitutes real values into the baked MCP declarations at container start:
# Claude Code expands ${VAR} in an MCP entry's url but NOT in its headers, so
# bearers must be written in literally.
COPY --chown=developer:developer docker/phantom-session/setup/materialize-mcp.py /home/developer/materialize-mcp.py
COPY --chown=developer:developer docker/phantom-session/setup/git-hooks        /opt/git-hooks
COPY docker/phantom-session/setup/complete.sh  /usr/local/bin/brainbox-complete
COPY docker/phantom-session/setup/ollama-mcp.py /usr/local/bin/ollama-mcp
COPY --from=pbrainctl-build  /out/pbrainctl  /usr/local/bin/pbrainctl
COPY --from=prouterctl-build /out/prouterctl /usr/local/bin/prouterctl
RUN chmod 755 /usr/local/bin/brainbox-complete /usr/local/bin/ollama-mcp \
              /usr/local/bin/pbrainctl /usr/local/bin/prouterctl

USER developer
WORKDIR /home/developer

# -----------------------------------------------------------------------------
# LAYER 2 — Homebrew CLI tools
# brew's own git history is ~142 MB of no value in an image; dropped here.
# -----------------------------------------------------------------------------
RUN --mount=type=cache,target=/home/developer/.cache/Homebrew,uid=1001,gid=1001,sharing=locked \
    NONINTERACTIVE=1 bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
    && brew install fd fzf gh gitleaks ripgrep tree yq kubectl hashicorp/tap/terraform \
    && brew cleanup -s \
    && rm -rf "$(brew --repo)/.git" "$(brew --cache)" 2>/dev/null || true

# -----------------------------------------------------------------------------
# LAYER 3 — Python via uv. ONE version: uv fetches others on demand, and the
# ~/.cache/uv volume mount makes that cheap at runtime.
# -----------------------------------------------------------------------------
RUN --mount=type=cache,target=/home/developer/.cache/uv,uid=1001,gid=1001,sharing=locked \
    curl -LsSf https://astral.sh/uv/install.sh | sh \
    && uv python install 3.12 \
    && ln -sf "$(uv python find 3.12)" ~/.local/bin/python3 \
    && ln -sf "$(uv python find 3.12)" ~/.local/bin/python

# -----------------------------------------------------------------------------
# LAYER 4 — Node (ONE version; required by claude and codex anyway) + the agent
# runtimes. Node 20 was carried for years with no identified consumer.
# -----------------------------------------------------------------------------
# NOTE: do NOT add a cache mount under $NVM_DIR. BuildKit creates the mount's
# parent directories, so mounting $NVM_DIR/.cache makes $NVM_DIR exist before
# nvm's installer runs and the install fails with "Failed to initialize nvm
# repo". The nvm tarball cache is removed at the end of this layer instead.
RUN --mount=type=cache,target=/home/developer/.npm,uid=1001,gid=1001 \
    curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash \
    && bash -c '. "$NVM_DIR/nvm.sh" && nvm install 22 && nvm alias default 22' \
    && bash -c '. "$NVM_DIR/nvm.sh" && ln -sf "$(dirname "$(nvm which default)")" "$NVM_DIR/default_bin"' \
    && curl -fsSL https://claude.ai/install.sh | bash \
    && (bash -c '. "$NVM_DIR/nvm.sh" && npm install -g @openai/codex' || true) \
    && rm -rf "$NVM_DIR/.cache" ~/.npm/_cacache 2>/dev/null || true \
    # Verify before moving on: a silent nvm failure here previously surfaced
    # four layers later as a playwright error, which is a miserable way to
    # learn that node is missing.
    && bash -lc 'command -v node && node --version' \
    && bash -lc 'command -v claude && claude --version'

# -----------------------------------------------------------------------------
# LAYER 5 — Go (ONE version). GOTOOLCHAIN=auto lets a module's own `toolchain`
# directive fetch what it needs, so the baked version is a floor, not a ceiling.
# -----------------------------------------------------------------------------
RUN git clone --depth=1 https://github.com/go-nv/goenv.git "$GOENV_ROOT" \
    && goenv install 1.25.3 && goenv global 1.25.3 && goenv rehash \
    && rm -rf "$GOENV_ROOT/cache" 2>/dev/null || true \
    && bash -lc 'command -v go && go version'

# -----------------------------------------------------------------------------
# LAYER 6 — Rust. KEPT: last-inbox and password-manager are Rust repos, so
# dropping it would break real work. Docs removed (~500 MB).
# -----------------------------------------------------------------------------
# NOTE: do NOT add a cache mount under ~/.rustup — BuildKit creates the
# parent as root and rustup then fails with "could not write settings file:
# .rustup/settings.toml: Permission denied". Downloads are pruned below instead.
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable \
    && rustup component remove rust-docs 2>/dev/null || true \
    && rm -rf ~/.rustup/downloads ~/.rustup/tmp 2>/dev/null || true \
    && bash -lc 'command -v cargo && cargo --version'

# -----------------------------------------------------------------------------
# LAYER 7 — browser. KEPT IN-SESSION on purpose: the standalone gateway
# container has NO browser, so in-session is the only place browser automation
# currently works. ~680 MB; revisit only together with provisioning a browser
# gateway-side (spec TODO-2).
# -----------------------------------------------------------------------------
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    bash -c '. "$NVM_DIR/nvm.sh" && npx --yes playwright install chromium --with-deps' \
    && test -n "$(find /ms-playwright -name chrome -type f -perm -u+x -print -quit)" \
    && echo "browser present in /ms-playwright"

# -----------------------------------------------------------------------------
# LAYER 8 — SESSION-LOCAL MCP servers ONLY.
#
# Each one here needs the session's own filesystem, identity or workspace.
# Everything that merely calls a remote API with a profile-scoped credential
# lives in the gateway catalog instead — that is the ADR-002 boundary, and it is
# what keeps this image from growing back to 9 GB.
#
#   brainbox-mcp      the session's own hub identity: channel_* (conversations),
#                     tasks, agents. CANNOT be gateway-side — the tools are
#                     scoped to THIS session's bearer token.
#   server-filesystem the session's workspace files
#   mcp-server-git    the session's checkout
#   markitdown-mcp    converts files on the session's own disk
#   @playwright/mcp   drives the browser in LAYER 7 (the gateway has none)
#
# NOT here, and why — do not re-add without re-reading the spec:
#   markdown-to-confluence  3.8 GB, of which 3.4 GB is @atlaskit pulled
#                           transitively for one ADF helper. Replaceable with
#                           kroki + existing Atlassian tooling.
#   cloudflare-dns, uptime-kuma, workspace-mcp   already in the gateway catalog
#   new-relic, argocd, kubernetes, mssql, azure, azure-devops, devbox, github,
#   atlassian                                    remote APIs -> gateway catalog
#   mcp-remote              is a proxy to remote MCP servers; that IS the gateway
#   chrome-devtools-mcp     dropped by operator decision
#   server-memory           superseded by phantom-brain
# -----------------------------------------------------------------------------
RUN --mount=type=cache,target=/home/developer/.npm,uid=1001,gid=1001 \
    --mount=type=cache,target=/home/developer/.cache/uv,uid=1001,gid=1001,sharing=locked \
    bash -c '. "$NVM_DIR/nvm.sh" && npm install -g \
        @playwright/mcp \
        @modelcontextprotocol/server-filesystem' \
    && uvx --python 3.12 --with-requirements /tmp/hooks-requirements.txt \
         python -c "import requests; print('hook deps ok')" \
    && uv tool install --python 3.12 'mcp-server-git==2025.12.18' \
    && uv tool install --python 3.12 markitdown-mcp \
    && uv tool install --python 3.12 /tmp/brainbox-mcp-dist/brainbox_mcp-*.whl \
    && rm -rf /tmp/brainbox-mcp-dist /tmp/hooks-requirements.txt \
    && rm -rf ~/.npm/_cacache 2>/dev/null || true \
    # brainbox-mcp is the ONLY source of channel_*/task tools — the gateway
    # catalog does not contain it and cannot, since its tools are scoped to the
    # session's own bearer. If it is missing, sessions silently cannot talk to
    # each other, which is exactly the failure this image is meant to end.
    && bash -lc 'command -v brainbox-mcp' \
    # `command -v` is NOT enough: the binary existed while the server could not
    # start at all. mcp 2.x removed mcp.server.fastmcp, so an unpinned resolve
    # yields a shim that exits 1 on import and Claude Code reports only
    # CONNECTION_CLOSED. Import the module the server actually needs, inside
    # the tool's own venv, so a bad resolve fails the BUILD instead of every
    # session.
    && /home/developer/.local/share/uv/tools/brainbox-mcp/bin/python -c \
         'import mcp.server.fastmcp; print("brainbox-mcp imports OK")' \
    && bash -lc 'command -v mcp-server-git && command -v markitdown-mcp'

# -----------------------------------------------------------------------------
# LAYER 9 — shell config, git hooks, and the pre-accepted Claude config.
# -----------------------------------------------------------------------------
RUN cat /tmp/.bashrc.extra >> ~/.bashrc && rm /tmp/.bashrc.extra \
    && printf '%s\n' \
       '' \
       '# nvm' \
       'export NVM_DIR="$HOME/.nvm"' \
       '[ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh"' \
       '[ -s "$NVM_DIR/bash_completion" ] && source "$NVM_DIR/bash_completion"' \
       '' \
       '# goenv' \
       'export GOENV_ROOT="$HOME/.goenv"' \
       'export PATH="$GOENV_ROOT/bin:$PATH"' \
       'command -v goenv >/dev/null 2>&1 && eval "$(goenv init -)"' \
       '' \
       '# uv shell completions' \
       'eval "$(uv generate-shell-completion bash 2>/dev/null)"' \
       '' \
       '# rustup shell completions' \
       'eval "$(rustup completions bash 2>/dev/null)"' \
       >> ~/.bashrc \
    && chmod +x ~/ttyd-wrapper.sh \
    && chmod 0755 /opt/git-hooks/pre-commit \
    && git config --global core.hooksPath /opt/git-hooks \
    && mkdir -p ~/.azure ~/.claude \
    # Pre-accept onboarding, Bypass Permissions and directory trust. Without
    # these a headless `claude --dangerously-skip-permissions` stalls on an
    # interactive warning. The runtime decrypt re-copies the profile's live
    # config over this and ttyd-wrapper.sh re-asserts the same flags, because
    # the decrypted host config does not carry them.
    && printf '%s' \
       '{"hasCompletedOnboarding":true,"bypassPermissionsModeAccepted":true,"projects":{' \
       '"/home/developer/workspace":{"allowedTools":[],"mcpContextUris":[],"hasTrustDialogAccepted":true,"projectOnboardingSeenCount":0},' \
       '"/home/developer/task-repo":{"allowedTools":[],"mcpContextUris":[],"hasTrustDialogAccepted":true,"projectOnboardingSeenCount":0},' \
       '"/home/developer":{"allowedTools":[],"mcpContextUris":[],"hasTrustDialogAccepted":true,"projectOnboardingSeenCount":0}}}' \
       > ~/.claude/.claude.json \
    && cp -f ~/.claude/.claude.json ~/.claude.json \
    && chmod 600 ~/.claude/.claude.json ~/.claude.json

WORKDIR /home/developer/workspace

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD pgrep -x "claude" > /dev/null || pgrep -x "codex" > /dev/null || pgrep -x "ollama" > /dev/null || exit 1

CMD ["/bin/bash", "-l"]
