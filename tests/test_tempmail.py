#!/usr/bin/env python3
import unittest
import sys
import os
import io
import re
import json
import stat
import time
import base64
import tempfile
import contextlib
import subprocess
import urllib.error
from unittest import mock

import tempmail
from tempmail import (Extractor, copy_to_clipboard, send_notification, EmailMessage,
                      GuerrillaMailProvider, MailTmProvider)

class TestTempMailExtractor(unittest.TestCase):
    def test_standard_otp(self):
        text = "Your verification code is: 489201. Do not share it."
        otps = Extractor.extract_otp(text)
        self.assertTrue(len(otps) > 0)
        self.assertEqual(otps[0]["code"], "489201")
        self.assertEqual(otps[0]["confidence"], "high")

    def test_hyphenated_otp(self):
        text = "Enter code: 938-412 to continue."
        otps = Extractor.extract_otp(text)
        self.assertTrue(len(otps) > 0)
        self.assertEqual(otps[0]["code"], "938412")

    def test_spaced_otp(self):
        text = "Your login OTP is: 123 456"
        otps = Extractor.extract_otp(text)
        self.assertTrue(len(otps) > 0)
        self.assertEqual(otps[0]["code"], "123456")

    def test_pin_code(self):
        text = "Your temporary security PIN is: 8492"
        otps = Extractor.extract_otp(text)
        self.assertTrue(len(otps) > 0)
        self.assertEqual(otps[0]["code"], "8492")

    def test_contextual_otp(self):
        text = "719302 is your Discord verification code."
        otps = Extractor.extract_otp(text)
        self.assertTrue(len(otps) > 0)
        self.assertEqual(otps[0]["code"], "719302")

    def test_alphanumeric_otp(self):
        text = "Your confirmation code is: ABC-789"
        otps = Extractor.extract_otp(text)
        self.assertTrue(len(otps) > 0)
        self.assertEqual(otps[0]["raw"], "ABC-789")

    def test_standalone_number_with_keyword(self):
        text = "Please verify your account.\n\n582910\n\nExpires in 15 minutes."
        otps = Extractor.extract_otp(text)
        self.assertTrue(len(otps) > 0)
        self.assertEqual(otps[0]["code"], "582910")

    def test_year_false_positive_suppression(self):
        text = "Copyright 2026 Acme Corp. All rights reserved."
        otps = Extractor.extract_otp(text)
        self.assertEqual(len(otps), 0)

    def test_non_otp_codes_ignored(self):
        for text in ["Ship to zip code 90210 by Friday.",
                     "Your order barcode 48213977 is attached.",
                     "Use promo code: SAVE20 at checkout.",
                     "Reference token 20261006 for your ticket."]:
            with self.subTest(text=text):
                self.assertEqual(Extractor.extract_otp(text), [])

    def test_otp_after_unrelated_code_phrase(self):
        otps = Extractor.extract_otp("No promo code needed. Your verification code: 482913")
        self.assertEqual([o["code"] for o in otps], ["482913"])

    def test_confirmation_link_anchor(self):
        html = '''
        <p>Thanks for registering!</p>
        <a href="https://example.com/api/v1/auth/confirm?token=ab89c02&amp;uid=42">Confirm Email Address</a>
        <br>
        <a href="https://example.com/unsubscribe?user=42">Unsubscribe</a>
        '''
        links = Extractor.extract_links("Please confirm your email", html)
        self.assertTrue(len(links) > 0)
        self.assertIn("api/v1/auth/confirm", links[0]["url"])
        self.assertEqual(links[0]["anchor"], "Confirm Email Address")
        self.assertTrue(links[0]["score"] > 50)

    def test_magic_link_plaintext(self):
        text = "Click the following link to authenticate: https://service.io/auth/magic?token=9f82d1c7a4"
        links = Extractor.extract_links(text)
        self.assertTrue(len(links) > 0)
        self.assertEqual(links[0]["url"], "https://service.io/auth/magic?token=9f82d1c7a4")

    def test_link_ignores_social_and_tracking(self):
        html = '''
        <a href="https://twitter.com/example">Twitter</a>
        <a href="https://facebook.com/example">Facebook</a>
        <a href="https://example.com/privacy-policy">Privacy Policy</a>
        '''
        links = Extractor.extract_links("Follow us on social media", html)
        self.assertEqual(len(links), 0)

    def test_link_containing_own_address_kept(self):
        html = '<a href="https://site.io/verify?email=abc%40sharklasers.com&amp;token=x1">Verify email</a>'
        links = Extractor.extract_links("", html)
        self.assertEqual(len(links), 1)

    def test_domains_containing_ignored_substrings_kept(self):
        for url in ["https://www.dropbox.com/verify_email?t=abc",
                    "https://account.microsoft.com/confirm?code=1",
                    "https://www.reddit.com/verification/abc",
                    "https://www.netflix.com/verifyemail?t=1"]:
            with self.subTest(url=url):
                self.assertGreater(Extractor.score_url(url, "Confirm your email"), 0)

    def test_ignored_domain_subdomains_rejected(self):
        self.assertEqual(Extractor.score_url("https://mobile.twitter.com/verify?token=1"), -100)

    def test_link_with_control_chars_rejected(self):
        html = '<a href="https://site.io/verify?token=1\x1b[2J">Verify email</a>'
        self.assertEqual(Extractor.extract_links("", html), [])

    def test_email_message_full_analysis(self):
        msg = EmailMessage(
            msg_id="100",
            sender="support@supabase.io",
            subject="Confirm your Supabase account",
            date="2026-10-01 04:00",
            body_text="Your signup code is 992811. Or click below to confirm.",
            body_html='<p>Code: <strong>992811</strong></p><a href="https://supabase.io/auth/v1/verify?token=xyz987">Confirm Your Email</a>'
        )
        msg.analyze()
        self.assertEqual(len(msg.otps), 1)
        self.assertEqual(msg.otps[0]["code"], "992811")
        self.assertEqual(len(msg.links), 1)
        self.assertIn("token=xyz987", msg.links[0]["url"])

    def test_email_header_fields_sanitized(self):
        msg = EmailMessage("1", "evil\x1b]52;c;aGk=\x07@x.io", "Hi\nLINK: https://evil.example\u202e", "2026\r\n")
        for value in (msg.sender, msg.subject, msg.date):
            self.assertIsNone(re.search(r'[\x00-\x1f\x7f-\x9f\u202a-\u202e]', value))


class TestClipboard(unittest.TestCase):
    """Mocked so the suite runs headless and never touches the real clipboard."""

    def copy(self, env, available, failing=(), platform="linux"):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 1 if cmd[0] in failing else 0)

        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(tempmail.sys, "platform", platform), \
             mock.patch.object(tempmail.shutil, "which", lambda c: f"/usr/bin/{c}" if c in available else None), \
             mock.patch.object(tempmail.subprocess, "run", side_effect=fake_run):
            result = copy_to_clipboard("hello")
        return result, calls

    def test_falls_back_when_backend_fails(self):
        result, _ = self.copy({"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}, {"wl-copy", "xclip"}, failing={"wl-copy"})
        self.assertEqual(result, (True, "xclip"))

    def test_skips_wayland_without_session(self):
        result, calls = self.copy({"DISPLAY": ":0"}, {"wl-copy", "xclip"})
        self.assertEqual(result, (True, "xclip"))
        self.assertNotIn("wl-copy", [cmd[0] for cmd, _ in calls])

    def test_does_not_inherit_stdout(self):
        _, calls = self.copy({"WAYLAND_DISPLAY": "wayland-0"}, {"wl-copy"})
        self.assertTrue(calls)
        for _, kwargs in calls:
            self.assertEqual(kwargs.get("stdout"), subprocess.DEVNULL)

    def test_reports_failure_when_nothing_works(self):
        result, _ = self.copy({}, set())
        self.assertEqual(result, (False, "none"))

    def test_macos_uses_pbcopy_with_utf8_locale(self):
        result, calls = self.copy({"LANG": "C"}, {"pbcopy"}, platform="darwin")
        self.assertEqual(result, (True, "pbcopy"))
        self.assertEqual(calls[0][1]["env"]["LC_CTYPE"], "UTF-8")

    def test_wsl_uses_clip_exe(self):
        result, calls = self.copy({"WSL_DISTRO_NAME": "Ubuntu"}, {"clip.exe"})
        self.assertEqual(result, (True, "clip.exe"))
        self.assertEqual(calls[0][1]["input"], "hello".encode("utf-16le"))

    def test_windows_falls_back_to_clip_exe(self):
        with mock.patch.object(tempmail, "_win32_set_clipboard", return_value=False):
            result, _ = self.copy({}, {"clip.exe"}, platform="win32")
        self.assertEqual(result, (True, "clip.exe"))


class TestNotification(unittest.TestCase):
    def test_macos_text_passed_as_arguments(self):
        subject = 'quote " breaks out & (beep) & "'
        with mock.patch.object(tempmail.sys, "platform", "darwin"), \
             mock.patch.object(tempmail.shutil, "which", lambda c: "/usr/bin/osascript" if c == "osascript" else None), \
             mock.patch.object(tempmail.subprocess, "run") as run:
            send_notification("New Email", subject)
        argv = run.call_args[0][0]
        self.assertEqual(argv[-2:], [subject, "New Email"])
        self.assertFalse(any(subject in part for part in argv[:-2]))

    def toast(self, platform, wsl=False, env=None):
        subject = "quote \" and $(subexpression) <tag>"
        which = {"powershell.exe": "/win/powershell.exe"}
        with mock.patch.dict(os.environ, env or {}), \
             mock.patch.object(tempmail.sys, "platform", platform), \
             mock.patch.object(tempmail, "_is_wsl", return_value=wsl), \
             mock.patch.object(tempmail.shutil, "which", which.get), \
             mock.patch.object(tempmail.subprocess, "Popen") as popen:
            self.assertTrue(send_notification("New Email", subject))
        args, kwargs = popen.call_args
        return subject, args[0], kwargs

    def test_windows_toast_passes_text_via_environment(self):
        subject, argv, kwargs = self.toast("win32")
        script = base64.b64decode(argv[-1]).decode("utf-16-le")
        self.assertNotIn(subject, script)
        self.assertFalse(any(subject in part for part in argv))
        self.assertEqual(kwargs["env"]["TEMPMAIL_MESSAGE"], subject)
        self.assertEqual(kwargs["env"]["TEMPMAIL_TITLE"], "New Email")

    def test_wsl_forwards_toast_variables(self):
        _, _, kwargs = self.toast("linux", wsl=True, env={"WSLENV": "FOO/p"})
        self.assertEqual(kwargs["env"]["WSLENV"], "FOO/p:TEMPMAIL_TITLE:TEMPMAIL_MESSAGE")


class TestPlatformIntegration(unittest.TestCase):
    def cache_dir(self, platform, env):
        with mock.patch.object(tempmail.sys, "platform", platform), mock.patch.dict(os.environ, env):
            return tempmail._default_cache_dir()

    def test_cache_dir_follows_os_conventions(self):
        local_appdata = os.path.abspath("LocalAppData")
        xdg = os.path.abspath("xdg-cache")
        self.assertEqual(self.cache_dir("win32", {"LOCALAPPDATA": local_appdata}),
                         os.path.join(local_appdata, "tempmail"))
        self.assertEqual(self.cache_dir("darwin", {}),
                         os.path.join(os.path.expanduser("~/Library/Caches"), "tempmail"))
        self.assertEqual(self.cache_dir("linux", {"XDG_CACHE_HOME": xdg}), os.path.join(xdg, "tempmail"))
        # The XDG spec says relative paths must be ignored
        self.assertEqual(self.cache_dir("linux", {"XDG_CACHE_HOME": "relative"}),
                         os.path.join(os.path.expanduser("~/.cache"), "tempmail"))

    def test_open_url_falls_back_to_windows_browser_on_wsl(self):
        which = {"explorer.exe": "/win/explorer.exe"}
        with mock.patch.object(tempmail.webbrowser, "open", return_value=False), \
             mock.patch.object(tempmail, "_is_wsl", return_value=True), \
             mock.patch.object(tempmail.shutil, "which", which.get), \
             mock.patch.object(tempmail.subprocess, "Popen") as popen:
            self.assertTrue(tempmail.open_url("https://example.com/verify"))
        self.assertEqual(popen.call_args[0][0], ["explorer.exe", "https://example.com/verify"])

    def test_output_survives_non_utf8_encoding(self):
        # Windows pipes default to a legacy code page; emoji in --help must not crash
        out = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
        with mock.patch.object(sys, "argv", ["tempmail", "--help"]), mock.patch.object(sys, "stdout", out):
            with self.assertRaises(SystemExit) as cm:
                tempmail.main()
        self.assertEqual(cm.exception.code, 0)


class TestGuerrillaProvider(unittest.TestCase):
    def make_provider(self, failing_ids):
        p = GuerrillaMailProvider()
        p.last_seq = 10

        def fake_request(func, params=None):
            if func == "check_email":
                return {"list": [{"mail_id": str(i), "mail_subject": f"Mail {i}"} for i in (11, 12) if i > params["seq"]]}
            if func == "fetch_email":
                if params["email_id"] in failing_ids:
                    raise ConnectionError("network down")
                return {"mail_from": "a@example.com", "mail_subject": f"Mail {params['email_id']}", "mail_body": "hello"}
            raise AssertionError(func)

        p._request = fake_request
        return p

    def test_failed_fetch_is_retried(self):
        failing = {12}
        p = self.make_provider(failing)
        self.assertEqual([m.id for m in p.check_new_messages()], ["11"])
        self.assertEqual(p.last_seq, 11)
        failing.clear()
        self.assertEqual([m.id for m in p.check_new_messages()], ["12"])
        self.assertEqual(p.last_seq, 12)

    def test_total_failure_keeps_position(self):
        p = self.make_provider({11, 12})
        with self.assertRaises(ConnectionError):
            p.check_new_messages()
        self.assertEqual(p.last_seq, 10)

    def test_rejects_foreign_domain(self):
        p = GuerrillaMailProvider()
        p._request = mock.Mock(side_effect=AssertionError("no request expected"))
        with self.assertRaises(ValueError):
            p.init_inbox(custom_domain="gmail.com")


class TestMailTmProvider(unittest.TestCase):
    def test_failed_fetch_is_retried(self):
        p = MailTmProvider()
        failing = {"b"}

        def fake_request(method, path, payload=None, auth=False):
            if path == "/messages":
                return {"hydra:member": [{"id": "a"}, {"id": "b"}]}
            msg_id = path.rsplit("/", 1)[1]
            if msg_id in failing:
                raise ConnectionError("network down")
            return {"from": {"address": "a@example.com"}, "subject": msg_id, "text": "hello"}

        p._request = fake_request
        self.assertEqual([m.id for m in p.check_new_messages()], ["a"])
        self.assertNotIn("b", p.seen_ids)
        failing.clear()
        self.assertEqual([m.id for m in p.check_new_messages()], ["b"])

    def test_rejects_unknown_domain(self):
        p = MailTmProvider()
        p.get_domains = lambda: ["one.com"]
        p._request = mock.Mock(side_effect=AssertionError("no request expected"))
        with self.assertRaises(ValueError):
            p.init_inbox(custom_domain="gmail.com")

    def test_expired_token_refreshed(self):
        p = MailTmProvider()
        p.email_address, p.password, p.token = "x@one.com", "pw", "old"

        def fake_send(method, path, payload=None, auth=False):
            if path == "/token":
                return {"token": "new"}
            if p.token == "old":
                raise urllib.error.HTTPError(path, 401, "Expired JWT Token", None, io.BytesIO())
            return {"hydra:member": []}

        p._send = fake_send
        self.assertEqual(p.check_new_messages(), [])
        self.assertEqual(p.token, "new")


class TestSession(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cache = os.path.join(tmp.name, "cache")
        for name, value in (("CACHE_DIR", cache), ("SESSION_FILE", os.path.join(cache, "session.json"))):
            patcher = mock.patch.object(tempmail, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_session_file_is_private(self):
        tempmail.save_session({"created_at": time.time(), "password": "pw"})
        self.assertEqual(stat.S_IMODE(os.stat(tempmail.SESSION_FILE).st_mode), 0o600)
        self.assertFalse(os.path.exists(tempmail.SESSION_FILE + ".tmp"))

    def test_resume_window_counts_from_creation(self):
        tempmail.save_session({"provider": "guerrilla", "created_at": time.time() - 3700})
        self.assertIsNone(tempmail.load_session())
        tempmail.save_session({"provider": "guerrilla", "created_at": time.time() - 60})
        self.assertIsNotNone(tempmail.load_session())


class FakeClock:
    def __init__(self):
        self.now = 1_000_000.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeProvider(tempmail.BaseProvider):
    name = "Fake"

    def __init__(self, batches=()):
        self.batches = list(batches)

    def init_inbox(self, custom_user=None, custom_domain=None):
        return "bot@sharklasers.com"

    def get_session_data(self):
        return {"provider": "guerrilla"}

    def check_new_messages(self):
        return self.batches.pop(0) if self.batches else []


def make_msg(subject, body_text="", body_html=""):
    msg = EmailMessage("1", "sender@example.com", subject, "", body_text, body_html)
    msg.analyze()
    return msg


class TestWatchLoop(unittest.TestCase):
    def run_main(self, argv, provider=None):
        provider = provider or FakeProvider()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(sys, "argv", ["tempmail"] + argv))
            stack.enter_context(mock.patch.object(tempmail, "GuerrillaMailProvider", lambda: provider))
            stack.enter_context(mock.patch.object(tempmail, "time", FakeClock()))
            stack.enter_context(mock.patch.object(tempmail, "copy_to_clipboard", return_value=(True, "fake")))
            stack.enter_context(mock.patch.object(tempmail, "send_notification"))
            stack.enter_context(mock.patch.object(tempmail, "save_session"))
            stack.enter_context(mock.patch.object(tempmail, "load_session", return_value=None))
            self.browser_open = stack.enter_context(mock.patch.object(tempmail.webbrowser, "open"))
            stack.enter_context(mock.patch.object(sys, "stdout", out))
            stack.enter_context(mock.patch.object(sys, "stderr", err))
            with self.assertRaises(SystemExit) as cm:
                tempmail.main()
        return cm.exception.code, out.getvalue(), err.getvalue()

    @staticmethod
    def events(out):
        return [json.loads(line) for line in out.splitlines() if line.strip()]

    def test_keeps_watching_until_verification_email(self):
        provider = FakeProvider([[make_msg("Welcome!", "Thanks for joining our newsletter.")],
                                 [make_msg("Your code", "Your verification code is 482913")]])
        code, out, _ = self.run_main(["--json"], provider)
        self.assertEqual(code, 0)
        received = [e for e in self.events(out) if e["event"] == "message_received"]
        self.assertEqual(len(received), 2)
        self.assertEqual(received[1]["otps"], ["482913"])

    def test_timeout_exits_nonzero(self):
        code, out, _ = self.run_main(["--json", "-t", "10"])
        self.assertEqual(code, tempmail.EXIT_TIMEOUT)
        self.assertEqual(self.events(out)[-1]["event"], "timeout")

    def test_resume_without_session_fails(self):
        code, _, err = self.run_main(["-r", "--json"])
        self.assertEqual(code, 1)
        self.assertIn("No session to resume", err)

    def test_raw_output_cannot_be_spoofed(self):
        provider = FakeProvider([[make_msg("Hi\nLINK: https://evil.example/x", "Hello there")]])
        code, out, _ = self.run_main(["--raw", "-t", "10"], provider)
        self.assertEqual(code, tempmail.EXIT_TIMEOUT)
        self.assertFalse(any(line.startswith("LINK:") for line in out.splitlines()))

    def test_open_skips_low_confidence_links(self):
        provider = FakeProvider([[make_msg("Hello", "See https://example.com/signup")]])
        code, out, _ = self.run_main(["--json", "-o"], provider)
        self.assertEqual(code, 0)
        self.browser_open.assert_not_called()
        self.assertIsNone(self.events(out)[-1]["opened"])

    def test_open_follows_verification_links(self):
        html = '<a href="https://example.com/auth/confirm?token=ab89c02">Confirm your email</a>'
        provider = FakeProvider([[make_msg("Confirm", "", html)]])
        code, _, _ = self.run_main(["--json", "-o"], provider)
        self.assertEqual(code, 0)
        self.browser_open.assert_called_once_with("https://example.com/auth/confirm?token=ab89c02")

    def test_rejects_tiny_interval(self):
        code, _, err = self.run_main(["-i", "0"])
        self.assertEqual(code, 2)
        self.assertIn("--interval", err)


if __name__ == "__main__":
    unittest.main()
