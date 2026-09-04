#!/bin/sh
set -eu

mkdir -p /root/.codex/skills/crun-agent-skills /workspace/canvas /workspace/.infinite-canvas /workspace/codex-artifacts/crun
cp -R /opt/codex-worker/bundled-skills/crun-agent-skills/. /root/.codex/skills/crun-agent-skills/
if ! command -v python >/dev/null 2>&1; then
    ln -s "$(command -v python3)" /usr/local/bin/python
fi

# Preserve the browser MCP bootstrapping installed on the existing worker.
for script in /etc/profile.d/zz-playwright-browser.sh /etc/profile.d/zzz-playwright-browser-runtime-v2.sh; do
    if [ -f "$script" ]; then
        . "$script"
    fi
done

export CANVAS_AGENT_TOKEN="${CANVAS_AGENT_TOKEN:-${CODEX_WORKER_TOKEN:-}}"
export CANVAS_AGENT_HOST="127.0.0.1"
export CANVAS_AGENT_PUBLIC_URL="${CANVAS_AGENT_PUBLIC_URL:-https://codex-worker.yiswim.cloud/canvas-agent}"
export CANVAS_AGENT_ALLOWED_ORIGINS="${CANVAS_AGENT_ALLOWED_ORIGINS:-https://canvas.yiswim.cloud}"
export CANVAS_AGENT_CONFIG_DIR="${CANVAS_AGENT_CONFIG_DIR:-/workspace/.infinite-canvas}"
export CANVAS_AGENT_WORKSPACE="${CANVAS_AGENT_WORKSPACE:-/workspace/canvas}"

if [ -z "$CANVAS_AGENT_TOKEN" ]; then
    echo "CANVAS_AGENT_TOKEN or CODEX_WORKER_TOKEN must be configured" >&2
    exit 1
fi

uvicorn --app-dir /opt/codex-worker server:app --host 127.0.0.1 --port 8000 &
worker_pid=$!

PORT=17371 node /opt/infinite-canvas/canvas-agent/dist/index.js &
agent_pid=$!

cleanup() {
    kill "$worker_pid" "$agent_pid" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

nginx -g 'daemon off;'
