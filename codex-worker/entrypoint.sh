#!/bin/sh
set -eu

mkdir -p /root/.codex/skills/marketing-scheduler /root/.codex/skills/playwright-browser /root/.codex/skills/google-ads /root/.codex/skills/hongxiu-weekly-product-email /root/.codex/skills/shopify-analytics /root/.codex/skills/zoho-api /root/.zoho-api /workspace /workspace/codex-artifacts /browser-data/profile
cp -R /opt/codex-worker/bundled-skills/marketing-scheduler/. /root/.codex/skills/marketing-scheduler/
cp -R /opt/codex-worker/bundled-skills/playwright-browser/. /root/.codex/skills/playwright-browser/
cp -R /opt/codex-worker/bundled-skills/google-ads/. /root/.codex/skills/google-ads/
cp -R /opt/codex-worker/bundled-skills/hongxiu-weekly-product-email/. /root/.codex/skills/hongxiu-weekly-product-email/
cp -R /opt/codex-worker/bundled-skills/shopify-analytics/. /root/.codex/skills/shopify-analytics/
cp -R /opt/codex-worker/bundled-skills/zoho-api/. /root/.codex/skills/zoho-api/

if [ -n "${ZOHO_REGION:-}" ] && [ -n "${ZOHO_CLIENT_ID:-}" ] && [ -n "${ZOHO_CLIENT_SECRET:-}" ] && [ -n "${ZOHO_REFRESH_TOKEN:-}" ]; then
    umask 077
    {
        printf 'ZOHO_REGION=%s\n' "$ZOHO_REGION"
        printf 'ZOHO_CLIENT_ID=%s\n' "$ZOHO_CLIENT_ID"
        printf 'ZOHO_CLIENT_SECRET=%s\n' "$ZOHO_CLIENT_SECRET"
        printf 'ZOHO_REFRESH_TOKEN=%s\n' "$ZOHO_REFRESH_TOKEN"
    } > /root/.zoho-api/.env
    chmod 600 /root/.zoho-api/.env
else
    echo "WARNING: Zoho API credentials are incomplete; zoho-api skill will be installed but authentication will be unavailable." >&2
fi

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
