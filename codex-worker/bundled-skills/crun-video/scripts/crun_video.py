#!/usr/bin/env python3
"""Create, monitor, and download Crun video tasks using the standard library."""

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
DEFAULT_MODEL = "google/gemini-omni"
GROK_MODEL = "grok-imagine-video-1.5-preview"
MODELS = (DEFAULT_MODEL, GROK_MODEL)
GEMINI_DURATIONS = (4, 6, 8, 10)
GEMINI_ASPECT_RATIOS = ("16:9", "9:16")
GEMINI_RESOLUTIONS = ("720p", "1080p", "4k")
GROK_ASPECT_RATIOS = ("1:1", "2:3", "3:2", "16:9", "9:16", "auto")
GROK_RESOLUTIONS = ("480p", "720p", "1080p")
ASPECT_RATIOS = tuple(dict.fromkeys((*GEMINI_ASPECT_RATIOS, *GROK_ASPECT_RATIOS)))
RESOLUTIONS = tuple(dict.fromkeys((*GEMINI_RESOLUTIONS, *GROK_RESOLUTIONS)))


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
        "User-Agent": "crun-video-skill/1.0",
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


def validate_url(value, parser, option):
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        parser.error(f"{option} must be a public HTTP(S) URL")


def build_create_payload(args, parser):
    """Validate model-specific arguments and build a CreateTask payload."""
    if not args.prompt:
        parser.error("--prompt must not be empty")
    for value in args.image_url:
        validate_url(value, parser, "--image-url")
    if args.callback_url:
        validate_url(args.callback_url, parser, "--callback-url")

    if args.model == DEFAULT_MODEL:
        if len(args.prompt) > 20000:
            parser.error("--prompt must not exceed 20000 characters for google/gemini-omni")
        if len(args.image_url) > 7:
            parser.error("Gemini Omni supports at most 7 --image-url values")
        has_video = args.video_url is not None
        if has_video:
            validate_url(args.video_url, parser, "--video-url")
            if args.video_start is None or args.video_end is None:
                parser.error("--video-url requires --video-start and --video-end")
            if args.video_start < 0:
                parser.error("--video-start must be non-negative")
            if args.video_end <= args.video_start:
                parser.error("--video-end must be greater than --video-start")
            if len(args.image_url) > 5:
                parser.error("Gemini Omni supports at most 5 images with --video-url")
            if args.duration is not None:
                parser.error("omit --duration with --video-url; the model determines output duration")
        elif args.video_start is not None or args.video_end is not None:
            parser.error("--video-start and --video-end require --video-url")
        duration = args.duration if args.duration is not None else 4
        if not has_video and duration not in GEMINI_DURATIONS:
            parser.error("Gemini Omni --duration must be 4, 6, 8, or 10")
        aspect_ratio = args.aspect_ratio or "16:9"
        resolution = args.resolution or "720p"
        if aspect_ratio not in GEMINI_ASPECT_RATIOS:
            parser.error(f"unsupported Gemini Omni aspect ratio: {aspect_ratio}")
        if resolution not in GEMINI_RESOLUTIONS:
            parser.error(f"unsupported Gemini Omni resolution: {resolution}")
        inputs = {"prompt": args.prompt, "aspect_ratio": aspect_ratio, "resolution": resolution}
        if not has_video:
            inputs["duration"] = duration
        if args.image_url:
            inputs["img_urls"] = args.image_url
        if has_video:
            inputs["video_list"] = [{"url": args.video_url, "start": args.video_start, "ends": args.video_end}]
    else:
        if len(args.prompt) > 4096:
            parser.error("--prompt must not exceed 4096 characters for Grok Imagine Video")
        if len(args.image_url) > 7:
            parser.error("Grok Imagine Video supports at most 7 --image-url values")
        if args.video_url is not None or args.video_start is not None or args.video_end is not None:
            parser.error("source-video options are supported only by google/gemini-omni")
        duration = args.duration if args.duration is not None else 6
        if not 1 <= duration <= 15:
            parser.error("Grok Imagine Video --duration must be between 1 and 15")
        aspect_ratio = args.aspect_ratio or "auto"
        resolution = args.resolution or "480p"
        if aspect_ratio not in GROK_ASPECT_RATIOS:
            parser.error(f"unsupported Grok Imagine Video aspect ratio: {aspect_ratio}")
        if resolution not in GROK_RESOLUTIONS:
            parser.error(f"unsupported Grok Imagine Video resolution: {resolution}")
        inputs = {"prompt": args.prompt, "aspect_ratio": aspect_ratio, "resolution": resolution, "duration": duration}
        if args.image_url:
            inputs["img_urls"] = args.image_url

    payload = {"model": args.model, "input": inputs}
    if args.callback_url:
        payload["callback_url"] = args.callback_url
    return payload


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
            request = Request(url, headers={"User-Agent": "crun-video-skill/1.0"})
            with urlopen(request, timeout=300) as response:
                content_type = response.headers.get_content_type()
                suffix = Path(urlparse(url).path).suffix or mimetypes.guess_extension(content_type) or ".mp4"
                path = target / f"crun-video-{index}{suffix}"
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


def positive_int(value):
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def main():
    parser = argparse.ArgumentParser(description="Crun video-generation client")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="create a video task")
    create.add_argument("--prompt", required=True)
    create.add_argument("--model", choices=MODELS, default=DEFAULT_MODEL)
    create.add_argument("--image-url", action="append", default=[])
    create.add_argument("--video-url")
    create.add_argument("--video-start", type=int)
    create.add_argument("--video-end", type=int)
    create.add_argument("--duration", type=int)
    create.add_argument("--aspect-ratio", choices=ASPECT_RATIOS)
    create.add_argument("--resolution", choices=RESOLUTIONS)
    create.add_argument("--callback-url")
    create.add_argument("--wait", action="store_true")
    create.add_argument("--interval", type=positive_int, default=20)
    create.add_argument("--timeout", type=positive_int, default=1200)
    create.add_argument("--output-dir")
    status = commands.add_parser("status", help="query a task once")
    status.add_argument("task_id")
    wait = commands.add_parser("wait", help="wait for a task to finish")
    wait.add_argument("task_id")
    wait.add_argument("--interval", type=positive_int, default=20)
    wait.add_argument("--timeout", type=positive_int, default=1200)
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
