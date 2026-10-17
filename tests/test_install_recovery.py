"""Install failure regressions; no host services or package repositories are changed."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from l4d2_cli import cli, host, installer, network, packages, steam, storage
from l4d2_cli.common import CliError, Redactor, REDACTOR, result
from l4d2_cli.model import Config, MIN_FREE, Source


def directory():
    parent = ROOT / ".test-tmp"
    parent.mkdir(exist_ok=True)
    return tempfile.TemporaryDirectory(dir=parent)


class SteamFailureTests(unittest.TestCase):
    def call(self, runner, account="anonymous"):
        with patch.object(steam, "run", runner), patch.object(steam.time, "sleep"), \
                patch.object(packages, "validate_game_tree", return_value={"build_id": "TEST"}) as validate:
            try:
                returned = steam.download_game(Path("/private/tool/steamcmd.sh"), Path("/private/game"),
                                               30, game_user=False, steam_user=account)
            except CliError as exc:
                return exc, validate
            return returned, validate

    def test_observed_invalid_platform_is_terminal_and_actionable(self):
        from unittest.mock import Mock
        runner = Mock(return_value=subprocess.CompletedProcess([], 8,
                      "Connecting anonymously to Steam Public...OK\n"
                      "ERROR! Failed to install app '222860' (Invalid platform)\n"))
        error, validate = self.call(runner)
        self.assertEqual(error.code, "steam_invalid_platform")
        self.assertEqual(runner.call_count, 1)
        validate.assert_not_called()
        self.assertEqual(error.details["phase"], "steam_content")
        self.assertEqual(error.details["app_id"], 222860)
        self.assertIn("does not resolve", str(error))
        self.assertTrue(any("--server-archive" in action and "--offline" in action
                            for action in error.details["next_steps"]))

    def test_authorization_failure_is_not_retried_as_network_failure(self):
        from unittest.mock import Mock
        for message in ("No subscription", "Invalid password", "Steam Guard", "Account logon denied"):
            with self.subTest(message=message):
                runner = Mock(return_value=subprocess.CompletedProcess([], 14, message))
                error, validate = self.call(runner)
                self.assertEqual(error.code, "steam_auth_required")
                self.assertEqual(runner.call_count, 1)
                validate.assert_not_called()
                self.assertIn("does not grant a Steam license", str(error))

    def test_transient_failure_retries_and_requires_explicit_game_success(self):
        from unittest.mock import Mock
        runner = Mock(side_effect=[subprocess.CompletedProcess([], 1, "Connection timeout"),
                                  subprocess.CompletedProcess([], 0, "Success! App '222860' fully installed.")])
        data, validate = self.call(runner)
        self.assertEqual(data["build_id"], "TEST")
        self.assertEqual(runner.call_count, 2)
        validate.assert_called_once()
        args = runner.call_args[0][0]
        self.assertEqual(args[args.index("+@NoPromptForPassword") + 1], "1")

    def test_zero_exit_without_content_success_is_not_install_success(self):
        from unittest.mock import Mock
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, "SteamCMD exited"))
        error, validate = self.call(runner)
        self.assertEqual(error.code, "steam_download_failed")
        self.assertEqual(runner.call_count, 3)
        validate.assert_not_called()

    def test_total_timeout_preserves_phase_and_offline_next_steps(self):
        from unittest.mock import Mock
        runner = Mock(side_effect=CliError("steamcmd.sh timed out.", "timeout", 124))
        error, validate = self.call(runner)
        self.assertEqual((error.code, error.exit_code), ("timeout", 124))
        self.assertEqual(runner.call_count, 1)
        self.assertTrue(error.details["partial_content_retained"])
        self.assertFalse(error.details["http_proxy_proves_steam_routing"])
        validate.assert_not_called()

    def test_cached_account_name_is_not_in_failure_details_or_progress(self):
        from unittest.mock import Mock
        account = "SYNTHETIC_PRIVATE_ACCOUNT"
        previous = REDACTOR.values.copy()
        try:
            runner = Mock(return_value=subprocess.CompletedProcess([], 1, "No subscription"))
            error, _ = self.call(runner, account)
            self.assertEqual(error.details["login"], "cached_account")
            self.assertNotIn(account, json.dumps(error.details) + str(error))
            self.assertEqual(REDACTOR.clean("Connecting " + account), "Connecting REDACTED")
        finally:
            REDACTOR.values = previous


class OfficialUpdateMapTests(unittest.TestCase):
    def test_last_stand_maps_are_discovered_in_official_update_directory(self):
        with directory() as temporary:
            root = Path(temporary)
            maps = root / "update/maps"; maps.mkdir(parents=True)
            for name in ("c14m1_junkyard", "c14m2_lighthouse", "custom_not_official"):
                (maps / (name + ".bsp")).write_bytes(b"fixture")
            self.assertEqual(packages.installed_maps(root),
                             ["c14m1_junkyard", "c14m2_lighthouse"])


class ToolCacheTests(unittest.TestCase):
    def source(self, root):
        tool = root / "input"
        (tool / "linux32").mkdir(parents=True)
        (tool / "linux32/steamcmd").write_bytes(b"\x7fELF" + b"fixture")
        (tool / "steamcmd.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (tool / "Steam/config").mkdir(parents=True)
        (tool / "Steam/config/config.vdf").write_text("SYNTHETIC_CACHE", encoding="utf-8")
        (tool / "Steam/logs").mkdir()
        (tool / "Steam/logs/private.log").write_text("DO_NOT_COPY", encoding="utf-8")
        return tool

    def test_new_cache_layout_is_cloned_privately_without_source_changes(self):
        with directory() as d:
            root = Path(d)
            source = self.source(root)
            original = {p.relative_to(source): (p.read_bytes(), p.stat().st_mode)
                        for p in source.rglob("*") if p.is_file()}
            dest = root / "cloned"
            steam.prepare_tool(Source(steamcmd_path=str(source / "steamcmd.sh")), dest, 10)
            self.assertEqual((dest / "Steam/config/config.vdf").read_text(), "SYNTHETIC_CACHE")
            self.assertFalse((dest / "Steam/logs").exists())
            for rel, data in original.items():
                self.assertEqual(((source / rel).read_bytes(), (source / rel).stat().st_mode), data)
            if os.name == "posix":
                self.assertEqual((dest / "Steam/config/config.vdf").stat().st_mode & 0o777, 0o600)
                self.assertEqual((dest / "Steam/config").stat().st_mode & 0o777, 0o700)
                self.assertEqual((dest / "Steam").stat().st_mode & 0o777, 0o700)

    @unittest.skipUnless(os.name == "posix", "POSIX symlink test")
    def test_linked_steam_parent_cannot_copy_unrelated_private_cache(self):
        with directory() as d:
            root = Path(d); source = self.source(root)
            (source / "Steam").rename(root / "external")
            (source / "Steam").symlink_to(root / "external", target_is_directory=True)
            with self.assertRaises(CliError) as caught:
                steam.prepare_tool(Source(steamcmd_path=str(source / "steamcmd.sh")), root / "clone", 10)
            self.assertEqual(caught.exception.code, "unsafe_path")
            self.assertEqual((root / "external/config/config.vdf").read_text(), "SYNTHETIC_CACHE")
            self.assertFalse((root / "clone/Steam/config/config.vdf").exists())


class ResumeSpaceTests(unittest.TestCase):
    def test_first_install_and_new_bundle_keep_full_budget(self):
        with directory() as d:
            config = Config(game_dir=str(Path(d) / "game")); Path(config.game_dir).mkdir()
            for state, source in (({}, Source()), ({"source": "online"}, Source(server_archive="unused"))):
                budget = installer.space_budget(config, state, source)
                self.assertEqual(budget["minimum_free_bytes"], MIN_FREE)
                self.assertEqual(budget["reusable_partial_bytes"], 0)

    def test_registered_partial_data_reduces_additional_free_budget(self):
        with directory() as d:
            target = Path(d) / "game"; target.mkdir()
            with patch.object(host, "game_account", return_value=(1000, 1000, True)), \
                    patch.object(installer, "allocated_content_bytes", return_value=10 * 1024**3):
                budget = installer.space_budget(Config(game_dir=str(target)),
                                                {"source": "online", "phase": "steam_content"}, Source())
            self.assertEqual(budget["minimum_free_bytes"], 10 * 1024**3)
            self.assertEqual(budget["reusable_partial_bytes"], 10 * 1024**3)

    @unittest.skipUnless(os.name == "posix", "Linux allocated tool blocks")
    def test_retry_credits_owned_tool_only_for_same_target_without_refresh(self):
        with directory() as d:
            root = Path(d); target = root / "game"; target.mkdir()
            tool = root / "managed/steamcmd"; tool.mkdir(parents=True)
            (tool / "runtime").write_bytes(b"x" * 16384)
            outside = root / "outside"; outside.write_bytes(b"x" * 16384)
            (tool / "linked").symlink_to(outside)
            allocated = (tool / "runtime").stat().st_blocks * 512
            config = Config(game_dir=str(target))
            state = {"source": "steamcmd_path", "phase": "steam_content", "game_dir": str(target)}
            paths = storage.Paths(steamcmd=tool)
            with patch.object(installer, "PATHS", paths), patch.object(
                    host, "game_account", return_value=(os.getuid(), os.getgid(), True)):
                budget = installer.space_budget(config, state, Source())
                self.assertEqual(budget["reusable_partial_bytes"], 0)
                self.assertEqual(budget["reusable_tool_bytes"], allocated)
                self.assertEqual(budget["minimum_free_bytes"], MIN_FREE - allocated)
                for source, recorded in (
                        (Source(steamcmd_path="refresh-request"), state),
                        (Source(), {**state, "game_dir": str(root / "other")})):
                    budget = installer.space_budget(config, recorded, source)
                    self.assertEqual(budget["reusable_tool_bytes"], 0)
                    self.assertEqual(budget["minimum_free_bytes"], MIN_FREE)
            self.assertEqual(outside.read_bytes(), b"x" * 16384)

    @unittest.skipUnless(os.name == "posix", "Registered Linux install preflight")
    def test_resume_preflight_does_not_require_unused_bootstrap_network(self):
        from dataclasses import asdict
        with directory() as d:
            root = Path(d); target = root / "game"; target.mkdir()
            tool = root / "tool"; tool.mkdir()
            ready = tool / "steamcmd.sh"; ready.write_text("#!/bin/sh\n")
            paths = storage.Paths(
                config=root / "etc/config.toml", secrets=root / "etc/secrets.toml",
                state=root / "state.json", hashes=root / "hashes.json",
                journal=root / "journal.json", steamcmd=tool, cache=root / "cache",
                unit=root / "unit.service")
            config = Config(game_dir=str(target))
            identity = "11111111-1111-1111-1111-111111111111"
            state = {"schema_version": 1, "installation_id": identity,
                     "game_dir": str(target), "config": asdict(config),
                     "source": "steamcmd_path", "phase": "steam_content"}
            storage.save_json(paths.state, state)
            storage.save_json(target / installer.MARKER, {"installation_id": identity})
            def probe(source, **kwargs):
                # A missing bootstrap endpoint must not prevent a cached-tool retry.
                return {"required_failures": 0 if source.steamcmd_path else 1,
                        "needs_steam": kwargs["needs_steam"]}
            with patch.object(installer, "PATHS", paths), patch.object(host, "require_platform"), \
                    patch.object(host, "platform_report", return_value={}), \
                    patch.object(host, "missing_dependencies", return_value=[]), \
                    patch.object(host, "game_account", return_value=(os.getuid(), os.getgid(), True)), \
                    patch.object(network, "check", side_effect=probe):
                plan, _, _ = installer.install_plan(str(target), Source(), 30)
                self.assertEqual(plan["network"]["required_failures"], 0)
                self.assertTrue(plan["network"]["needs_steam"])
                ready.unlink()
                plan, _, _ = installer.install_plan(str(target), Source(), 30)
                self.assertEqual(plan["network"]["required_failures"], 1)

    def test_unrelated_source_or_account_receives_no_credit(self):
        with directory() as d:
            target = Path(d) / "game"; target.mkdir()
            with patch.object(host, "game_account", return_value=(-1, -1, False)), \
                    patch.object(installer, "allocated_content_bytes", side_effect=AssertionError("No credit")):
                for source in (Source(), Source(steamcmd_archive="unused")):
                    budget = installer.space_budget(Config(game_dir=str(target)), {"source": "online"}, source)
                    self.assertEqual(budget["minimum_free_bytes"], MIN_FREE)

    def test_resume_keeps_two_gib_headroom(self):
        with directory() as d:
            target = Path(d) / "game"; target.mkdir()
            with patch.object(host, "game_account", return_value=(1000, 1000, True)), \
                    patch.object(installer, "allocated_content_bytes", return_value=MIN_FREE * 2):
                budget = installer.space_budget(Config(game_dir=str(target)), {"source": "online"}, Source())
            self.assertEqual(budget["minimum_free_bytes"], 2 * 1024**3)

    def test_complete_runtime_resume_requires_content_validation(self):
        with directory() as d:
            target = Path(d) / "game"; target.mkdir()
            config = Config(game_dir=str(target)); state = {"source": "online", "phase": "runtime_validation"}
            with patch.object(packages, "validate_game_tree", return_value={"build_id": "TEST"}):
                budget = installer.space_budget(config, state, Source())
            self.assertTrue(budget["resume_runtime_validation"])
            self.assertEqual(budget["minimum_free_bytes"], 64 * 1024**2)
            with patch.object(packages, "validate_game_tree", side_effect=CliError("Missing srcds", "incomplete_game")), \
                    patch.object(host, "game_account", return_value=(-1, -1, False)):
                budget = installer.space_budget(config, state, Source())
            self.assertFalse(budget["resume_runtime_validation"])
            self.assertEqual(budget["minimum_free_bytes"], MIN_FREE)

    def test_committed_import_finalizes_without_second_full_extraction_budget(self):
        with directory() as d:
            config = Config(game_dir=str(Path(d) / "game"))
            for flag in ("import_ready", "import_committed"):
                budget = installer.space_budget(config, {"bundle_sha256": "TEST", flag: True},
                                                Source(server_archive="unused", offline=True))
                self.assertEqual(budget["minimum_free_bytes"], 64 * 1024**2)

    @unittest.skipUnless(os.name == "posix", "Linux allocated-block test")
    def test_real_sparse_linked_and_foreign_owner_files_do_not_inflate_credit(self):
        with directory() as d:
            root = Path(d); target = root / "game"; target.mkdir()
            regular = target / "real.bin"; regular.write_bytes(b"x" * 8192)
            sparse = target / "sparse.bin"
            with sparse.open("wb") as f: f.truncate(64 * 1024**2)
            outside = root / "outside.bin"; outside.write_bytes(b"x" * 8192)
            (target / "link.bin").symlink_to(outside)
            os.link(outside, target / "hardlink.bin")
            expected = (regular.stat().st_blocks + sparse.stat().st_blocks) * 512
            self.assertEqual(installer.allocated_content_bytes(target, os.getuid()), expected)
            self.assertLess(expected, sparse.stat().st_size)
            self.assertEqual(installer.allocated_content_bytes(target, os.getuid() + 1), 0)


class ProxyPreflightTests(unittest.TestCase):
    def report(self, bypass=False, archive_ok=True):
        def probe(kind, target, timeout):
            return {"status": "fail", "message": "DNS failed", "category": "dns"} if kind == "dns" else \
                   {"status": "pass" if archive_ok else "fail", "message": "HTTP", "category": "connection"}
        with patch.object(network, "getproxies", return_value={"https": "http://127.0.0.1:7897"}), \
                patch.object(network, "proxy_bypass", return_value=bypass), \
                patch.object(network, "probe", side_effect=probe):
            return network.check(Source(), archive_only=True)

    def test_proxied_archive_does_not_require_direct_destination_dns(self):
        report = self.report()
        self.assertEqual(report["required_failures"], 0)
        dns = next(x for x in report["checks"] if x["name"] == "steamcmd_dns")
        self.assertEqual(dns["status"], "warn")
        self.assertFalse(dns["required"])
        self.assertFalse(report["download_guaranteed"])

    def test_no_proxy_bypass_preserves_direct_dns_requirement(self):
        self.assertEqual(self.report(bypass=True)["required_failures"], 1)

    def test_proxy_connection_failure_still_blocks(self):
        self.assertEqual(self.report(archive_ok=False)["required_failures"], 1)


class OutputTests(unittest.TestCase):
    def test_config_paths_and_protocol_flags_are_preserved(self):
        redactor = Redactor()
        for text in ("/etc/l4d2-cli/secrets.toml", "/tmp/secret.json", "password_required=true",
                     "steam_token_cache", "--clear-rcon-password"):
            self.assertEqual(redactor.clean(text), text)

    def test_cfg_json_proxy_and_registered_escaped_secrets_are_hidden(self):
        redactor = Redactor()
        redactor.register('SYNTHETIC_"PRIVATE')
        for text in ('rcon_password "SYNTHETIC_PASSWORD"', 'password=SYNTHETIC_PASSWORD',
                     '"token": "SYNTHETIC_TOKEN"', 'https://user:SYNTHETIC_PASSWORD@proxy.example/',
                     json.dumps('SYNTHETIC_"PRIVATE')):
            clean = redactor.clean(text)
            self.assertNotIn("SYNTHETIC_", clean)
            self.assertIn("REDACTED", clean)



class ToolReplacementTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "POSIX private-cache transaction")
    def test_failed_refresh_restores_original_cache_and_success_preserves_it(self):
        import time
        with directory() as d:
            root = Path(d)
            paths = storage.Paths(state=root / "state.json", steamcmd=root / "managed/steamcmd",
                                  cache=root / "cache")
            paths.cache.mkdir()
            fixture = ToolCacheTests()
            old = fixture.source(root / "old")
            source = fixture.source(root / "new")
            (old / "Steam/config/config.vdf").write_text("TEST_OLD_CACHE", encoding="utf-8")
            (source / "Steam/config/config.vdf").write_text("TEST_AUTHORIZED_CACHE", encoding="utf-8")
            paths.steamcmd.parent.mkdir()
            old.rename(paths.steamcmd)
            state = {"installation_id": "11111111-1111-1111-1111-111111111111", "source": "steamcmd_path"}
            real_rename = os.rename
            def interrupted_move(src, dest):
                if Path(src).parent == paths.cache and Path(dest) == paths.steamcmd:
                    raise OSError("Synthetic interrupted cache commit")
                return real_rename(src, dest)
            with patch.object(installer, "PATHS", paths), patch.object(host, "own_tree"), \
                    patch.object(installer.os, "chown"), patch.object(installer.os, "rename", side_effect=interrupted_move):
                with self.assertRaises(OSError):
                    installer.prepare_managed_tool(Source(steamcmd_path=str(source / "steamcmd.sh")),
                                                   state, os.getuid(), os.getgid(), time.monotonic() + 20)
            self.assertEqual((paths.steamcmd / "Steam/config/config.vdf").read_text(), "TEST_OLD_CACHE")
            self.assertNotIn("pending_tool_backup", state)
            with patch.object(installer, "PATHS", paths), patch.object(host, "own_tree"), \
                    patch.object(installer.os, "chown"):
                result = installer.prepare_managed_tool(Source(steamcmd_path=str(source / "steamcmd.sh")),
                                                        state, os.getuid(), os.getgid(), time.monotonic() + 20)
            self.assertEqual(result, paths.steamcmd / "steamcmd.sh")
            self.assertEqual((paths.steamcmd / "Steam/config/config.vdf").read_text(), "TEST_AUTHORIZED_CACHE")
            backup = Path(state["previous_tools"][-1])
            self.assertEqual((backup / "Steam/config/config.vdf").read_text(), "TEST_OLD_CACHE")
            self.assertEqual(backup.stat().st_mode & 0o777, 0o700)

    @unittest.skipUnless(os.name == "posix", "POSIX private-cache transaction")
    def test_power_loss_between_renames_restores_owned_cache_on_retry(self):
        import time
        with directory() as d:
            root = Path(d)
            paths = storage.Paths(state=root / "state.json", steamcmd=root / "managed/steamcmd",
                                  cache=root / "cache")
            fixture = ToolCacheTests()
            old = fixture.source(root / "old")
            paths.steamcmd.parent.mkdir()
            identity = "11111111-1111-1111-1111-111111111111"
            backup = paths.steamcmd.with_name("steamcmd.previous-" + identity + "-1")
            old.rename(backup)
            state = {"installation_id": identity, "pending_tool_backup": str(backup), "source": "online"}
            with patch.object(installer, "PATHS", paths), patch.object(host, "own_tree") as own, \
                    patch.object(steam, "prepare_tool", side_effect=AssertionError("No new download")):
                result = installer.prepare_managed_tool(Source(), state, os.getuid(), os.getgid(),
                                                        time.monotonic() + 20)
            self.assertTrue(result.is_file())
            own.assert_called_once_with(paths.steamcmd, os.getuid(), os.getgid())
            self.assertNotIn("pending_tool_backup", json.loads(paths.state.read_text()))
            self.assertFalse(backup.exists())

    @unittest.skipUnless(os.name == "posix", "POSIX private-cache transaction")
    def test_unregistered_cache_backup_path_is_rejected(self):
        import time
        with directory() as d:
            root = Path(d)
            paths = storage.Paths(state=root / "state.json", steamcmd=root / "managed/steamcmd",
                                  cache=root / "cache")
            state = {"installation_id": "11111111-1111-1111-1111-111111111111",
                     "pending_tool_backup": str(root / "unrelated")}
            with patch.object(installer, "PATHS", paths), patch.object(steam, "prepare_tool") as prepare:
                with self.assertRaises(CliError) as caught:
                    installer.prepare_managed_tool(Source(), state, 1000, 1000, time.monotonic() + 20)
            self.assertEqual(caught.exception.code, "unsafe_tool")
            prepare.assert_not_called()

    def test_switching_steam_acquisition_keeps_registered_partial_credit(self):
        with directory() as d:
            target = Path(d) / "game"; target.mkdir()
            with patch.object(host, "game_account", return_value=(1000, 1000, True)), \
                    patch.object(installer, "allocated_content_bytes", return_value=10 * 1024**3):
                budget = installer.space_budget(Config(game_dir=str(target)), {"source": "online"},
                                                Source(steamcmd_path="fixture"))
            self.assertEqual(budget["minimum_free_bytes"], 10 * 1024**3)

    def test_steam_tcp_option_reaches_native_command(self):
        from unittest.mock import Mock
        args = cli.parser().parse_args(["server", "install", "--steam-tcp", "--yes"])
        self.assertTrue(args.steam_tcp)
        runner = Mock(return_value=subprocess.CompletedProcess([], 8, "Invalid platform"))
        with patch.object(steam, "run", runner):
            with self.assertRaises(CliError):
                steam.download_game(Path("/private/tool/steamcmd.sh"), Path("/private/game"), 30,
                                    game_user=False, steam_tcp=True)
        self.assertEqual(runner.call_args[0][0][1], "-tcp")


if __name__ == "__main__":
    unittest.main()


class InputAndResumeNetworkTests(unittest.TestCase):
    def test_invalid_account_is_rejected_before_install_planning(self):
        with patch.object(installer, "install_plan") as plan:
            with self.assertRaises(CliError) as caught:
                installer.install(None, Source(), dry_run=False, yes=True, timeout=30,
                                  steam_user="+quit")
        self.assertEqual(caught.exception.code, "invalid_steam_user")
        plan.assert_not_called()

    def test_validated_complete_content_resume_has_no_steam_probes(self):
        with patch.object(network, "probe") as probe, patch.object(network, "getproxies", return_value={}):
            report = network.check(Source(), needs_steam=False)
        probe.assert_not_called()
        self.assertEqual(report["required_failures"], 0)
        self.assertFalse(report["download_guaranteed"])
        self.assertFalse(any(entry["name"] == "steam_bootstrap_login_content" for entry in report["checks"]))

    def test_resume_still_checks_missing_dependency_repositories(self):
        with patch.object(network, "detect_adapter") as adapter, \
                patch.object(network, "repository_targets", return_value=[
                    {"kind": "repository", "target": "https://repo.invalid/InRelease", "scope": "fixture"}]), \
                patch.object(network, "probe", return_value={"status": "fail"}) as probe, \
                patch.object(network, "getproxies", return_value={}):
            report = network.check(Source(), needs_steam=False, needs_dependencies=True)
        self.assertEqual(probe.call_count, 1)
        self.assertEqual(report["required_failures"], 1)
