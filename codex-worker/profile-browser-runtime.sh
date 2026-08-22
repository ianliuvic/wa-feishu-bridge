#!/bin/sh

export PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright

(
    set -eu
    runtime_dir=/root/.codex/browser-mcp
    mkdir -p "$runtime_dir" "$PLAYWRIGHT_BROWSERS_PATH" /browser-data/profile /workspace/codex-artifacts

    if [ ! -x "$runtime_dir/node_modules/.bin/playwright-mcp" ]; then
        npm install --prefix "$runtime_dir" @playwright/mcp@0.0.79
    fi

    "$runtime_dir/node_modules/.bin/playwright" install-deps chromium
    "$runtime_dir/node_modules/.bin/playwright" install chromium
    ln -sf "$runtime_dir/node_modules/.bin/playwright-mcp" /usr/local/bin/playwright-mcp
    touch /tmp/playwright-browser-ready
) >/tmp/playwright-browser-setup.log 2>&1 || {
    echo "Playwright browser setup failed; see /tmp/playwright-browser-setup.log" >&2
}
