FROM node:22-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
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
    && dsh --version

RUN pip3 install --break-system-packages --no-cache-dir \
    fastapi==0.115.6 \
    uvicorn==0.32.1

ENV DSH_HOME=/root/.dsh
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
    && test -f /root/.dsh/profiles/headless/package.json

COPY server.py /opt/dsh-worker/server.py
COPY entrypoint.sh /opt/dsh-worker/entrypoint.sh
RUN chmod +x /opt/dsh-worker/entrypoint.sh

EXPOSE 80
CMD ["/opt/dsh-worker/entrypoint.sh"]
