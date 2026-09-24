FROM node:22-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        ffmpeg \
        git \
        jq \
        python3 \
        python3-pip \
        python-is-python3 \
    && rm -rf /var/lib/apt/lists/*

# Pin the harness: the worker's HTTP contract is written against dsh 0.1.x, and
# an unpinned global install would silently change behaviour on every rebuild.
ARG DSH_VERSION=0.1.5-rc.2
RUN npm install -g "@deepseek-ai/dsh@${DSH_VERSION}" \
    && npm install -g "@deepseek-ai/dsh-sandbox-local@${DSH_VERSION}" \
    && dsh --version

# The profile resolves its plugin bundles through DSH_MODULE_FALLBACK and, for
# anything not installed alongside the profile, through the global tree. Node
# does not search the global root by default, so a plugin that only exists there
# failed to resolve at run time ("plugin tree failed to load") even though
# --dump-config succeeded. Installing sandbox-local explicitly and exposing the
# global root makes the build fail loudly if it is ever unavailable again.
ENV NODE_PATH=/usr/local/lib/node_modules

# The skill scripts import these directly. google-ads-mcp pulls in the
# google-ads / google-auth / protobuf stack that google-ads and the wearhongxiu
# SEO scripts need; pillow and PyYAML are used by the image and news workflows.
# Mirrors what codex-worker installs, so a skill behaves the same on both.
RUN pip3 install --break-system-packages --no-cache-dir \
    fastapi==0.115.6 \
    uvicorn==0.32.1 \
    google-ads-mcp==0.0.1 \
    pillow \
    PyYAML \
    openai \
    requests

# Install the Codex-side skills at the *same* absolute path codex-worker uses.
# Several skill bodies (hongxiu-weekly-product-email, marketing-scheduler,
# reddit-ops, shopify-analytics) hardcode /root/.codex/skills/<name>/scripts/...,
# so keeping the path identical is what lets DSH run them unmodified. The
# entrypoint then points the harness's filesystem skill provider at this root.
#
# These come from the build context, NOT a `git clone` of a moving ref: cloning
# inside a cached RUN layer silently ships stale skill code whenever only the
# repo moves and the Dockerfile does not. That is exactly how a credential fix
# failed to reach a running container once. The context is the repository root,
# so base_directory is "/" and dockerfile_location is /dsh-worker/Dockerfile.
COPY codex-worker/bundled-skills/ /root/.codex/skills/
COPY codex-worker/configure_google_ads.py /opt/dsh-worker/configure_google_ads.py

# The harness home is baked into the image rather than living on a persistent
# volume. A DSH profile resolves its bundles through an install that belongs to
# the harness build, so a volume-mounted home outlives the image and drifts away
# from it: every run then dies at boot with
#   "plugin tree failed to load: <plugin> could not be resolved"
# while the static --dump-config check still passes, because plugins load per run
# and not when the config is dumped. That is exactly what happened here after a
# rebuild. A one-shot worker needs no state across runs (DSH cannot resume a
# session), so the home belongs in the image. Any storage still mounted at the
# old path is ignored.
ENV DSH_HOME=/opt/dsh-home
ENV DSH_PROFILE=headless
# danger-full-access also pins the approval policy to "never" (see the dsh-base
# approval row), which is what lets an unattended run use shell and file tools.
ENV DSH_PERMISSION_MODE=danger-full-access
ENV DSH_TELEMETRY_DISABLED=1
ENV PYTHONUNBUFFERED=1
WORKDIR /workspace

# Materialize the shipped headless profile at build time. The profile is
# auto-initialized from the package's own template and resolves its plugins
# through the global install, so a cold container needs no bootstrap step and
# the image is proven to compose before it ever ships.
RUN dsh --profile headless --dump-config > /dev/null \
    && dsh --profile headless --help > /dev/null \
    && test -f "${DSH_HOME}/profiles/headless/package.json"

RUN mkdir -p /opt/dsh-worker
COPY dsh-worker/server.py /opt/dsh-worker/server.py
COPY dsh-worker/configure_skills.py /opt/dsh-worker/configure_skills.py
COPY dsh-worker/entrypoint.sh /opt/dsh-worker/entrypoint.sh
RUN chmod +x /opt/dsh-worker/entrypoint.sh \
    && ls -1 /root/.codex/skills

EXPOSE 80
CMD ["/opt/dsh-worker/entrypoint.sh"]
