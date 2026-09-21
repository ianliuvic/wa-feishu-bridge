#!/bin/sh
set -eu

DSH_HOME="${DSH_HOME:-/root/.dsh}"
DSH_WORKSPACE="${DSH_WORKSPACE:-/workspace}"

mkdir -p "$DSH_HOME/skills" "$DSH_HOME/profiles" \
    "$DSH_WORKSPACE" "$DSH_WORKSPACE/dsh-artifacts" "$DSH_WORKSPACE/dsh-run-logs"

# Skills are mounted into the harness home rather than the image's package tree,
# so a later `docker cp`/volume refresh can add or replace one without a rebuild.
if [ -d /opt/dsh-worker/bundled-skills ]; then
    cp -R /opt/dsh-worker/bundled-skills/. "$DSH_HOME/skills/"
fi

# Home-level patch, applied over every profile. It points the filesystem skill
# provider at the Codex skill root, so DSH discovers the same skills codex-worker
# has instead of a second, drifting copy under $DSH_HOME/skills. Only written
# when absent, so an operator edit to this file survives a restart.
SKILL_ROOT="${DSH_SKILL_ROOT:-/root/.codex/skills}"
PATCH_FILE="$DSH_HOME/cordis.patch.yml"
if [ ! -f "$PATCH_FILE" ]; then
    cat > "$PATCH_FILE" <<EOF
# Written by the dsh-worker entrypoint on first start; edit freely and it will
# be preserved. The user skill root lives at \$DSH_HOME/skills.
- id: skill-filesystem
  config:
    customSkillDirs:
      - $SKILL_ROOT
EOF
fi

if [ ! -d "$SKILL_ROOT" ]; then
    echo "WARNING: skill root $SKILL_ROOT does not exist; dsh will see no Codex skills." >&2
fi

if [ -z "${DEEPSEEK_API_KEY:-}" ]; then
    echo "WARNING: DEEPSEEK_API_KEY is not set; dsh runs will fail to reach the model." >&2
fi

if [ -z "${DSH_WORKER_TOKEN:-}" ]; then
    echo "WARNING: DSH_WORKER_TOKEN is not set; /v1/runs will reject every caller." >&2
fi

# Prove the profile still composes in this container before accepting traffic.
dsh --profile "${DSH_PROFILE:-headless}" --dump-config > /dev/null

echo "DSH Worker API ready: dsh $(dsh --version)"
exec uvicorn --app-dir /opt/dsh-worker server:app --host 0.0.0.0 --port 80
