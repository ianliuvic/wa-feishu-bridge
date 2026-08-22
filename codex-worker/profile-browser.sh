#!/bin/sh

mkdir -p /root/.codex /root/.codex/skills/playwright-browser /browser-data/profile /workspace/codex-artifacts
touch /root/.codex/config.toml

if ! grep -q '^\[mcp_servers\.playwright\]' /root/.codex/config.toml 2>/dev/null; then
    cat >>/root/.codex/config.toml <<'EOF'

[mcp_servers.playwright]
command = "/usr/local/bin/playwright-mcp"
args = ["--headless", "--no-sandbox", "--caps", "vision", "--user-data-dir", "/browser-data/profile", "--output-dir", "/workspace/codex-artifacts", "--viewport-size", "1440x900", "--timeout-action", "15000"]
startup_timeout_sec = 60
tool_timeout_sec = 120
EOF
fi
