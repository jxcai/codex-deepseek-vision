#!/usr/bin/env python3
"""Send a local image or the system clipboard image to the configured Qwen or Gemini vision endpoint."""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = SKILL_DIR / "config.json"
MIME_FALLBACKS = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}
RETRY_STATUSES = {408, 429, 500, 502, 503, 504}
DEFAULT_USER_AGENT = "codex-deepseek-vision/1.0"


class BridgeError(RuntimeError):
    """An actionable bridge failure."""


def parse_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def load_config(path: Path) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BridgeError(f"Config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise BridgeError(f"Invalid JSON config at {path}: {exc}") from exc
    if not isinstance(config, dict):
        raise BridgeError("Config root must be a JSON object")
    provider = config.get("active_provider")
    providers = config.get("providers")
    if provider not in {"qwen", "gemini"} or not isinstance(providers, dict):
        raise BridgeError("active_provider must be qwen or gemini and providers must be an object")
    settings = providers.get(provider)
    if not isinstance(settings, dict):
        raise BridgeError(f"Missing configuration for provider: {provider}")
    for key in ("base_url", "model", "api_key_env"):
        if not isinstance(settings.get(key), str) or not settings[key].strip():
            raise BridgeError(f"Provider {provider} requires a non-empty {key}")
    return config


def image_data_url(image_path: Path, mime_type: str | None) -> str:
    if not image_path.is_file():
        raise BridgeError(f"Image file not found: {image_path}")
    guessed = mime_type or mimetypes.guess_type(image_path.name)[0] or MIME_FALLBACKS.get(image_path.suffix.lower())
    if not guessed or not guessed.startswith("image/"):
        raise BridgeError("Could not determine an image MIME type; pass --mime-type image/png (or similar)")
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{guessed};base64,{encoded}"


def clipboard_image() -> tuple[Path, str]:
    """Read the image currently on the system clipboard into a temp file.

    Returns ``(temp path, MIME type)``. The caller owns the temp file and
    should delete it once done. Raises :class:`BridgeError` when the platform
    is unsupported or the clipboard holds no image.
    """
    if sys.platform == "darwin":
        return _clipboard_image_macos()
    if sys.platform.startswith("linux"):
        return _clipboard_image_linux()
    if sys.platform == "win32":
        return _clipboard_image_windows()
    raise BridgeError(
        f"Clipboard image capture is not implemented on {sys.platform}; use --image with a file path"
    )


def _parse_macos_clipboard_info(info: str) -> tuple[str, str, str] | None:
    """Map ``osascript -e 'clipboard info'`` output to (format, MIME, suffix).

    macOS screenshots put both PNG and TIFF representations on the clipboard;
    prefer PNG (lossless, universally served). Returns ``None`` when no image
    representation is present.
    """
    lowered = info.lower()
    if "pngf" in lowered:
        return "PNGf", "image/png", ".png"
    if "8bps" in lowered:
        return "8BPS", "image/tiff", ".tif"
    return None


def _macos_clipboard_script(out_path: str, fmt: str) -> str:
    escaped = out_path.replace("\\", "\\\\").replace('"', '\\"')
    return (
        f'set outFile to POSIX file "{escaped}"\n'
        f'set outData to the clipboard as «class {fmt}»\n'
        f'set fh to open for access outFile with write permission\n'
        f'write outData to fh\n'
        f'close access fh'
    )


def _run_capture(command: list[str], what: str, timeout: int = 10) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise BridgeError(f"{what}: executable not found: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise BridgeError(f"{what}: timed out after {timeout}s") from exc


def _clipboard_image_macos() -> tuple[Path, str]:
    info = _run_capture(["osascript", "-e", "clipboard info"], "osascript").stdout
    parsed = _parse_macos_clipboard_info(info)
    if parsed is None:
        raise BridgeError("The clipboard does not contain an image; copy or screenshot one first")
    fmt, mime, suffix = parsed
    fd, name = tempfile.mkstemp(prefix="vision-bridge-", suffix=suffix)
    os.close(fd)
    try:
        result = _run_capture(["osascript", "-e", _macos_clipboard_script(name, fmt)], "osascript")
        if result.returncode != 0:
            raise BridgeError(f"osascript could not read the clipboard image: {result.stderr.strip()}")
    except BaseException:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise
    return Path(name), mime


def _clipboard_image_linux() -> tuple[Path, str]:
    for command in (
        ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
        ["xsel", "--clipboard", "--output"],
    ):
        try:
            result = subprocess.run(command, capture_output=True, timeout=10)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0 and result.stdout:
            fd, name = tempfile.mkstemp(prefix="vision-bridge-", suffix=".png")
            with os.fdopen(fd, "wb") as handle:
                handle.write(result.stdout)
            return Path(name), "image/png"
    raise BridgeError(
        "No image found on the clipboard (needs xclip or xsel); copy an image first or pass --image"
    )


def _clipboard_image_windows() -> tuple[Path, str]:
    fd, name = tempfile.mkstemp(prefix="vision-bridge-", suffix=".png")
    os.close(fd)
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "Add-Type -AssemblyName System.Drawing;"
        "$img = [System.Windows.Forms.Clipboard]::GetImage();"
        "if ($null -eq $img) { Write-Error 'no image on clipboard'; exit 1 };"
        f"$img.Save('{name}', [System.Drawing.Imaging.ImageFormat]::Png);"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise BridgeError("powershell not found; use --image with a file path") from None
    if result.returncode != 0:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise BridgeError(f"PowerShell could not read the clipboard image: {result.stderr.strip()}")
    return Path(name), "image/png"


def join_url(base_url: str, suffix: str) -> str:
    return base_url.rstrip("/") + "/" + suffix.lstrip("/")


def build_qwen_request(settings: dict[str, Any], prompt: str, data_url: str) -> tuple[str, dict[str, str], dict[str, Any]]:
    endpoint = settings.get("endpoint") or join_url(settings["base_url"], "chat/completions")
    thinking = settings.get("thinking") or {}
    body: dict[str, Any] = {
        "model": settings["model"],
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": data_url}},
            {"type": "text", "text": prompt},
        ]}],
    }
    # Qwen 3.8 enables thinking by default; send the boolean explicitly so
    # config.json can reliably turn it off as well as on.
    body["enable_thinking"] = bool(thinking.get("enabled", False))
    if thinking.get("enabled"):
        if thinking.get("thinking_budget", 0) > 0:
            body["thinking_budget"] = thinking["thinking_budget"]
    return endpoint, {"Authorization": f"Bearer {settings['_api_key']}", "Content-Type": "application/json"}, body


def build_gemini_request(settings: dict[str, Any], prompt: str, data_url: str) -> tuple[str, dict[str, str], dict[str, Any]]:
    style = settings.get("endpoint_style", "generate-content")
    encoded = data_url.split(",", 1)[1]
    mime_type = data_url.split(";", 1)[0].split(":", 1)[1]
    if style == "openai":
        endpoint = settings.get("endpoint") or join_url(settings["base_url"], "chat/completions")
        body: dict[str, Any] = {"model": settings["model"], "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]}]}
    elif style == "interactions":
        endpoint = settings.get("endpoint") or join_url(settings["base_url"], "interactions")
        body = {
            "model": settings["model"],
            "input": [
                {"type": "text", "text": prompt},
                {"type": "image", "data": encoded, "mime_type": mime_type},
            ],
        }
    else:
        model_path = urllib.parse.quote(settings["model"], safe="-_.~")
        endpoint = settings.get("endpoint") or join_url(settings["base_url"], f"models/{model_path}:generateContent")
        body = {"contents": [{"role": "user", "parts": [
            {"text": prompt},
            {"inline_data": {"mime_type": mime_type, "data": encoded}},
        ]}]}
    return endpoint, {"x-goog-api-key": settings["_api_key"], "Content-Type": "application/json"}, body


def response_diagnostics(exc: urllib.error.HTTPError, detail: str) -> str:
    """Keep the provider body and correlation headers useful for relay debugging."""
    diagnostics = [f"HTTP {exc.code}: {detail or '<empty response body>'}"]
    for header in ("x-request-id", "x-client-request-id", "cf-ray", "server", "content-type"):
        value = exc.headers.get(header)
        if value:
            diagnostics.append(f"{header}={value}")
    return "; ".join(diagnostics)


def request_json(endpoint: str, headers: dict[str, str], body: dict[str, Any], timeout: int, retries: int) -> dict[str, Any]:
    payload = json.dumps(body).encode("utf-8")
    last_error = ""
    for attempt in range(retries + 1):
        request_headers = dict(headers)
        # Some compatible gateways put Cloudflare in front of the API and reject
        # urllib's default Python-urllib User-Agent before API validation.
        request_headers.setdefault("User-Agent", DEFAULT_USER_AGENT)
        request = urllib.request.Request(endpoint, data=payload, headers=request_headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
                parsed = json.loads(raw)
                if not isinstance(parsed, dict):
                    raise BridgeError("Provider returned a non-object JSON response")
                return parsed
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            last_error = response_diagnostics(exc, detail)
            if exc.code not in RETRY_STATUSES or attempt >= retries:
                raise BridgeError(last_error) from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            if attempt >= retries:
                raise BridgeError(f"Request failed: {last_error}") from exc
        time.sleep(min(2 ** attempt, 8))
    raise BridgeError(f"Request failed: {last_error}")


def extract_text(provider: str, response: dict[str, Any]) -> str:
    if provider == "qwen":
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise BridgeError("Qwen response did not contain choices[0].message.content") from exc
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = "\n".join(part.get("text", "") for part in content if isinstance(part, dict))
        else:
            text = ""
    elif provider == "gemini-interactions":
        output_text = response.get("output_text")
        if isinstance(output_text, str):
            text = output_text
        else:
            outputs = response.get("outputs")
            text_parts: list[str] = []
            if isinstance(outputs, list):
                for output in outputs:
                    if isinstance(output, dict):
                        candidate_text = output.get("text")
                        if isinstance(candidate_text, str):
                            text_parts.append(candidate_text)
                        content = output.get("content")
                        if isinstance(content, list):
                            text_parts.extend(
                                item.get("text", "")
                                for item in content
                                if isinstance(item, dict) and isinstance(item.get("text"), str)
                            )
            text = "\n".join(text_parts)
    else:
        try:
            parts = response["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError) as exc:
            raise BridgeError("Gemini response did not contain candidates[0].content.parts") from exc
        text = "\n".join(part.get("text", "") for part in parts if isinstance(part, dict) and isinstance(part.get("text"), str))
    text = text.strip()
    if not text:
        raise BridgeError("Provider returned no text; refusing to pass an empty visual result to DeepSeek")
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect an image with the configured Qwen or Gemini vision model")
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--image", help="Path to a local image")
    source_group.add_argument("--clipboard", action="store_true", help="Read the image from the system clipboard")
    prompt_group = parser.add_mutually_exclusive_group()
    prompt_group.add_argument("--prompt", help="Instruction for the vision model")
    prompt_group.add_argument("--question", help="Question supplied with the image; sent verbatim as the vision prompt")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to config.json")
    parser.add_argument("--env-file", default=None, help="Path to .env; defaults to the nearest project .env")
    parser.add_argument("--mime-type", default=None, help="Override inferred image MIME type")
    args = parser.parse_args()
    try:
        config_path = Path(args.config).expanduser().resolve()
        config = load_config(config_path)
        provider = config["active_provider"]
        settings = dict(config["providers"][provider])
        env_path = Path(args.env_file).expanduser().resolve() if args.env_file else config_path.parents[3] / ".env"
        dotenv_values = parse_dotenv(env_path)
        key_name = settings["api_key_env"]
        api_key = os.environ.get(key_name) or dotenv_values.get(key_name)
        if not api_key:
            raise BridgeError(f"Missing API key {key_name}; add it to {env_path} or the environment")
        settings["_api_key"] = api_key
        request_settings = config.get("request") or {}
        prompt = (args.question if args.question is not None else args.prompt)
        if prompt is None:
            prompt = request_settings.get(
                "default_prompt",
                "Understand and analyze the image: identify its main contents, objects and relationships, scene or layout, visible text, data or chart meaning, and details relevant to follow-up questions. Ground the answer only in the image and state uncertainty when needed.",
            )
        prompt = prompt.strip()
        if not prompt:
            raise BridgeError("--prompt cannot be empty")
        clipboard_temp: Path | None = None
        try:
            if args.clipboard:
                image_path, clipboard_mime = clipboard_image()
                clipboard_temp = image_path
                mime = args.mime_type or clipboard_mime
            else:
                image_path = Path(args.image).expanduser().resolve()
                mime = args.mime_type
            data_url = image_data_url(image_path, mime)
        finally:
            if clipboard_temp is not None:
                try:
                    clipboard_temp.unlink()
                except OSError:
                    pass
        if provider == "qwen":
            endpoint, headers, body = build_qwen_request(settings, prompt, data_url)
        else:
            endpoint, headers, body = build_gemini_request(settings, prompt, data_url)
        if isinstance(request_settings.get("temperature"), (int, float)) and provider == "qwen":
            body.setdefault("temperature", request_settings["temperature"])
        response_provider = "gemini-interactions" if provider == "gemini" and settings.get("endpoint_style") == "interactions" else provider
        response = request_json(endpoint, headers, body, int(request_settings.get("timeout_seconds", 60)), int(request_settings.get("max_retries", 2)))
        print(json.dumps({"provider": provider, "model": settings["model"], "text": extract_text(response_provider, response)}, ensure_ascii=False, indent=2))
        return 0
    except BridgeError as exc:
        print(f"vision_bridge: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
