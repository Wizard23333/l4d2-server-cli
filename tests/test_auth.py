"""Authentication boundary and credential-cache regressions; synthetic accounts only."""
from __future__ import annotations

import io
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from l4d2_cli import auth, cli, steam
from l4d2_cli.common import CliError, REDACTOR
from l4d2_cli.model import Source
from test_install_recovery import directory


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.previous = REDACTOR.values.copy()

    def tearDown(self):
        REDACTOR.values = self.previous

    def linux(self):
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch.object(auth.os, "name", "posix"))
        stack.enter_context(patch.object(auth.os, "geteuid", return_value=1000, create=True))
        stack.enter_context(patch.object(auth.platform, "system", return_value="Linux"))
        stack.enter_context(patch.object(auth.platform, "machine", return_value="x86_64"))
        stack.enter_context(patch.object(auth.platform, "libc_ver", return_value=("glibc", "2.31")))
        return stack

    def test_root_login_refused_before_network_or_disk(self):
        with self.linux(), patch.object(auth.os, "geteuid", return_value=0), \
                patch.object(auth.network, "check") as network:
            with self.assertRaises(CliError) as caught:
                auth.login(Source(), "SYNTHETIC_USER", "/private/cache", 30)
        self.assertEqual(caught.exception.code, "root_login_refused")
        network.assert_not_called()

    def test_nonterminal_refused_before_network_or_disk(self):
        with self.linux(), patch.object(auth.sys, "stdin", io.StringIO()), \
                patch.object(auth.network, "check") as network:
            with self.assertRaises(CliError) as caught:
                auth.login(Source(), "SYNTHETIC_USER", "/private/cache", 30)
        self.assertEqual(caught.exception.code, "interactive_required")
        network.assert_not_called()

    def test_no_password_token_or_json_auth_flags(self):
        for option in ("--password", "--token", "--json"):
            with self.subTest(option=option), self.assertRaises(CliError):
                cli.parser().parse_args(["auth", "login", "--steam-user", "SYNTHETIC_USER", option, "SECRET"])
        args = cli.parser().parse_args(["auth", "login", "--steam-user", "SYNTHETIC_USER",
                                      "--steamcmd-path", "/private/tool/steamcmd.sh", "--steam-tcp"])
        self.assertEqual(args.group, "auth")
        self.assertTrue(args.steam_tcp)

    def test_account_name_cannot_inject_native_commands(self):
        for name in ("+quit", "../account", "user name", "name;quit", "name@host", "", "x" * 65):
            with self.subTest(name=name), self.assertRaises(CliError):
                steam.validate_login_name(name)
        steam.validate_login_name("SYNTHETIC_USER123")

    def test_headless_verification_does_not_trust_exit_zero(self):
        for text in ("SteamCMD exited", "Logging in user TEST...FAILED",
                     "Connecting anonymously to Steam Public...OK\nWaiting for user info...OK",
                     "Logging in user TEST...OK\nWaiting for user info...OK\nInvalid password"):
            runner = Mock(return_value=subprocess.CompletedProcess([], 0, text))
            with self.subTest(text=text), patch.object(auth, "run", runner), self.assertRaises(CliError) as caught:
                auth.verify_cached_login(Path("/private/steamcmd.sh"), "SYNTHETIC_USER", {}, 30, False)
            self.assertEqual(caught.exception.code, "steam_auth_unverified")
            self.assertEqual(caught.exception.details["download_authorization"], "unknown")

    def test_cached_login_accepts_native_colors_and_compat_status(self):
        # Redacted shape of real SteamCMD 1791918946 output from a cached login.
        text = (
            "\x1b[0m\x1b[1mLogging in using cached credentials.\n"
            "\x1b[0mLogging in user 'SYNTHETIC_USER' [U:1:123] to Steam Public..."
            "\x1b[0mOK\n"
            "\x1b[0mWaiting for client config...\x1b[0m"
            "Waiting for compat in post-logon took: 0.098916sOK\n"
            "\x1b[0mWaiting for user info...\x1b[0mOK\n"
            "\x1b[0mUnloading Steam API...\x1b[0mOK\n"
        )
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, text))
        with patch.object(auth, "run", runner):
            auth.verify_cached_login(Path("/private/steamcmd.sh"), "SYNTHETIC_USER", {}, 30, False)

    def test_colored_verification_still_requires_success_and_rejects_failures(self):
        success = (
            "\x1b[0mLogging in user TEST to Steam Public...\x1b[0mOK\n"
            "\x1b[0mWaiting for user info...\x1b[0mOK\n"
        )
        cases = [
            (0, success + "Invalid \x1b[31mpassword\x1b[0m"),
            (0, success + "Cached credentials \x1b[31mnot found\x1b[0m"),
            (0, success + "Cached credentials expired"),
            (1, success),
            (0, "\x1b[0mConnecting anonymously to Steam Public...\x1b[0mOK\n"
                "Waiting for user info...\x1b[0mOK"),
            (0, "\x1b[0mLogging in user TEST to Steam Public...\x1b[0mOK\n"
                "Waiting for user info..."),
        ]
        for code, text in cases:
            with self.subTest(code=code, text=text), patch.object(
                    auth, "run", return_value=subprocess.CompletedProcess([], code, text)):
                with self.assertRaises(CliError) as caught:
                    auth.verify_cached_login(Path("/private/steamcmd.sh"), "SYNTHETIC_USER", {}, 30, False)
                self.assertEqual(caught.exception.code, "steam_auth_unverified")

    def test_cached_login_verification_never_supplies_a_password(self):
        runner = Mock(return_value=subprocess.CompletedProcess([], 0,
                      "Logging in user TEST [U:1:123] to Steam Public...OK\nWaiting for user info...OK"))
        with patch.object(auth, "run", runner):
            auth.verify_cached_login(Path("/private/steamcmd.sh"), "SYNTHETIC_USER", {}, 30, True)
        args = runner.call_args[0][0]
        self.assertEqual(args, [str(Path("/private/steamcmd.sh")), "-tcp", "+@ShutdownOnFailedCommand", "1",
                                "+@NoPromptForPassword", "1", "+login", "SYNTHETIC_USER", "+quit"])
        self.assertFalse(runner.call_args[1].get("stream", False))

    def test_interactive_child_inherits_terminal_without_capturing_secrets(self):
        process = Mock()
        process.wait.return_value = 0
        process.pid = 12345
        with patch.object(auth, "foreground_terminal") as terminal, \
                patch.object(auth.os, "tcsetpgrp", create=True), \
                patch.object(auth.os, "killpg", create=True), \
                patch.object(auth.signal, "SIGCONT", getattr(auth.signal, "SIGCONT", 18), create=True), \
                patch.object(auth.subprocess, "Popen", return_value=process) as launch:
            auth.interactive(["/private/steamcmd.sh", "+login", "SYNTHETIC_USER", "+quit"],
                             Path("/private/steamcmd.sh"), {"HOME": "/private"}, 10)
        self.assertNotIn("stdin", launch.call_args[1])
        self.assertNotIn("stdout", launch.call_args[1])
        self.assertNotIn("stderr", launch.call_args[1])
        self.assertNotIn("start_new_session", launch.call_args[1])
        self.assertIs(launch.call_args[1]["preexec_fn"], auth._terminal_child)

    def test_interactive_timeout_terminates_owned_process_group(self):
        process = Mock()
        process.wait.side_effect = subprocess.TimeoutExpired("steamcmd.sh", 1)
        process.pid = 12345
        with patch.object(auth, "foreground_terminal") as terminal, \
                patch.object(auth.os, "tcsetpgrp", create=True), \
                patch.object(auth.os, "killpg", create=True), \
                patch.object(auth.signal, "SIGCONT", getattr(auth.signal, "SIGCONT", 18), create=True), \
                patch.object(auth.subprocess, "Popen", return_value=process), \
                patch.object(auth, "_terminate") as terminate:
            with self.assertRaises(CliError) as caught:
                auth.interactive(["/private/steamcmd.sh"], Path("/private/steamcmd.sh"), {}, 1)
        self.assertEqual((caught.exception.code, caught.exception.exit_code), ("timeout", 124))
        terminate.assert_called_once_with(process)
        terminal.return_value.__exit__.assert_called_once()

    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_private_cache_permissions_and_links(self):
        with directory() as temporary:
            cache = Path(temporary) / "cache"
            cache.mkdir()
            config = cache / "Steam/config"
            config.mkdir(parents=True)
            credential = config / "config.vdf"
            credential.write_text("SYNTHETIC_CACHE")
            credential.chmod(0o644)
            auth.private_tree(cache, chmod=False)
            self.assertEqual(credential.stat().st_mode & 0o777, 0o644)
            auth.private_tree(cache)
            self.assertEqual(credential.stat().st_mode & 0o777, 0o600)
            self.assertEqual(config.stat().st_mode & 0o777, 0o700)
            os.link(credential, config / "linked.vdf")
            with self.assertRaises(CliError):
                auth.private_tree(cache)

    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_unregistered_directory_is_rejected_without_chmod(self):
        with directory() as temporary:
            cache = Path(temporary) / "existing"
            cache.mkdir(mode=0o755)
            file = cache / "existing.txt"
            file.write_text("KEEP")
            file.chmod(0o644)
            with patch.object(auth.sys.stdin, "isatty", return_value=True), \
                    patch.object(auth.sys.stdout, "isatty", return_value=True), \
                    patch.object(auth.sys.stderr, "isatty", return_value=True), \
                    patch.object(auth.host, "missing_dependencies", return_value=[]):
                with self.assertRaises(CliError) as caught:
                    auth.login(Source(), "SYNTHETIC_USER", str(cache), 10)
            self.assertEqual(caught.exception.code, "auth_cache_conflict")
            self.assertEqual(file.stat().st_mode & 0o777, 0o644)

    @unittest.skipUnless(os.name == "posix", "Linux controlling terminal")
    def test_inherited_terminal_password_input_is_not_echoed_or_captured(self):
        from test_auth_first_login import tty_run
        child = (
            "import os,getpass; f=os.open('/dev/tty',os.O_RDWR); "
            "assert os.tcgetpgrp(f)==os.getpgrp(); os.close(f); "
            "p=getpass.getpass('SYNTHETIC_PROMPT:'); "
            "print('NATIVE_INPUT_OK' if p == 'SYNTHETIC_PASSWORD' else 'INPUT_FAILED')"
        )
        driver = (
            "import sys; from pathlib import Path; "
            "sys.path.insert(0,sys.argv[1]); from l4d2_cli import auth; "
            "auth.interactive([sys.executable,'-c',sys.argv[2]],Path(sys.executable),__import__('os').environ.copy(),5)"
        )
        code, received = tty_run([sys.executable,"-c",driver,str(ROOT/"src"),child],
                                 [(b"SYNTHETIC_PROMPT:", b"SYNTHETIC_PASSWORD")])
        self.assertEqual(code,0,received.decode(errors="replace"))
        self.assertIn(b"NATIVE_INPUT_OK",received)
        self.assertNotIn(b"SYNTHETIC_PASSWORD",received)


if __name__ == "__main__":
    unittest.main()
