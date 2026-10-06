#!/usr/bin/env python3
"""
Tests against the real clipboard and notification systems of the machine they run on.

They overwrite your clipboard and may show a desktop notification, so they only run when
TEMPMAIL_INTEGRATION=1 is set. CI runs them on Linux (X11 and Wayland), macOS, Windows and WSL.
"""
import os
import sys
import shutil
import subprocess
import unittest

import tempmail

ENABLED = os.environ.get("TEMPMAIL_INTEGRATION") == "1"
SKIP_REASON = "set TEMPMAIL_INTEGRATION=1 to use the real clipboard and notifications"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = "tempmail ✓ héllo 東京 482913"
ON_WINDOWS = sys.platform == "win32" or tempmail._is_wsl()


def _read_win32_clipboard() -> str:
    import ctypes
    from ctypes import wintypes

    user32, kernel32 = ctypes.WinDLL("user32"), ctypes.WinDLL("kernel32")
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    if not user32.OpenClipboard(None):
        raise OSError("could not open the clipboard")
    try:
        handle = user32.GetClipboardData(13)  # CF_UNICODETEXT
        ptr = kernel32.GlobalLock(handle)
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def read_clipboard(backend: str) -> str:
    """Read the clipboard back with the counterpart of the backend that wrote it."""
    if backend == "win32":
        return _read_win32_clipboard()
    if backend == "clip.exe":
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             "[Console]::OutputEncoding = [Text.Encoding]::UTF8; Get-Clipboard -Raw"],
            capture_output=True, check=True, timeout=60).stdout
        return out.decode("utf-8").rstrip("\r\n")
    readers = {
        "wl-copy": ["wl-paste", "--no-newline"],
        "xclip": ["xclip", "-selection", "clipboard", "-o"],
        "xsel": ["xsel", "--clipboard", "--output"],
        "pbcopy": ["pbpaste"],
    }
    env = dict(os.environ, LC_CTYPE="UTF-8")
    return subprocess.run(readers[backend], capture_output=True, check=True, timeout=10, env=env).stdout.decode("utf-8")


@unittest.skipUnless(ENABLED, SKIP_REASON)
class TestRealClipboard(unittest.TestCase):
    def test_roundtrip_preserves_unicode(self):
        ok, backend = tempmail.copy_to_clipboard(SAMPLE)
        self.assertTrue(ok, f"no working clipboard backend (got {backend!r})")
        self.assertEqual(read_clipboard(backend), SAMPLE, f"backend: {backend}")

    def test_copy_does_not_hold_stdout_open(self):
        # Clipboard tools that fork a background server must not inherit our stdout,
        # or command substitution like `EMAIL=$(tempmail -a)` never finishes
        proc = subprocess.Popen(
            [sys.executable, "-c", "import tempmail; print(tempmail.copy_to_clipboard('x'))"],
            cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            out, err = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            self.fail("stdout stayed open after copying; command substitution would hang")
        self.assertIn("(True,", out.decode(), err.decode())


@unittest.skipUnless(ENABLED, SKIP_REASON)
class TestRealNotifications(unittest.TestCase):
    TITLE = "tempmail ✓ integration test"
    MESSAGE = 'From: "CI" <ci@example.com> & $(not-a-command) <b>bold</b>'

    def windows_command(self):
        command = tempmail._windows_toast_command(self.TITLE, self.MESSAGE)
        if command is None:
            self.skipTest("powershell.exe not available")
        return command

    @unittest.skipUnless(ON_WINDOWS, "Windows or WSL only")
    def test_toast_text_reaches_powershell_unchanged(self):
        argv, env = self.windows_command()
        script = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
                  "Write-Output $env:TEMPMAIL_TITLE; Write-Output $env:TEMPMAIL_MESSAGE")
        result = subprocess.run([argv[0], "-NoProfile", "-NonInteractive", "-Command", script],
                                env=env, capture_output=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        self.assertEqual(result.stdout.decode("utf-8").splitlines(), [self.TITLE, self.MESSAGE])

    @unittest.skipUnless(ON_WINDOWS, "Windows or WSL only")
    def test_toast_script_runs(self):
        argv, env = self.windows_command()
        result = subprocess.run(argv, env=env, capture_output=True, timeout=120)
        stderr = result.stderr.decode("utf-8", "replace")
        self.assertEqual(result.returncode, 0, stderr)
        self.assertNotIn("Exception", stderr)

    @unittest.skipUnless(sys.platform == "darwin", "macOS only")
    def test_macos_notification(self):
        self.assertTrue(tempmail.send_notification(self.TITLE, self.MESSAGE))

    @unittest.skipUnless(sys.platform.startswith("linux") and not tempmail._is_wsl() and shutil.which("notify-send"),
                         "needs notify-send on Linux")
    def test_linux_notification(self):
        self.assertTrue(tempmail.send_notification(self.TITLE, self.MESSAGE))


if __name__ == "__main__":
    unittest.main()
