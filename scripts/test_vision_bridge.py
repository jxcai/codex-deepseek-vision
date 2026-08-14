#!/usr/bin/env python3
"""Focused regression tests for the Gemini relay request and diagnostics."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import patch

import vision_bridge


class GeminiRequestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = {
            "base_url": "https://relay.example/v1beta",
            "model": "gemini-3.6-flash",
            "endpoint_style": "generate-content",
            "_api_key": "relay-key",
        }

    def test_generate_content_uses_user_role_and_inline_image(self) -> None:
        endpoint, headers, body = vision_bridge.build_gemini_request(
            self.settings,
            "请说明图片里的当前设置。",
            "data:image/png;base64,ZmFrZQ==",
        )

        self.assertEqual(
            endpoint,
            "https://relay.example/v1beta/models/gemini-3.6-flash:generateContent",
        )
        self.assertEqual(headers["x-goog-api-key"], "relay-key")
        self.assertEqual(body["contents"][0]["role"], "user")
        self.assertEqual(body["contents"][0]["parts"][0], {"text": "请说明图片里的当前设置。"})
        self.assertEqual(
            body["contents"][0]["parts"][1],
            {"inline_data": {"mime_type": "image/png", "data": "ZmFrZQ=="}},
        )

    @patch("vision_bridge.urllib.request.urlopen")
    def test_request_adds_identifiable_user_agent(self, urlopen) -> None:
        response = type("Response", (), {
            "__enter__": lambda self: self,
            "__exit__": lambda self, *args: None,
            "read": lambda self: b'{"ok": true}',
        })()
        urlopen.return_value = response

        result = vision_bridge.request_json(
            "https://relay.example/test", {"Content-Type": "application/json"}, {"ok": True}, 1, 0
        )

        self.assertEqual(result, {"ok": True})
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header("User-agent"), vision_bridge.DEFAULT_USER_AGENT)

    @patch("vision_bridge.urllib.request.urlopen")
    def test_http_error_keeps_body_and_correlation_headers(self, urlopen) -> None:
        headers = Message()
        headers["x-request-id"] = "request-123"
        headers["cf-ray"] = "ray-456"
        error = urllib.error.HTTPError(
            "https://relay.example/test", 400, "Bad Request", headers, None
        )
        error.read = lambda: b'{"error":{"message":"Please use a valid role: user, model."}}'
        urlopen.side_effect = error

        with self.assertRaises(vision_bridge.BridgeError) as raised:
            vision_bridge.request_json(
                "https://relay.example/test", {}, {"contents": []}, 1, 0
            )

        message = str(raised.exception)
        self.assertIn("HTTP 400", message)
        self.assertIn("Please use a valid role", message)
        self.assertIn("x-request-id=request-123", message)
        self.assertIn("cf-ray=ray-456", message)


class ClipboardTests(unittest.TestCase):
    def test_parse_macos_clipboard_info_prefers_png(self) -> None:
        self.assertEqual(
            vision_bridge._parse_macos_clipboard_info("«class PNGf», «class 8BPS»"),
            ("PNGf", "image/png", ".png"),
        )

    def test_parse_macos_clipboard_info_tiff_only(self) -> None:
        self.assertEqual(
            vision_bridge._parse_macos_clipboard_info("«class 8BPS»"),
            ("8BPS", "image/tiff", ".tif"),
        )

    def test_parse_macos_clipboard_info_no_image(self) -> None:
        self.assertIsNone(vision_bridge._parse_macos_clipboard_info("«class utxt», «class utf8»"))

    def test_macos_clipboard_script_escapes_path(self) -> None:
        script = vision_bridge._macos_clipboard_script('/tmp/a"b\\c.png', "PNGf")
        self.assertIn('POSIX file "/tmp/a\\"b\\\\c.png"', script)
        self.assertIn("«class PNGf»", script)
        self.assertIn("open for access", script)

    @patch("vision_bridge._run_capture")
    def test_clipboard_image_macos_raises_without_image(self, run_capture) -> None:
        run_capture.return_value = subprocess.CompletedProcess(["osascript"], 0, "«class utxt»\n", "")
        with self.assertRaises(vision_bridge.BridgeError) as raised:
            vision_bridge._clipboard_image_macos()
        self.assertIn("does not contain an image", str(raised.exception))

    @patch("vision_bridge._run_capture")
    def test_clipboard_image_macos_writes_temp_png(self, run_capture) -> None:
        run_capture.side_effect = [
            subprocess.CompletedProcess(["osascript"], 0, "«class PNGf», «class 8BPS»\n", ""),
            subprocess.CompletedProcess(["osascript"], 0, "", ""),
        ]
        image_path, mime = vision_bridge._clipboard_image_macos()
        try:
            self.assertEqual(mime, "image/png")
            self.assertTrue(image_path.name.endswith(".png"))
            self.assertTrue(image_path.is_file())
            self.assertEqual(image_path.parent, Path(tempfile.gettempdir()))
        finally:
            image_path.unlink(missing_ok=True)

    @patch("vision_bridge.subprocess.run")
    def test_clipboard_image_linux_via_xclip(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(["xclip"], 0, b"\x89PNG\r\n\x1a\nfake")
        image_path, mime = vision_bridge._clipboard_image_linux()
        try:
            self.assertEqual(mime, "image/png")
            self.assertTrue(image_path.is_file())
            self.assertEqual(image_path.read_bytes(), b"\x89PNG\r\n\x1a\nfake")
        finally:
            image_path.unlink(missing_ok=True)

    @patch("vision_bridge.subprocess.run")
    def test_clipboard_image_linux_raises_when_empty(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(["xclip"], 1, b"", b"nothing")
        with self.assertRaises(vision_bridge.BridgeError) as raised:
            vision_bridge._clipboard_image_linux()
        self.assertIn("No image found", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
