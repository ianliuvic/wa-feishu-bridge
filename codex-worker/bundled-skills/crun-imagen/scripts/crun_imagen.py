#!/usr/bin/env python3
"""Create and monitor Crun image-generation tasks using only the standard library."""

import argparse
import json
import mimetypes
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

# DSH strips credential-shaped variables from the shell environment it gives
# child processes, so pull this skill's credentials from the harness-managed
# file when they are not already present. No-op on a Codex worker.
try:
    from skill_credentials import load as _load_skill_credentials
    _load_skill_credentials("CRUN_API_KEY")
except ImportError:
    pass

BASE_URL = "https://api.crun.ai/api/v1/client/job"
DEFAULT_MODEL = "openai/gpt-image-2"
GROK_MODEL = "grok-imagine-image-2.0"
NANO_MODEL = "google/nano-banana-2"
NANO_V2_MODEL = "google/nano-banana-2-v2"
NANO_MODELS = (NANO_MODEL, NANO_V2_MODEL)
MODELS = (DEFAULT_MODEL, GROK_MODEL, *NANO_MODELS)
GPT_ASPECT_RATIOS = ("1:1", "2:3", "3:2", "9:16", "16:9", "4:3", "3:4", "21:9", "auto")
GROK_ASPECT_RATIOS = ("1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9")
NANO_ASPECT_RATIOS = (
    "1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9",
    "1:4", "4:1", "1:8", "8:1", "21:9", "auto",
)
ASPECT_RATIOS = tuple(dict.fromkeys((*GPT_ASPECT_RATIOS, *GROK_ASPECT_RATIOS, *NANO_ASPECT_RATIOS)))
RESOLUTIONS = ("1K", "2K", "4K")
OUTPUT_FORMATS = ("png", "jpg")


def api_key():
    value = os.environ.get("CRUN_API_KEY")
    if not value:
        raise SystemExit("CRUN_API_KEY is not set")
    return value


def request_json(method, url, payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    request = Request(url, data=body, method=method, headers={
        "x-api-key": api_key(),
        "Content-Type": "application/json",
        # Cloudflare rejects urllib's default Python-urllib User-Agent (1010).
        "User-Agent": "crun-imagen-skill/1.0",
    })
    try:
        with urlopen(request, timeout=60) as response:
            result = json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise SystemExit(f"Crun HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise SystemExit(f"Crun request failed: {exc}") from exc
    if result.get("code") != 200:
        raise SystemExit(f"Crun API error: {json.dumps(result, ensure_ascii=False)}")
    return result


def task_info(task_id):
    return request_json("GET", f"{BASE_URL}/TaskInfo?{urlencode({'task_id': task_id})}")


def wait_for_task(task_id, interval, timeout):
    deadline = time.monotonic() + timeout
    while True:
        response = task_info(task_id)
        state = response.get("data", {}).get("status")
        if state in ("success", "failed"):
            return response
        if time.monotonic() >= deadline:
            raise SystemExit(f"Timed out waiting for task {task_id}; last status: {state}")
        time.sleep(interval)


def download_media(urls, output_dir):
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    saved = []
    for index, url in enumerate(urls, 1):
        try:
            with urlopen(url, timeout=120) as response:
                content_type = response.headers.get_content_type()
                suffix = Path(urlparse(url).path).suffix or mimetypes.guess_extension(content_type) or ".png"
                path = target / f"crun-image-{index}{suffix}"
                path.write_bytes(response.read())
        except (HTTPError, URLError, TimeoutError) as exc:
            raise SystemExit(f"Failed to download {url}: {exc}") from exc
        saved.append(str(path.resolve()))
    return saved


def emit(response, output_dir=None):
    data = response.get("data", {})
    if data.get("status") == "failed":
        raise SystemExit(f"Generation failed: {json.dumps(data.get('result'), ensure_ascii=False)}")
    urls = (data.get("result") or {}).get("media_urls") or []
    if output_dir and urls:
        response["saved_files"] = download_media(urls, output_dir)
    print(json.dumps(response, ensure_ascii=False, indent=2))


def build_create_payload(args, parser):
    """Validate model-specific options and build a CreateTask payload."""
    if not args.prompt:
        parser.error("--prompt must not be empty")

    if args.model == DEFAULT_MODEL:
        if len(args.prompt) > 5000:
            parser.error("--prompt must not exceed 5000 characters for openai/gpt-image-2")
        if args.n is not None:
            parser.error("--n is supported only by grok-imagine-image-2.0")
        if args.google_search:
            parser.error("--google-search is supported only by google/nano-banana-2")
        if args.output_format is not None:
            parser.error("--output-format is supported only by google/nano-banana-2")
        if len(args.image_url) > 16:
            parser.error("at most 16 --image-url values are supported")
        aspect_ratio = args.aspect_ratio or "auto"
        resolution = args.resolution or "1K"
        if aspect_ratio not in GPT_ASPECT_RATIOS:
            parser.error(f"unsupported aspect ratio for {DEFAULT_MODEL}: {aspect_ratio}")
        if resolution == "4K" and aspect_ratio == "1:1":
            parser.error("4K does not support a 1:1 aspect ratio")
        if aspect_ratio == "auto" and resolution != "1K":
            parser.error("auto aspect ratio supports only 1K")
        payload = {"model": args.model, "input": {
            "prompt": args.prompt, "aspect_ratio": aspect_ratio, "resolution": resolution
        }}
        if args.image_url:
            payload["input"]["img_urls"] = args.image_url
    elif args.model == GROK_MODEL:
        if len(args.prompt) > 20000:
            parser.error("--prompt must not exceed 20000 characters for grok-imagine-image-2.0")
        if args.image_url:
            parser.error("--image-url is not supported by grok-imagine-image-2.0")
        if args.resolution is not None:
            parser.error("--resolution is not supported by grok-imagine-image-2.0")
        if args.google_search:
            parser.error("--google-search is supported only by google/nano-banana-2")
        if args.output_format is not None:
            parser.error("--output-format is supported only by google/nano-banana-2")
        aspect_ratio = args.aspect_ratio or "1:1"
        if aspect_ratio not in GROK_ASPECT_RATIOS:
            parser.error(f"unsupported aspect ratio for {GROK_MODEL}: {aspect_ratio}")
        image_count = args.n if args.n is not None else 1
        if not 1 <= image_count <= 12:
            parser.error("--n must be between 1 and 12 for grok-imagine-image-2.0")
        payload = {"model": args.model, "input": {
            "prompt": args.prompt, "n": image_count, "aspect_ratio": aspect_ratio
        }}
    else:
        if len(args.prompt) > 20000:
            parser.error(f"--prompt must not exceed 20000 characters for {args.model}")
        if args.n is not None:
            parser.error("--n is supported only by grok-imagine-image-2.0")
        if len(args.image_url) > 14:
            parser.error("at most 14 --image-url values are supported by Nano Banana 2")
        aspect_ratio = args.aspect_ratio or "1:1"
        resolution = args.resolution or "1K"
        if aspect_ratio not in NANO_ASPECT_RATIOS:
            parser.error(f"unsupported aspect ratio for {args.model}: {aspect_ratio}")
        if args.model == NANO_V2_MODEL and args.google_search:
            parser.error("--google-search is not supported by google/nano-banana-2-v2")
        if args.model == NANO_V2_MODEL and args.output_format is not None:
            parser.error("--output-format is not supported by google/nano-banana-2-v2")
        payload = {"model": args.model, "input": {
            "prompt": args.prompt, "aspect_ratio": aspect_ratio, "resolution": resolution
        }}
        if args.image_url:
            payload["input"]["img_urls"] = args.image_url
        if args.model == NANO_MODEL:
            payload["input"]["google_search"] = args.google_search
            payload["input"]["output_format"] = args.output_format or "png"

    if args.callback_url:
        payload["callback_url"] = args.callback_url
    return payload


def main():
    parser = argparse.ArgumentParser(description="Crun image-generation client")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="create an image task")
    create.add_argument("--prompt", required=True)
    create.add_argument("--model", choices=MODELS, default=DEFAULT_MODEL)
    create.add_argument("--image-url", action="append", default=[])
    create.add_argument("--aspect-ratio", choices=ASPECT_RATIOS)
    create.add_argument("--resolution", choices=RESOLUTIONS)
    create.add_argument("--n", type=int)
    create.add_argument("--google-search", action="store_true")
    create.add_argument("--output-format", choices=OUTPUT_FORMATS)
    create.add_argument("--callback-url")
    create.add_argument("--wait", action="store_true")
    create.add_argument("--interval", type=int, default=15)
    create.add_argument("--timeout", type=int, default=1200)
    create.add_argument("--output-dir")
    status = commands.add_parser("status", help="query a task once")
    status.add_argument("task_id")
    wait = commands.add_parser("wait", help="wait for a task to finish")
    wait.add_argument("task_id")
    wait.add_argument("--interval", type=int, default=15)
    wait.add_argument("--timeout", type=int, default=1200)
    wait.add_argument("--output-dir")
    args = parser.parse_args()

    if args.command == "create":
        payload = build_create_payload(args, parser)
        response = request_json("POST", f"{BASE_URL}/CreateTask", payload)
        if args.wait:
            task_id = response.get("data", {}).get("task_id")
            if not task_id:
                raise SystemExit("Crun response did not include data.task_id")
            response = wait_for_task(task_id, args.interval, args.timeout)
        emit(response, args.output_dir)
    elif args.command == "status":
        emit(task_info(args.task_id))
    else:
        emit(wait_for_task(args.task_id, args.interval, args.timeout), args.output_dir)


if __name__ == "__main__":
    main()
