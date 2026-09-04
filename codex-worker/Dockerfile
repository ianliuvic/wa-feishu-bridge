FROM node:22-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git jq python3 python3-pip python-is-python3 ffmpeg nginx \
    && rm -rf /var/lib/apt/lists/*
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN npm install -g @openai/codex@latest @playwright/mcp@0.0.79 \
    && codex --version \
    && playwright-mcp --version
RUN /usr/local/lib/node_modules/@playwright/mcp/node_modules/.bin/playwright install --with-deps chromium \
    && chmod -R a+rX /opt/ms-playwright
RUN pip3 install --break-system-packages --no-cache-dir \
    fastapi==0.115.6 \
    uvicorn==0.32.1 \
    google-ads-mcp==0.0.1 \
    pillow

# Build the remote-capable Canvas Agent. It runs beside the existing exec worker
# and therefore shares the same CODEX_HOME, credentials, skills, and workspace.
RUN git clone --depth 1 https://github.com/ianliuvic/infinite-canvas.git /opt/infinite-canvas \
    && cd /opt/infinite-canvas/canvas-agent \
    && npm install \
    && npm run build \
    && npm prune --omit=dev

ENV CODEX_HOME=/root/.codex
ENV PYTHONUNBUFFERED=1
WORKDIR /workspace

COPY server.py /opt/codex-worker/server.py
COPY bundled-skills /opt/codex-worker/bundled-skills
COPY configure_google_ads.py /opt/codex-worker/configure_google_ads.py
COPY entrypoint.sh /opt/codex-worker/entrypoint.sh
COPY canvas-entrypoint.sh /opt/codex-worker/canvas-entrypoint.sh
COPY nginx-canvas-agent.conf /etc/nginx/sites-enabled/default
RUN chmod +x /opt/codex-worker/entrypoint.sh
RUN chmod +x /opt/codex-worker/canvas-entrypoint.sh

EXPOSE 80
CMD ["/opt/codex-worker/canvas-entrypoint.sh"]
