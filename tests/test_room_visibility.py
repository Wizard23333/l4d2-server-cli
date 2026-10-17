"""Public/Steam group configuration, upgrade compatibility and live-state reporting."""
from dataclasses import asdict, replace
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from l4d2_cli import cli, manager, model, storage
from l4d2_cli.common import CliError
from test_install_recovery import directory


def config(**values):
    return model.Config(game_dir=str(ROOT / "test-game"), **values)


class VisibilityModelTests(unittest.TestCase):
    def test_public_and_group_render_real_engine_settings(self):
        for selected, expected in [(config(), ('""', '"0"')),
                                   (config(visibility="group", steam_group_id="12345678"), ('"12345678"', '"1"'))]:
            with self.subTest(visibility=selected.visibility):
                rendered = model.render_cfg(selected, {})
                self.assertIn("sv_steamgroup " + expected[0] + "\n", rendered)
                self.assertIn("sv_steamgroup_exclusive " + expected[1] + "\n", rendered)
                self.assertIn("sv_allow_lobby_connect_only " + expected[1] + "\n", rendered)
                self.assertIn('sv_lan "0"', rendered)
                self.assertIn('sv_password ""', rendered)

    def test_missing_group_and_conflicting_public_configuration_are_rejected(self):
        for selected, code in [(config(visibility="hidden"), "invalid_visibility"),
                               (config(visibility="group"), "steam_group_required"),
                               (config(steam_group_id="123"), "invalid_group_settings")]:
            with self.subTest(code=code), self.assertRaises(CliError) as caught:
                selected.validate()
            self.assertEqual(caught.exception.code, code)

    def test_group_id_is_one_short_ascii_uint32_and_never_cfg_text(self):
        for group in ["0", "-1", "01", "4294967296", "103582791441867086", "１２３", "1,2",
                      "https://steamcommunity.com/groups/example", '1";quit', "1\nquit", "+1", " 1 "]:
            with self.subTest(group=group), self.assertRaises(CliError) as caught:
                config(visibility="group", steam_group_id=group).validate()
            self.assertEqual(caught.exception.code, "invalid_steam_group_id")
        for group in ["1", "4294967295", "12345678"]:
            config(visibility="group", steam_group_id=group).validate()
        for group in [None, 123, True]:
            with self.subTest(group=group), self.assertRaises(CliError):
                config(visibility="group", steam_group_id=group).validate()

    def test_dev9_configuration_loads_without_rewriting_and_group_roundtrips(self):
        with directory() as temporary:
            path = Path(temporary) / "config.toml"
            old = "\n".join(l for l in config().toml().splitlines()
                            if not l.startswith(("visibility =", "steam_group_id ="))) + "\n"
            path.write_text(old, encoding="utf-8")
            loaded = storage.load_config(storage.Paths(config=path))
            self.assertEqual((loaded.visibility, loaded.steam_group_id), ("public", ""))
            self.assertEqual(path.read_text(encoding="utf-8"), old)
            selected = config(visibility="group", steam_group_id="12345678")
            path.write_text(selected.toml(), encoding="utf-8")
            self.assertEqual(storage.load_config(storage.Paths(config=path)), selected)


class VisibilityInputsTests(unittest.TestCase):
    def inputs(self, flags, old=None):
        args = cli.parser().parse_args(["server", "configure", *flags, "--yes", "--json"])
        with patch.object(cli.host, "require_platform"), \
                patch.object(cli.manager, "installed", return_value=(old or config(), {})), \
                patch.object(cli.storage, "load_secrets", return_value={"rcon_password": "SYNTHETIC"}):
            return cli.configured_inputs(args)

    def test_group_flags_and_id_only_select_group_without_discarding_secrets(self):
        for flags in [["--visibility", "group", "--steam-group-id", "12345678"],
                      ["--steam-group-id", "12345678"]]:
            selected, private = self.inputs(flags)
            self.assertEqual((selected.visibility, selected.steam_group_id), ("group", "12345678"))
            self.assertEqual(private, {"rcon_password": "SYNTHETIC"})

    def test_public_clears_old_group_and_unrelated_updates_keep_group(self):
        old = config(visibility="group", steam_group_id="12345678")
        public, _ = self.inputs(["--visibility", "public"], old)
        self.assertEqual((public.visibility, public.steam_group_id), ("public", ""))
        kept, _ = self.inputs(["--name", "Changed Name"], old)
        self.assertEqual((kept.visibility, kept.steam_group_id), ("group", "12345678"))

    def test_missing_or_ambiguous_cli_settings_fail_before_configuration(self):
        for flags, code in [(["--visibility", "group"], "steam_group_required"),
                            (["--visibility", "public", "--steam-group-id", "123"], "invalid_group_settings"),
                            (["--steam-group-id", "1,2"], "invalid_steam_group_id")]:
            with self.subTest(flags=flags), self.assertRaises(CliError) as caught:
                self.inputs(flags)
            self.assertEqual(caught.exception.code, code)

    def test_wizard_can_select_group_and_switch_back_to_public(self):
        args = cli.parser().parse_args(["server", "configure", "--yes"])
        for old, answers, expected in [(config(), ["", "", "", "", "", "group", "", "12345678"], ("group", "12345678")),
                                       (config(visibility="group", steam_group_id="12345678"),
                                        ["", "", "", "", "", "public", ""], ("public", ""))]:
            with self.subTest(expected=expected), \
                    patch.object(cli.host, "require_platform"), \
                    patch.object(cli.manager, "installed", return_value=(old, {})), \
                    patch.object(cli.storage, "load_secrets", return_value={}), \
                    patch.object(cli.sys.stdin, "isatty", return_value=True), \
                    patch("builtins.input", side_effect=answers), \
                    patch.object(cli.getpass, "getpass", return_value=""):
                selected, _ = cli.configured_inputs(args)
                self.assertEqual((selected.visibility, selected.steam_group_id), expected)


class VisibilityStatusTests(unittest.TestCase):
    def test_group_guide_does_not_offer_a_direct_connect_command(self):
        with patch.object(manager, "query_address", return_value="10.1.2.3"):
            public = manager.connection_guide(config())
            group = manager.connection_guide(config(visibility="group", steam_group_id="12345678"))
        self.assertEqual(public["player_command"], "connect YOUR_SERVER_IP:27015")
        self.assertEqual(group["join_method"], "steam_group_lobby")
        self.assertTrue(group["lobby_only"])
        self.assertIsNone(group["player_command"])
        self.assertEqual(group["client_join"], "unverified")

    def test_running_and_desired_visibility_differ_until_restart(self):
        desired = config(visibility="group", steam_group_id="12345678", revision=2)
        running = config()
        state = {"status": "installed", "running_invocation_id": "OLD", "applied_revision": 1,
                 "running_config": asdict(running)}
        info = {"MainPID": 123, "ActiveState": "active", "InvocationID": "OLD"}
        with patch.object(manager.host, "require_platform"), \
                patch.object(manager.storage, "read_json", return_value=state), \
                patch.object(manager, "registered_config", return_value=desired), \
                patch.object(manager.host, "service_info", return_value=info), \
                patch.object(manager.host, "udp_owner", return_value=True), \
                patch.object(manager, "query_address", return_value="10.1.2.3"), \
                patch.object(manager.a2s, "query", return_value={"map": "c1m1_hotel"}):
            report = manager.status()
        self.assertTrue(report["configuration_pending"])
        self.assertEqual(report["desired_config"]["visibility"], "group")
        self.assertEqual(report["running_config"]["visibility"], "public")
        self.assertEqual(report["connection"]["join_method"], "direct")

    def test_group_dry_run_lists_changes_without_mutating_or_restarting(self):
        old = config()
        with patch.object(manager.host, "require_platform"), \
                patch.object(manager, "installed", return_value=(old, {})), \
                patch.object(manager.packages, "installed_maps", return_value=[old.map]), \
                patch.object(manager.host, "service_info", return_value={"MainPID": 0}), \
                patch.object(manager.host, "verify_service"), \
                patch.object(manager.host, "check_udp_available"), \
                patch.object(manager, "apply_configuration") as apply, \
                patch.object(manager.host, "systemctl") as systemctl:
            report = manager.configure(replace(old, visibility="group", steam_group_id="12345678"), {},
                                       dry_run=True, yes=True)
        self.assertEqual(report["plan"]["changes"]["steam_group_id"]["after"], "12345678")
        self.assertFalse(report["plan"]["restarts_server"])
        apply.assert_not_called()
        systemctl.assert_not_called()
