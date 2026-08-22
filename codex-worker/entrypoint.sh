#!/bin/sh
set -eu

mkdir -p /root/.codex/skills/marketing-scheduler /workspace
cp -R /opt/codex-worker/bundled-skills/marketing-scheduler/. /root/.codex/skills/marketing-scheduler/

echo "Codex Worker API ready: $(codex --version)"
exec uvicorn --app-dir /opt/codex-worker server:app --host 0.0.0.0 --port 80
