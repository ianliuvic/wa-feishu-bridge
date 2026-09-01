#!/bin/sh
set -eu

mkdir -p /root/.codex/skills/marketing-scheduler /root/.codex/skills/playwright-browser /root/.codex/skills/google-ads /workspace /workspace/codex-artifacts /browser-data/profile
cp -R /opt/codex-worker/bundled-skills/marketing-scheduler/. /root/.codex/skills/marketing-scheduler/
cp -R /opt/codex-worker/bundled-skills/playwright-browser/. /root/.codex/skills/playwright-browser/
cp -R /opt/codex-worker/bundled-skills/google-ads/. /root/.codex/skills/google-ads/

if ! grep -q '^\[mcp_servers\.playwright\]' /root/.codex/config.toml 2>/dev/null; then
    cat >>/root/.codex/config.toml <<'EOF'

[mcp_servers.playwright]
command = "/usr/local/bin/playwright-mcp"
args = ["--headless", "--no-sandbox", "--caps", "vision", "--user-data-dir", "/browser-data/profile", "--output-dir", "/workspace/codex-artifacts", "--viewport-size", "1440x900", "--timeout-action", "15000"]
startup_timeout_sec = 60
tool_timeout_sec = 120
EOF
fi

python3 /opt/codex-worker/configure_google_ads.py

echo "Codex Worker API ready: $(codex --version)"
exec uvicorn --app-dir /opt/codex-worker server:app --host 0.0.0.0 --port 80
