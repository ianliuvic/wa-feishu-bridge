#!/usr/bin/env python3
"""Generate images through Replicate and save outputs to local files."""

from __future__ import annotations

import sys
from pathlib import Path as _PersistentPath
sys.path.insert(0, str(_PersistentPath.home() / '.codex' / 'python-packages'))

import argparse
import json
import mimetypes
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import urlopen


DEFAULT_MODEL = "black-forest-labs/flux-schnell"
DEFAULT_OUT = "output/imagegen/replicate-output.png"
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.json"


def die(message: str) -> None:
    raise SystemExit(f"error: {message}")


def parse_scalar(value: str) -> Any:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def parse_set(values: list[str]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for item in values:
        if "=" not in item:
            die(f"--set expects key=value, got {item!r}")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            die("--set key cannot be empty")
        parsed[key] = parse_scalar(value.strip())
    return parsed


def read_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        die(f"{CONFIG_PATH} is not valid JSON: {exc}")
    if not isinstance(payload, dict):
        die(f"{CONFIG_PATH} must contain a JSON object")
    return payload


def get_preset(config: dict[str, Any], name: str | None) -> tuple[str | None, dict[str, Any]]:
    if not name:
        name = config.get("default_preset")
    if not name:
        return None, {}

    presets = config.get("presets", {})
    if not isinstance(presets, dict):
        die(f"{CONFIG_PATH} field presets must be an object")
    if name not in presets:
        available = ", ".join(sorted(presets)) or "(none)"
        die(f"unknown preset {name!r}; available presets: {available}")
    preset = presets[name]
    if not isinstance(preset, dict):
        die(f"preset {name!r} must be an object")
    return name, preset


def build_input(args: argparse.Namespace, preset: dict[str, Any]) -> dict[str, Any]:
    if args.input_json:
        try:
            payload = json.loads(args.input_json)
        except json.JSONDecodeError as exc:
            die(f"--input-json is not valid JSON: {exc}")
        if not isinstance(payload, dict):
            die("--input-json must decode to an object")
    else:
        if not args.prompt:
            die("--prompt is required unless --input-json is provided")
        preset_input = preset.get("input", {})
        if preset_input is None:
            preset_input = {}
        if not isinstance(preset_input, dict):
            die("preset input must be an object")
        payload = dict(preset_input)
        payload["prompt"] = args.prompt
        if args.aspect_ratio:
            payload["aspect_ratio"] = args.aspect_ratio
        elif "aspect_ratio" not in payload:
            payload["aspect_ratio"] = "1:1"

    payload.update(parse_set(args.set or []))
    return payload


def read_token_from_config(config: dict[str, Any]) -> str | None:
    token = config.get("replicate_api_token")
    if token is None:
        return None
    if not isinstance(token, str) or not token.strip():
        die(f"{CONFIG_PATH} field replicate_api_token must be a non-empty string")
    return token.strip()


def configure_token(config: dict[str, Any]) -> None:
    if os.environ.get("REPLICATE_API_TOKEN"):
        return
    token = read_token_from_config(config)
    if token:
        os.environ["REPLICATE_API_TOKEN"] = token


def resolve_model(args: argparse.Namespace, preset: dict[str, Any]) -> str:
    if args.model:
        return args.model
    model = preset.get("model")
    if model:
        if not isinstance(model, str):
            die("preset model must be a string")
        return model
    return DEFAULT_MODEL


def print_presets(config: dict[str, Any]) -> None:
    presets = config.get("presets", {})
    if not isinstance(presets, dict):
        die(f"{CONFIG_PATH} field presets must be an object")
    rows = []
    for name, preset in sorted(presets.items()):
        if isinstance(preset, dict):
            rows.append({"name": name, "model": preset.get("model", "")})
    print(json.dumps(rows, indent=2))


def has_file_extension(path: Path) -> bool:
    return bool(path.suffix)


def with_index(path: Path, index: int) -> Path:
    if index == 0:
        return path
    suffix = path.suffix if path.suffix else ".png"
    stem = path.stem if path.suffix else path.name
    return path.with_name(f"{stem}-{index + 1}{suffix}")


def infer_suffix_from_url(url: str) -> str:
    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix
    if suffix:
        return suffix
    guessed = mimetypes.guess_extension("image/png")
    return guessed or ".png"


def normalize_out_path(out_path: Path, source_url: str | None = None) -> Path:
    if has_file_extension(out_path):
        return out_path
    suffix = infer_suffix_from_url(source_url) if source_url else ".png"
    return out_path.with_suffix(suffix)


def collect_outputs(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return [value]
    if hasattr(value, "read"):
        return [value]
    if isinstance(value, list) or isinstance(value, tuple):
        items: list[Any] = []
        for entry in value:
            items.extend(collect_outputs(entry))
        return items
    if isinstance(value, dict):
        for key in ("url", "image", "output"):
            if key in value:
                return collect_outputs(value[key])
    return [value]


def read_output(item: Any) -> tuple[bytes, str | None]:
    if hasattr(item, "read"):
        data = item.read()
        if isinstance(data, str):
            data = data.encode("utf-8")
        return data, None

    if isinstance(item, bytes):
        return item, None

    text = str(item)
    if text.startswith("http://") or text.startswith("https://"):
        with urlopen(text, timeout=180) as response:
            return response.read(), text

    path = Path(text)
    if path.exists():
        return path.read_bytes(), None

    die(f"unsupported Replicate output item: {text[:200]}")


def save_outputs(output: Any, out: str, force: bool) -> list[Path]:
    items = collect_outputs(output)
    if not items:
        die("Replicate returned no outputs")

    requested = Path(out)
    saved: list[Path] = []
    for index, item in enumerate(items):
        data, source_url = read_output(item)
        target = normalize_out_path(with_index(requested, index), source_url)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not force:
            die(f"{target} already exists; choose another --out path or pass --force")
        target.write_bytes(data)
        saved.append(target)
    return saved


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate images through Replicate.")
    parser.add_argument("--model", help="Replicate model slug. Overrides --preset and config default_preset.")
    parser.add_argument("--preset", help="Named preset from config.json.")
    parser.add_argument("--list-presets", action="store_true")
    parser.add_argument("--prompt")
    parser.add_argument("--aspect-ratio", help="Overrides preset input.aspect_ratio.")
    parser.add_argument("--input-json", help="Full Replicate input object as JSON.")
    parser.add_argument("--set", action="append", default=[], help="Input override as key=value; repeatable.")
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = read_config()
    if args.list_presets:
        print_presets(config)
        return

    _preset_name, preset = get_preset(config, args.preset)
    model = resolve_model(args, preset)
    payload = build_input(args, preset)

    if args.dry_run:
        print(json.dumps({"model": model, "input": payload, "out": args.out}, indent=2))
        return

    configure_token(config)
    if not os.environ.get("REPLICATE_API_TOKEN"):
        die(f"REPLICATE_API_TOKEN is not set and {CONFIG_PATH} has no replicate_api_token")

    try:
        import replicate
    except ModuleNotFoundError:
        die("Python package 'replicate' is not installed. Run: pip install replicate")

    output = replicate.run(model, input=payload)
    saved = save_outputs(output, args.out, args.force)
    for path in saved:
        print(path)


if __name__ == "__main__":
    main()