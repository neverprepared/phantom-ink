# syntax=docker/dockerfile:1
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

# Build-time assets consumed in RUN layers (chown 1001 = developer, assigned after ubuntu:1000)
COPY --chown=1001:1001 docker/brainbox/hooks-requirements.txt /tmp/hooks-requirements.txt
COPY --chown=1001:1001 packages/brainbox-mcp/dist             /tmp/brainbox-mcp-dist

# =============================================================================
# Root: system packages, user, cloud CLIs, static binaries
# One RUN keeps intermediate state out of layers; BuildKit cache mounts keep
# apt download caches out of the image entirely.
# =============================================================================
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
      build-essential ca-certificates curl file git jq less \
      libssl-dev locales openssh-client pkg-config procps \
      sudo tmux ttyd unzip vim wget zip \
    && locale-gen en_US.UTF-8 \
    # Drop docs/man pages installed by apt — not needed in a container image
    && rm -rf /usr/share/doc /usr/share/man /usr/share/info \
    # developer user
    && useradd -m -s /bin/bash developer \
    && echo 'developer ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/developer \
    # Homebrew prefix — must be owned by developer before the installer runs
    && mkdir -p /home/linuxbrew/.linuxbrew \
    && chown -R developer:developer /home/linuxbrew \
    # Pre-create dirs that BuildKit cache mounts would otherwise create as root
    && mkdir -p /home/developer/.cache /home/developer/.local/bin \
    && chown -R developer:developer /home/developer/.cache /home/developer/.local \
    # Playwright browser dir (writable by developer via npx playwright install)
    && mkdir -p /ms-playwright && chmod 777 /ms-playwright \
    # AWS CLI
    && ARCH=$(uname -m) \
    && curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-${ARCH}.zip" -o /tmp/awscliv2.zip \
    && unzip -q /tmp/awscliv2.zip -d /tmp/aws-install \
    && /tmp/aws-install/aws/install \
    && rm -rf /tmp/awscliv2.zip /tmp/aws-install \
    # Azure CLI (adds Microsoft apt source and installs azure-cli)
    && curl -sL https://aka.ms/InstallAzureCLIDeb | bash \
    # websocat — SSH agent WebSocket relay for remote Docker mode
    && WSARCH=$(case "$(uname -m)" in \
         x86_64)  echo x86_64-unknown-linux-musl ;; \
         aarch64) echo aarch64-unknown-linux-musl ;; \
         *) echo "Unsupported arch: $(uname -m)" >&2 && exit 1 ;; \
       esac) \
    && curl -fsSL "https://github.com/vi/websocat/releases/latest/download/websocat.${WSARCH}" \
       -o /usr/local/bin/websocat \
    && chmod +x /usr/local/bin/websocat

# Developer-owned static assets (COPY after useradd so developer name resolves)
COPY --chown=developer:developer docker/brainbox/setup/.bashrc         /tmp/.bashrc.extra
COPY --chown=developer:developer docker/brainbox/setup/ttyd-wrapper.sh /home/developer/ttyd-wrapper.sh
COPY --chown=developer:developer reflex/plugins/reflex                  /opt/reflex/share/reflex

# =============================================================================
# Developer: all language runtimes, tools, MCP servers, Claude Code
#
# BuildKit cache mounts prevent download caches from landing in the image:
#   - /var/cache/apt + /var/lib/apt  → apt (for playwright --with-deps)
#   - ~/.cache/Homebrew              → brew downloads
#   - ~/.cache/uv                    → uv package downloads
#   - ~/.npm                         → npm package downloads
#   - ~/.nvm/cache                   → nvm Node tarballs
#   - ~/.goenv/cache                 → goenv Go distributions
#   - ~/.rustup/downloads            → rustup toolchain downloads
# =============================================================================
USER developer
WORKDIR /home/developer

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    --mount=type=cache,target=/home/developer/.cache/Homebrew,uid=1001,gid=1001,sharing=locked \
    --mount=type=cache,target=/home/developer/.cache/uv,uid=1001,gid=1001,sharing=locked \
    --mount=type=cache,target=/home/developer/.npm,uid=1001,gid=1001 \
    --mount=type=cache,target=/home/developer/.rustup/downloads,uid=1001,gid=1001 \
    # ---- Homebrew ----------------------------------------------------------
    NONINTERACTIVE=1 bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
    && brew install fd fzf gh ripgrep tree yq kubectl hashicorp/tap/terraform neverprepared/tap/pbrainctl \
    # ---- Python (uv) -------------------------------------------------------
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && uv python install 3.12 3.11 \
    && ln -sf "$(uv python find 3.12)" ~/.local/bin/python3 \
    && ln -sf "$(uv python find 3.12)" ~/.local/bin/python \
    # ---- Node.js (nvm) -----------------------------------------------------
    && curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash \
    && bash -c '. "$NVM_DIR/nvm.sh" && nvm install 22 && nvm install 20 && nvm alias default 22' \
    && bash -c '. "$NVM_DIR/nvm.sh" && ln -sf "$(dirname "$(nvm which default)")" "$NVM_DIR/default_bin"' \
    # ---- Go (goenv — shallow clone strips ~45 MB of history) ---------------
    && git clone --depth=1 https://github.com/go-nv/goenv.git "$GOENV_ROOT" \
    && goenv install 1.22.5 && goenv install 1.21.12 && goenv global 1.22.5 && goenv rehash \
    # ---- Rust (rustup — drop docs to save ~500 MB) -------------------------
    && curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
       | sh -s -- -y --default-toolchain stable \
    && rustup component remove rust-docs 2>/dev/null || true \
    # ---- Azure credential dir ----------------------------------------------
    && mkdir -p ~/.azure \
    # ---- Shell config ------------------------------------------------------
    && cat /tmp/.bashrc.extra >> ~/.bashrc && rm /tmp/.bashrc.extra \
    && printf '%s\n' \
       '' \
       '# nvm' \
       'export NVM_DIR="$HOME/.nvm"' \
       '[ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh"' \
       '[ -s "$NVM_DIR/bash_completion" ] && source "$NVM_DIR/bash_completion"' \
       '' \
       '# goenv' \
       'export GOENV_ROOT="$HOME/.goenv"' \
       'eval "$(goenv init -)"' \
       '' \
       '# uv shell completions' \
       'eval "$(uv generate-shell-completion bash 2>/dev/null)"' \
       '' \
       '# rustup shell completions' \
       'eval "$(rustup completions bash 2>/dev/null)"' \
       >> ~/.bashrc \
    && chmod +x ~/ttyd-wrapper.sh \
    && find /opt/reflex/share/reflex/scripts -name "*.sh" -o -name "*.py" 2>/dev/null \
       | xargs chmod 0755 2>/dev/null || true \
    # ---- Claude Code -------------------------------------------------------
    && curl -fsSL https://claude.ai/install.sh | bash \
    # ---- OpenAI Codex CLI (best-effort) ------------------------------------
    && (bash -c '. "$NVM_DIR/nvm.sh" && npm install -g @openai/codex' || true) \
    # ---- Playwright chromium (--with-deps uses passwordless sudo for apt) --
    && bash -c '. "$NVM_DIR/nvm.sh" && npx --yes playwright install chromium --with-deps' \
    # ---- npm MCP servers ---------------------------------------------------
    && bash -c '. "$NVM_DIR/nvm.sh" && npm install -g \
        @playwright/mcp \
        @modelcontextprotocol/server-memory \
        @modelcontextprotocol/server-filesystem \
        @modelcontextprotocol/server-github \
        chrome-devtools-mcp \
        mcp-remote \
        "@azure/mcp@2.0.0-beta.10" \
        "@azure-devops/mcp@2.2.2" \
        "mssql-mcp@2.1.1" \
        "@microsoft/devbox-mcp@0.0.3-alpha.4" \
        "kubernetes-mcp-server@0.0.56" \
        "argocd-mcp@0.5.0" \
        @neverprepared/mcp-markdown-to-confluence \
        @neverprepared/mcp-obsidian-second-brain \
        @neverprepared/mcp-cloudflare-dns \
        @neverprepared/mcp-uptime-kuma \
        @neverprepared/mcp-new-relic' \
    # ---- Python MCP servers + hook deps + brainbox-mcp --------------------
    && uvx --python 3.12 --with-requirements /tmp/hooks-requirements.txt \
         python -c "import langfuse; print('hook deps ok')" \
    && uv tool install --python 3.12 'mcp-atlassian==0.13.0' \
    && uv tool install --python 3.12 'mcp-server-git==2025.12.18' \
    && uv tool install --python 3.12 markitdown-mcp \
    && uv tool install --python 3.12 'workspace-mcp==1.7.1' \
    && uv tool install --python 3.12 \
         --from 'git+https://github.com/neverprepared/kuma_uptime_mcp.git' mcp-uptime-kuma \
    && uv tool install --python 3.12 /tmp/brainbox-mcp-dist/brainbox_mcp-*.whl \
    && rm -rf /tmp/brainbox-mcp-dist /tmp/hooks-requirements.txt \
    # ---- Pre-trust common working directories ------------------------------
    # The canonical config file lives at ~/.claude/.claude.json — this mirrors
    # the host CLAUDE_CONFIG_DIR layout ($WORKSPACE_HOME/.claude/.claude.json)
    # and the ttyd-wrapper decrypt path, which writes profile config there.
    # Claude Code inside the container reads ~/.claude.json (CLAUDE_CONFIG_DIR is
    # not set in profile images — builder.go strips it as host-only), so we
    # copy the canonical file to ~/.claude.json. It must be a real file — Claude
    # Code does not reliably honor a symlinked ~/.claude.json. The runtime
    # decrypt (ttyd-wrapper.sh) re-copies it with the profile's live config.
    && mkdir -p ~/.claude \
    && if [ -f ~/.claude.json ] && [ ! -L ~/.claude.json ]; then \
         mv ~/.claude.json ~/.claude/.claude.json ; \
       fi \
    && if [ -f ~/.claude/.claude.json ]; then \
         jq '. + {hasCompletedOnboarding:true,bypassPermissionsModeAccepted:true} \
             | .projects["/home/developer/workspace"] = \
                 (.projects["/home/developer/workspace"] // {} | . + {hasTrustDialogAccepted:true}) \
             | .projects["/home/developer/task-repo"] = \
                 {allowedTools:[],mcpContextUris:[],hasTrustDialogAccepted:true,projectOnboardingSeenCount:0} \
             | .projects["/home/developer"] = \
                 {allowedTools:[],mcpContextUris:[],hasTrustDialogAccepted:true,projectOnboardingSeenCount:0}' \
             ~/.claude/.claude.json > /tmp/.claude.json.tmp \
         && mv /tmp/.claude.json.tmp ~/.claude/.claude.json ; \
       else \
         printf '%s' \
           '{"hasCompletedOnboarding":true,"bypassPermissionsModeAccepted":true,"projects":{' \
           '"/home/developer/workspace":{"allowedTools":[],"mcpContextUris":[],"hasTrustDialogAccepted":true,"projectOnboardingSeenCount":0},' \
           '"/home/developer/task-repo":{"allowedTools":[],"mcpContextUris":[],"hasTrustDialogAccepted":true,"projectOnboardingSeenCount":0},' \
           '"/home/developer":{"allowedTools":[],"mcpContextUris":[],"hasTrustDialogAccepted":true,"projectOnboardingSeenCount":0}}}' \
           > ~/.claude/.claude.json ; \
       fi \
    && cp -f ~/.claude/.claude.json ~/.claude.json

WORKDIR /home/developer/workspace

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD pgrep -x "claude" > /dev/null || pgrep -x "codex" > /dev/null || pgrep -x "ollama" > /dev/null || exit 1

CMD ["/bin/bash", "-l"]
