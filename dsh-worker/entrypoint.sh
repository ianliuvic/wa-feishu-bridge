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

# Skill runtime config (e.g. wearhongxiu-wp/config.env, ~/.zoho-api/.env) is
# rebuilt from the environment on every start, so no credential is baked into
# the image and a lost container filesystem cannot lose the configuration.
python3 /opt/dsh-worker/configure_skills.py

# google-ads reads its token and service-account path out of ~/.codex/config.toml,
# so the same bootstrap codex-worker uses is reused verbatim here. It is a no-op
# unless the GOOGLE_ADS_* variables are present.
python3 /opt/dsh-worker/configure_google_ads.py || \
    echo "WARNING: Google Ads MCP configuration failed" >&2

if [ -z "${DEEPSEEK_API_KEY:-}" ]; then
    echo "WARNING: DEEPSEEK_API_KEY is not set; dsh runs will fail to reach the model." >&2
fi

if [ -z "${DSH_WORKER_TOKEN:-}" ]; then
    echo "WARNING: DSH_WORKER_TOKEN is not set; /v1/runs will reject every caller." >&2
fi

# $DSH_HOME is a persistent volume, so a profile materialised by an earlier
# harness survives while the installed harness changes underneath it. When the
# two drift, every run dies at boot with
#   "plugin tree failed to load: <plugin> could not be resolved"
# even though the static --dump-config check passes, because plugins are loaded
# per run rather than when the config is dumped.
#
# The profile is auto-initialised from the installed package (the image build
# relies on exactly that), and operator customisation belongs in
# cordis.patch.yml, so re-materialise it here and keep the previous copy.
PROFILE="${DSH_PROFILE:-headless}"
PROFILE_DIR="$DSH_HOME/profiles/$PROFILE"
if [ -d "$PROFILE_DIR" ]; then
    STAMP="$(date +%Y%m%d%H%M%S)"
    mv "$PROFILE_DIR" "$PROFILE_DIR.stale-$STAMP"
    echo "dsh-worker: re-materialising profile '$PROFILE'; previous copy at $PROFILE_DIR.stale-$STAMP" >&2
    # Keep the volume from filling up with superseded profiles.
    ls -1dt "$PROFILE_DIR".stale-* 2>/dev/null | tail -n +3 | while read -r old; do
        rm -rf "$old"
    done
fi

# Prove the profile still composes in this container before accepting traffic.
dsh --profile "${DSH_PROFILE:-headless}" --dump-config > /dev/null
test -f "$DSH_HOME/profiles/${DSH_PROFILE:-headless}/package.json"

echo "DSH Worker API ready: dsh $(dsh --version)"
exec uvicorn --app-dir /opt/dsh-worker server:app --host 0.0.0.0 --port 80
