#!/usr/bin/env python3
"""
Seele Game Generation API client.

A thin, structured wrapper around the Seele OpenAPI endpoints that
handles HTTP, polling, error mapping, and local game-history persistence
so that LLM agents can drive game generation reliably across turns.

All successful command output is a single JSON object on stdout.
All progress and errors go to stderr.

Commands:
    create   Create a new game.
    status   Query a game's current generation status (one-shot).
    wait     Poll a game until it finishes, errors, or times out.
    continue Continue/modify an existing (finished) game.
    upload   Upload a local file and return its file_id.
    recent   List recently created games from local history.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_BASE_URL = "https://openapi.seeles.ai/v1/api"
ENV_BASE_URL = "SEELE_BASE_URL"
ENV_API_KEY_PRIMARY = "SEELE_API_KEY"
ENV_API_KEY_FALLBACK = "CODE4AGENT_API_KEY"

DEFAULT_POLL_INTERVAL_SECONDS = 15
DEFAULT_POLL_TIMEOUT_SECONDS = 30 * 60  # 30 minutes

HISTORY_FILE = Path.cwd() / ".seele_games.json"
HISTORY_MAX_ENTRIES = 20

MAX_UPLOAD_SIZE_BYTES = 25 * 1024 * 1024  # 25 MiB, per API schema

# Map of well-known error codes the upstream returns and what they mean.
# Kept as guidance for agents reading the JSON error output.
ERROR_GUIDANCE = {
    "UNAUTHORIZED": "The API key is missing or invalid. Check SEELE_API_KEY.",
    "SUBSCRIPTION_REQUIRED": "Seele01-pro requires an active subscription. Retry with model_type=Seele01-flash.",
    "GAME_ALREADY_PROCESSING": "The game is still generating. Wait for it to finish before calling continue.",
    "UPSTREAM_INVALID_RESPONSE": "The Seele backend returned an unexpected response. Retry later.",
}


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def emit(obj: Any) -> None:
    """Write a structured result to stdout as a single JSON line."""
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.stdout.write("\n")
    sys.stdout.flush()


def log(msg: str) -> None:
    """Write progress information to stderr (does not pollute stdout JSON)."""
    sys.stderr.write(f"[seele] {msg}\n")
    sys.stderr.flush()


def fail(code: str, message: str, exit_code: int = 1, **extra: Any) -> None:
    """Emit a structured error to stdout and exit."""
    payload = {"ok": False, "error": {"code": code, "message": message}}
    if code in ERROR_GUIDANCE:
        payload["error"]["guidance"] = ERROR_GUIDANCE[code]
    if extra:
        payload.update(extra)
    emit(payload)
    sys.exit(exit_code)


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------

class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, body: Any = None):
        self.status = status
        self.code = code
        self.message = message
        self.body = body
        super().__init__(f"{status} {code}: {message}")


def _api_key() -> str:
    key = os.environ.get(ENV_API_KEY_PRIMARY) or os.environ.get(ENV_API_KEY_FALLBACK)
    if not key:
        fail(
            "MISSING_API_KEY",
            f"Set {ENV_API_KEY_PRIMARY} (or {ENV_API_KEY_FALLBACK}) to your Seele API key. "
            "Get one at: https://www.seeles.ai/api "
            '(click the "Get API Key" button).',
        )
    return key


def _base_url() -> str:
    return os.environ.get(ENV_BASE_URL, DEFAULT_BASE_URL).rstrip("/")


def _request(
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    raw_body: bytes | None = None,
    extra_headers: dict | None = None,
    absolute_url: str | None = None,
    expect_json: bool = True,
) -> Any:
    """
    Perform an HTTP request and return parsed JSON (or raw bytes if expect_json=False).
    Raises ApiError on non-2xx responses from the Seele API.
    """
    url = absolute_url if absolute_url else f"{_base_url()}{path}"
    headers = {"Accept": "application/json", "User-Agent": "seele-client/1.0"}
    if absolute_url is None:
        # Only send our Bearer token to the Seele API, never to presigned S3 URLs.
        headers["Authorization"] = f"Bearer {_api_key()}"
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif raw_body is not None:
        data = raw_body
    else:
        data = None
    if extra_headers:
        headers.update(extra_headers)

    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read()
            if not expect_json:
                return body
            if not body:
                return None
            return json.loads(body.decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError:
            parsed = {"raw": raw.decode("utf-8", errors="replace")}
        err = parsed.get("error") or {}
        raise ApiError(
            status=e.code,
            code=err.get("code", f"HTTP_{e.code}"),
            message=err.get("message", e.reason or "HTTP error"),
            body=parsed,
        ) from e
    except urllib.error.URLError as e:
        raise ApiError(
            status=0,
            code="NETWORK_ERROR",
            message=f"Could not reach {url}: {e.reason}",
        ) from e


# ---------------------------------------------------------------------------
# Local history
# ---------------------------------------------------------------------------

def _load_history() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def _save_history(entries: list[dict]) -> None:
    try:
        HISTORY_FILE.write_text(
            json.dumps(entries[:HISTORY_MAX_ENTRIES], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        log(f"warning: could not persist history to {HISTORY_FILE}: {e}")


def _record_game(game_id: str, prompt: str, engine_type: str, model_type: str) -> None:
    entries = _load_history()
    # Deduplicate: if this game_id is already present, move it to the front
    # and update its fields rather than inserting a duplicate row.
    entries = [e for e in entries if e.get("game_id") != game_id]
    entries.insert(0, {
        "game_id": game_id,
        "prompt": prompt,
        "engine_type": engine_type,
        "model_type": model_type,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "last_status": "processing",
    })
    _save_history(entries)


def _update_game_status(game_id: str, status: str, extra: dict | None = None) -> None:
    entries = _load_history()
    for e in entries:
        if e.get("game_id") == game_id:
            e["last_status"] = status
            e["last_checked_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            if extra:
                e.update(extra)
            break
    _save_history(entries)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_create(args: argparse.Namespace) -> None:
    body: dict[str, Any] = {
        "prompt": args.prompt,
        "model_type": args.model,
        "engine_type": args.engine,
    }
    if args.file_ids:
        body["file_ids"] = args.file_ids

    try:
        resp = _request("POST", "/games", json_body=body)
    except ApiError as e:
        fail(e.code, e.message, exit_code=2)

    data = resp.get("data", {})
    game_id = data.get("game_id")
    if not game_id:
        fail("UNEXPECTED_RESPONSE", f"create returned no game_id: {resp}", exit_code=2)

    _record_game(game_id, args.prompt, args.engine, args.model)
    log(f"created game {game_id} (engine={args.engine}, model={args.model})")

    if args.wait:
        _wait_and_emit(
            game_id,
            interval=args.interval,
            timeout=args.timeout,
            initial_payload=data,
        )
    else:
        emit({"ok": True, "data": data})


def cmd_status(args: argparse.Namespace) -> None:
    try:
        resp = _request("GET", f"/games/{args.game_id}")
    except ApiError as e:
        fail(e.code, e.message, exit_code=2)

    data = resp.get("data", {})
    status = data.get("generation_status")
    _update_game_status(args.game_id, status or "unknown", extra={
        "game_title": data.get("game_title"),
    })
    emit({"ok": True, "data": data})


def cmd_wait(args: argparse.Namespace) -> None:
    _wait_and_emit(
        args.game_id,
        interval=args.interval,
        timeout=args.timeout,
        initial_payload=None,
    )


def _wait_and_emit(
    game_id: str,
    *,
    interval: int,
    timeout: int,
    initial_payload: dict | None,
) -> None:
    """
    Poll GET /games/{game_id} until finished, or until timeout.

    Timeout is NOT treated as failure: the caller can resume later by
    calling `status` or `wait` again with the same game_id.
    """
    deadline = time.time() + timeout
    last_step = None
    attempts = 0

    while True:
        attempts += 1
        try:
            resp = _request("GET", f"/games/{game_id}")
        except ApiError as e:
            fail(e.code, e.message, exit_code=2, game_id=game_id)

        data = resp.get("data", {})
        status = data.get("generation_status")

        if status == "finished":
            _update_game_status(game_id, "finished", extra={
                "game_title": data.get("game_title"),
                "preview_url": data.get("preview_url"),
            })
            log(f"game {game_id} finished after {attempts} poll(s)")
            result = {
                "ok": True,
                "status": "finished",
                "data": data,
            }
            if initial_payload:
                result["create_response"] = initial_payload
            emit(result)
            return

        step = data.get("current_step")
        if step and step != last_step:
            log(f"game {game_id} progress: {step}")
            last_step = step

        if time.time() >= deadline:
            _update_game_status(game_id, "processing", extra={
                "last_step": last_step,
            })
            log(f"game {game_id} still processing after {timeout}s; stopping polling")
            emit({
                "ok": True,
                "status": "timeout",
                "game_id": game_id,
                "last_known_step": last_step,
                "message": (
                    f"Game is still generating after {timeout} seconds. "
                    "This is not a failure — generation may simply take longer "
                    "(Unity games can take 15-25 minutes). "
                    f"Resume polling later with: seele_client.py wait {game_id}"
                ),
                "data": data,
            })
            return

        # Sleep but cap the wait to not overshoot the deadline.
        remaining = deadline - time.time()
        time.sleep(min(interval, max(remaining, 1)))


def cmd_continue(args: argparse.Namespace) -> None:
    body: dict[str, Any] = {
        "prompt": args.prompt,
        "model_type": args.model,
    }
    if args.file_ids:
        body["file_ids"] = args.file_ids

    try:
        resp = _request("POST", f"/games/{args.game_id}/continue", json_body=body)
    except ApiError as e:
        # 409 deserves a clearer hint for agents.
        if e.code == "GAME_ALREADY_PROCESSING":
            fail(
                e.code, e.message, exit_code=2,
                hint=(
                    "Call `seele_client.py wait "
                    f"{args.game_id}` first, then retry continue once finished."
                ),
            )
        fail(e.code, e.message, exit_code=2)

    data = resp.get("data", {})
    log(f"continue accepted for game {args.game_id}")

    # Record this iteration (same game_id, new prompt).
    entries = _load_history()
    for e in entries:
        if e.get("game_id") == args.game_id:
            iterations = e.setdefault("iterations", [])
            iterations.append({
                "prompt": args.prompt,
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            })
            e["last_status"] = "processing"
            break
    _save_history(entries)

    if args.wait:
        _wait_and_emit(
            args.game_id,
            interval=args.interval,
            timeout=args.timeout,
            initial_payload=data,
        )
    else:
        emit({"ok": True, "data": data})


def cmd_upload(args: argparse.Namespace) -> None:
    path = Path(args.path)
    if not path.exists() or not path.is_file():
        fail("FILE_NOT_FOUND", f"No file at {path}", exit_code=2)

    size = path.stat().st_size
    if size == 0:
        fail("INVALID_FILE", "File is empty.", exit_code=2)
    if size > MAX_UPLOAD_SIZE_BYTES:
        fail(
            "FILE_TOO_LARGE",
            f"File is {size} bytes; upload limit is {MAX_UPLOAD_SIZE_BYTES} bytes (25 MiB).",
            exit_code=2,
        )

    content_type = args.content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    # Step 1: ask the API for a presigned upload URL.
    try:
        resp = _request("POST", "/files", json_body={
            "filename": path.name,
            "content_type": content_type,
            "size": size,
        })
    except ApiError as e:
        fail(e.code, e.message, exit_code=2)

    data = resp.get("data", {})
    file_id = data.get("file_id")
    upload_url = data.get("upload_url")
    upload_headers = data.get("upload_headers") or {}
    if not file_id or not upload_url:
        fail("UNEXPECTED_RESPONSE", f"upload init returned unexpected payload: {resp}", exit_code=2)

    # Step 2: PUT the raw bytes to the presigned URL. No Bearer header here —
    # the presigned URL already carries its own authorization.
    log(f"uploading {size} bytes to presigned URL")
    raw = path.read_bytes()
    try:
        _request(
            "PUT",
            "",
            raw_body=raw,
            extra_headers=upload_headers,
            absolute_url=upload_url,
            expect_json=False,
        )
    except ApiError as e:
        fail("UPLOAD_FAILED", f"PUT to presigned URL failed: {e.message}", exit_code=2)

    log(f"uploaded file {path.name} as file_id={file_id}")
    emit({
        "ok": True,
        "data": {
            "file_id": file_id,
            "filename": path.name,
            "content_type": content_type,
            "size": size,
        },
    })


def cmd_recent(args: argparse.Namespace) -> None:
    entries = _load_history()[: args.limit]
    emit({"ok": True, "data": {"games": entries, "history_file": str(HISTORY_FILE)}})


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_file_ids(val: str | None) -> list[str]:
    if not val:
        return []
    return [x.strip() for x in val.split(",") if x.strip()]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="seele_client.py",
        description="Client for the Seele game-generation API.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    # create
    c = sub.add_parser("create", help="Create a new game.")
    c.add_argument("--prompt", required=True, help="Game description prompt.")
    c.add_argument("--model", default="Seele01-flash",
                   choices=["Seele01-flash", "Seele01-pro"],
                   help="Model type (default: Seele01-flash).")
    c.add_argument("--engine", default="threejs",
                   choices=["threejs", "unity"],
                   help="Engine type (default: threejs).")
    c.add_argument("--file-ids", type=_parse_file_ids, default=[],
                   help="Optional comma-separated reference file IDs from `upload`.")
    c.add_argument("--wait", action="store_true",
                   help="Block and poll until the game finishes.")
    c.add_argument("--interval", type=int, default=DEFAULT_POLL_INTERVAL_SECONDS,
                   help=f"Polling interval in seconds (default: {DEFAULT_POLL_INTERVAL_SECONDS}).")
    c.add_argument("--timeout", type=int, default=DEFAULT_POLL_TIMEOUT_SECONDS,
                   help=f"Polling timeout in seconds (default: {DEFAULT_POLL_TIMEOUT_SECONDS}).")
    c.set_defaults(func=cmd_create)

    # status
    s = sub.add_parser("status", help="One-shot status check for a game.")
    s.add_argument("game_id")
    s.set_defaults(func=cmd_status)

    # wait
    w = sub.add_parser("wait", help="Poll a game until it finishes or times out.")
    w.add_argument("game_id")
    w.add_argument("--interval", type=int, default=DEFAULT_POLL_INTERVAL_SECONDS)
    w.add_argument("--timeout", type=int, default=DEFAULT_POLL_TIMEOUT_SECONDS)
    w.set_defaults(func=cmd_wait)

    # continue
    co = sub.add_parser("continue", help="Continue/modify an existing finished game.")
    co.add_argument("game_id")
    co.add_argument("--prompt", required=True)
    co.add_argument("--model", default="Seele01-flash",
                    choices=["Seele01-flash", "Seele01-pro"])
    co.add_argument("--file-ids", type=_parse_file_ids, default=[])
    co.add_argument("--wait", action="store_true")
    co.add_argument("--interval", type=int, default=DEFAULT_POLL_INTERVAL_SECONDS)
    co.add_argument("--timeout", type=int, default=DEFAULT_POLL_TIMEOUT_SECONDS)
    co.set_defaults(func=cmd_continue)

    # upload
    u = sub.add_parser("upload", help="Upload a local file and return its file_id.")
    u.add_argument("path", help="Path to the local file to upload.")
    u.add_argument("--content-type", default=None,
                   help="Override MIME type (auto-detected if omitted).")
    u.set_defaults(func=cmd_upload)

    # recent
    r = sub.add_parser("recent", help="List recently created games from local history.")
    r.add_argument("--limit", type=int, default=10)
    r.set_defaults(func=cmd_recent)

    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
