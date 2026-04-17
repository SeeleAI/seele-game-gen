"""
Unit tests for seele_client.py.

These tests mock urllib.request.urlopen so they can run in an offline sandbox
while still exercising the real request-building and response-handling code.
Run with: python -m unittest tests.test_seele_client
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import seele_client  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(body: dict | bytes, status: int = 200):
    """Return a context-manager-shaped mock imitating urlopen's return."""
    m = MagicMock()
    if isinstance(body, (dict, list)):
        m.read.return_value = json.dumps(body).encode("utf-8")
    else:
        m.read.return_value = body
    m.__enter__.return_value = m
    m.__exit__.return_value = False
    m.getcode.return_value = status
    return m


def _http_error(status: int, payload: dict):
    body = json.dumps(payload).encode("utf-8")
    err = urllib.error.HTTPError(
        url="http://example/", code=status, msg="err",
        hdrs=None, fp=io.BytesIO(body),
    )
    return err


def _run_cli(argv: list[str], stdin_env: dict | None = None) -> tuple[int, str, str]:
    """Run the CLI parser end-to-end with a captured stdout/stderr."""
    out = io.StringIO()
    err = io.StringIO()
    env = {**os.environ, **(stdin_env or {})}
    code = 0
    with patch.object(sys, "stdout", out), patch.object(sys, "stderr", err), \
         patch.dict(os.environ, env, clear=False):
        try:
            seele_client.main(argv)
        except SystemExit as e:
            code = int(e.code or 0)
    return code, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class RequestBuildingTests(unittest.TestCase):
    """Confirm the right URL, method, headers, and body go out."""

    def setUp(self):
        self.env = {"SEELE_API_KEY": "c4a_sk_test", "SEELE_BASE_URL": "https://example/v1/api"}

    def test_create_sends_correct_payload(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["method"] = req.get_method()
            captured["headers"] = dict(req.header_items())
            captured["body"] = req.data
            return _mock_response({
                "ok": True,
                "data": {
                    "game_id": "canvas_game_123",
                    "engine_type": "threejs",
                    "request_status": "created | accepted",
                    "meta": {
                        "estimated_time_minutes": 7,
                        "platform_url": "https://seeles.ai/game/generation/canvas_game_123",
                    },
                },
            })

        with tempfile.TemporaryDirectory() as td:
            with patch.object(seele_client, "HISTORY_FILE", Path(td) / ".seele_games.json"), \
                 patch("urllib.request.urlopen", side_effect=fake_urlopen):
                code, out, _ = _run_cli([
                    "create", "--prompt", "make a puzzle platformer with rewind",
                    "--engine", "threejs", "--model", "Seele01-flash",
                ], self.env)

        self.assertEqual(code, 0, out)
        self.assertEqual(captured["url"], "https://example/v1/api/games")
        self.assertEqual(captured["method"], "POST")
        # Headers are stored with lowercased names by urllib.
        hdrs = {k.lower(): v for k, v in captured["headers"].items()}
        self.assertEqual(hdrs.get("authorization"), "Bearer c4a_sk_test")
        self.assertEqual(hdrs.get("content-type"), "application/json")
        body = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual(body, {
            "prompt": "make a puzzle platformer with rewind",
            "model_type": "Seele01-flash",
            "engine_type": "threejs",
        })
        # file_ids must not be present when empty.
        self.assertNotIn("file_ids", body)

        result = json.loads(out.strip())
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["game_id"], "canvas_game_123")

    def test_create_includes_file_ids_when_given(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["body"] = req.data
            return _mock_response({"ok": True, "data": {"game_id": "g1", "meta": {"estimated_time_minutes": 5, "platform_url": "x"}}})

        with tempfile.TemporaryDirectory() as td:
            with patch.object(seele_client, "HISTORY_FILE", Path(td) / ".seele_games.json"), \
                 patch("urllib.request.urlopen", side_effect=fake_urlopen):
                _run_cli([
                    "create", "--prompt", "p", "--file-ids", "aaa,bbb,ccc",
                ], self.env)

        body = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual(body["file_ids"], ["aaa", "bbb", "ccc"])

    def test_continue_targets_correct_path(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["body"] = req.data
            return _mock_response({"ok": True, "data": {"game_id": "g1", "meta": {"estimated_time_minutes": 5, "platform_url": "x"}}})

        with tempfile.TemporaryDirectory() as td:
            with patch.object(seele_client, "HISTORY_FILE", Path(td) / ".seele_games.json"), \
                 patch("urllib.request.urlopen", side_effect=fake_urlopen):
                _run_cli([
                    "continue", "canvas_game_123",
                    "--prompt", "add bombs", "--model", "Seele01-flash",
                ], self.env)

        self.assertEqual(captured["url"], "https://example/v1/api/games/canvas_game_123/continue")
        body = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual(body, {"prompt": "add bombs", "model_type": "Seele01-flash"})

    def test_status_uses_get(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["method"] = req.get_method()
            return _mock_response({
                "ok": True,
                "data": {
                    "game_id": "g1",
                    "game_title": "Test Game",
                    "engine_type": "threejs",
                    "generation_status": "processing",
                    "current_step": "composing scene",
                },
            })

        with tempfile.TemporaryDirectory() as td:
            with patch.object(seele_client, "HISTORY_FILE", Path(td) / ".seele_games.json"), \
                 patch("urllib.request.urlopen", side_effect=fake_urlopen):
                _run_cli(["status", "g1"], self.env)

        self.assertEqual(captured["method"], "GET")
        self.assertEqual(captured["url"], "https://example/v1/api/games/g1")


class ErrorHandlingTests(unittest.TestCase):
    def setUp(self):
        self.env = {"SEELE_API_KEY": "c4a_sk_test", "SEELE_BASE_URL": "https://example/v1/api"}

    def test_missing_api_key_emits_structured_error(self):
        # Remove both env vars for this test.
        with patch.dict(os.environ, {}, clear=True):
            code, out, _ = _run_cli(["create", "--prompt", "p"], {})
        self.assertEqual(code, 1)
        result = json.loads(out.strip())
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "MISSING_API_KEY")

    def test_subscription_required_surfaces_guidance(self):
        def fake_urlopen(req, timeout=None):
            raise _http_error(403, {
                "ok": False,
                "error": {"code": "SUBSCRIPTION_REQUIRED", "message": "Seele01-pro requires an active subscription"},
            })

        with tempfile.TemporaryDirectory() as td:
            with patch.object(seele_client, "HISTORY_FILE", Path(td) / ".seele_games.json"), \
                 patch("urllib.request.urlopen", side_effect=fake_urlopen):
                code, out, _ = _run_cli([
                    "create", "--prompt", "p", "--model", "Seele01-pro",
                ], self.env)

        self.assertEqual(code, 2)
        result = json.loads(out.strip())
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "SUBSCRIPTION_REQUIRED")
        # Guidance should tell the agent what to do next.
        self.assertIn("guidance", result["error"])
        self.assertIn("Seele01-flash", result["error"]["guidance"])

    def test_continue_while_processing_includes_hint(self):
        def fake_urlopen(req, timeout=None):
            raise _http_error(409, {
                "ok": False,
                "error": {"code": "GAME_ALREADY_PROCESSING", "message": "game is still generating, continue is not allowed yet"},
            })

        with tempfile.TemporaryDirectory() as td:
            with patch.object(seele_client, "HISTORY_FILE", Path(td) / ".seele_games.json"), \
                 patch("urllib.request.urlopen", side_effect=fake_urlopen):
                code, out, _ = _run_cli([
                    "continue", "g1", "--prompt", "add bombs",
                ], self.env)

        self.assertEqual(code, 2)
        result = json.loads(out.strip())
        self.assertEqual(result["error"]["code"], "GAME_ALREADY_PROCESSING")
        # Hint points at the wait command.
        self.assertIn("hint", result)
        self.assertIn("wait g1", result["hint"])


class PollingTests(unittest.TestCase):
    def setUp(self):
        self.env = {"SEELE_API_KEY": "c4a_sk_test", "SEELE_BASE_URL": "https://example/v1/api"}

    def test_wait_returns_finished_when_status_flips(self):
        # First two polls return processing, third returns finished.
        responses = [
            {"ok": True, "data": {
                "game_id": "g1", "game_title": "X", "engine_type": "threejs",
                "generation_status": "processing", "current_step": "planning",
            }},
            {"ok": True, "data": {
                "game_id": "g1", "game_title": "X", "engine_type": "threejs",
                "generation_status": "processing", "current_step": "building",
            }},
            {"ok": True, "data": {
                "game_id": "g1", "game_title": "X", "engine_type": "threejs",
                "generation_status": "finished", "summary": "done",
                "preview_url": "https://seeles.ai/game/generation/g1",
            }},
        ]
        call_count = {"n": 0}

        def fake_urlopen(req, timeout=None):
            resp = responses[call_count["n"]]
            call_count["n"] += 1
            return _mock_response(resp)

        with tempfile.TemporaryDirectory() as td:
            with patch.object(seele_client, "HISTORY_FILE", Path(td) / ".seele_games.json"), \
                 patch("urllib.request.urlopen", side_effect=fake_urlopen), \
                 patch("time.sleep", lambda *_: None):  # don't actually wait
                code, out, _ = _run_cli([
                    "wait", "g1", "--interval", "1", "--timeout", "60",
                ], self.env)

        self.assertEqual(code, 0)
        result = json.loads(out.strip())
        self.assertEqual(result["status"], "finished")
        self.assertEqual(result["data"]["generation_status"], "finished")
        self.assertEqual(call_count["n"], 3)

    def test_wait_timeout_is_not_failure(self):
        # Always processing — simulate timing out.
        def fake_urlopen(req, timeout=None):
            return _mock_response({"ok": True, "data": {
                "game_id": "g1", "game_title": "X", "engine_type": "threejs",
                "generation_status": "processing", "current_step": "rendering",
            }})

        # Force the loop to exit quickly by controlling time.time's view of
        # the world: first call sets the deadline, second call is already past it.
        times = iter([1000.0, 1000.0, 2000.0, 2000.0, 2000.0])

        with tempfile.TemporaryDirectory() as td:
            with patch.object(seele_client, "HISTORY_FILE", Path(td) / ".seele_games.json"), \
                 patch("urllib.request.urlopen", side_effect=fake_urlopen), \
                 patch("time.time", lambda: next(times)), \
                 patch("time.sleep", lambda *_: None):
                code, out, _ = _run_cli([
                    "wait", "g1", "--interval", "1", "--timeout", "10",
                ], self.env)

        self.assertEqual(code, 0)  # not a failure
        result = json.loads(out.strip())
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "timeout")
        self.assertEqual(result["game_id"], "g1")
        self.assertIn("Resume polling later", result["message"])


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.env = {"SEELE_API_KEY": "c4a_sk_test", "SEELE_BASE_URL": "https://example/v1/api"}

    def test_create_writes_to_history_and_recent_lists_it(self):
        def fake_urlopen(req, timeout=None):
            return _mock_response({
                "ok": True, "data": {
                    "game_id": "g_history_1",
                    "meta": {"estimated_time_minutes": 5, "platform_url": "x"},
                },
            })

        with tempfile.TemporaryDirectory() as td:
            history = Path(td) / ".seele_games.json"
            with patch.object(seele_client, "HISTORY_FILE", history), \
                 patch("urllib.request.urlopen", side_effect=fake_urlopen):
                _run_cli(["create", "--prompt", "dodge asteroids"], self.env)
                code, out, _ = _run_cli(["recent"], self.env)

            self.assertEqual(code, 0)
            result = json.loads(out.strip())
            games = result["data"]["games"]
            self.assertEqual(len(games), 1)
            self.assertEqual(games[0]["game_id"], "g_history_1")
            self.assertEqual(games[0]["prompt"], "dodge asteroids")
            # Sanity: the file on disk matches.
            self.assertTrue(history.exists())

    def test_recent_returns_empty_when_no_history(self):
        with tempfile.TemporaryDirectory() as td:
            history = Path(td) / ".seele_games.json"
            with patch.object(seele_client, "HISTORY_FILE", history):
                code, out, _ = _run_cli(["recent"], self.env)

        self.assertEqual(code, 0)
        result = json.loads(out.strip())
        self.assertEqual(result["data"]["games"], [])


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.env = {"SEELE_API_KEY": "c4a_sk_test", "SEELE_BASE_URL": "https://example/v1/api"}

    def test_upload_two_phase_flow(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append({
                "url": req.full_url,
                "method": req.get_method(),
                "headers": {k.lower(): v for k, v in req.header_items()},
                "body": req.data,
            })
            if "/files" in req.full_url and "upload" not in req.full_url.lower():
                # First call: POST /files
                return _mock_response({
                    "ok": True,
                    "data": {
                        "file_id": "f" + "0" * 31,
                        "upload_url": "https://s3.example.com/presigned-url",
                        "upload_url_expires_at": "2026-12-31T23:59:59Z",
                        "upload_headers": {"Content-Type": "image/png"},
                        "filename": "ref.png",
                        "content_type": "image/png",
                        "size": 5,
                    },
                })
            else:
                # Second call: PUT to presigned URL
                return _mock_response(b"")

        with tempfile.TemporaryDirectory() as td:
            img = Path(td) / "ref.png"
            img.write_bytes(b"hello")  # 5 bytes
            history = Path(td) / ".seele_games.json"

            with patch.object(seele_client, "HISTORY_FILE", history), \
                 patch("urllib.request.urlopen", side_effect=fake_urlopen):
                code, out, _ = _run_cli(["upload", str(img)], self.env)

        self.assertEqual(code, 0, out)
        result = json.loads(out.strip())
        self.assertTrue(result["ok"])
        self.assertTrue(result["data"]["file_id"].startswith("f"))

        # Two calls: init then PUT.
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["method"], "POST")
        self.assertTrue(calls[0]["url"].endswith("/files"))
        init_body = json.loads(calls[0]["body"].decode("utf-8"))
        self.assertEqual(init_body, {"filename": "ref.png", "content_type": "image/png", "size": 5})
        # Init call carries Bearer.
        self.assertEqual(calls[0]["headers"]["authorization"], "Bearer c4a_sk_test")

        # PUT call goes to the presigned URL and MUST NOT carry our Bearer.
        self.assertEqual(calls[1]["method"], "PUT")
        self.assertEqual(calls[1]["url"], "https://s3.example.com/presigned-url")
        self.assertNotIn("authorization", calls[1]["headers"])
        # PUT call sends the raw file bytes.
        self.assertEqual(calls[1]["body"], b"hello")
        # PUT call carries the upload_headers.
        self.assertEqual(calls[1]["headers"].get("content-type"), "image/png")

    def test_upload_rejects_oversize_file(self):
        with tempfile.TemporaryDirectory() as td:
            big = Path(td) / "big.bin"
            # Create a sparse file that reports 26 MiB.
            with open(big, "wb") as f:
                f.seek(26 * 1024 * 1024)
                f.write(b"\0")

            code, out, _ = _run_cli(["upload", str(big)], self.env)

        self.assertEqual(code, 2)
        result = json.loads(out.strip())
        self.assertEqual(result["error"]["code"], "FILE_TOO_LARGE")

    def test_upload_rejects_missing_file(self):
        code, out, _ = _run_cli(["upload", "/nonexistent/path/xyz.png"], self.env)
        self.assertEqual(code, 2)
        result = json.loads(out.strip())
        self.assertEqual(result["error"]["code"], "FILE_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
