FROM node:22-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git jq python3 python3-pip ffmpeg \
    && rm -rf /var/lib/apt/lists/*
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN npm install -g @openai/codex@latest @playwright/mcp@0.0.79 \
    && codex --version \
    && playwright-mcp --version
RUN /usr/local/lib/node_modules/@playwright/mcp/node_modules/.bin/playwright install --with-deps chromium \
    && chmod -R a+rX /opt/ms-playwright
RUN pip3 install --break-system-packages --no-cache-dir fastapi==0.115.6 uvicorn==0.32.1

ENV CODEX_HOME=/root/.codex
ENV PYTHONUNBUFFERED=1
WORKDIR /workspace

COPY server.py /opt/codex-worker/server.py
COPY bundled-skills /opt/codex-worker/bundled-skills
COPY entrypoint.sh /opt/codex-worker/entrypoint.sh
RUN chmod +x /opt/codex-worker/entrypoint.sh

EXPOSE 80
CMD ["/opt/codex-worker/entrypoint.sh"]
