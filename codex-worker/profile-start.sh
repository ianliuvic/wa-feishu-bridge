# Start the worker API once when the image's login-shell CMD begins. The marker
# prevents later login shells inside the same container from starting a second server.
if [ ! -f /tmp/codex-worker-api-started ]; then
    touch /tmp/codex-worker-api-started
    pip3 install --break-system-packages --no-cache-dir fastapi==0.115.6 uvicorn==0.32.1 \
        >/tmp/codex-worker-pip.log 2>&1
    uvicorn --app-dir /opt/codex-worker server:app --host 0.0.0.0 --port 80 \
        >/proc/1/fd/1 2>/proc/1/fd/2 &
fi
