"""Regressions for configuration failures reproduced with the real vanilla game."""
import pathlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from l4d2_cli import manager, model
from test_install_recovery import directory
from l4d2_cli.common import CliError


class VanillaConfigurationTests(unittest.TestCase):
    def test_join_password_rejected_without_disclosing_secret(self):
        with self.assertRaises(CliError) as caught:
            model.render_cfg(model.Config(game_dir=str(pathlib.Path(__file__).resolve().parents[1] / "test-game")), {"join_password": "SYNTHETIC_JOIN_SECRET"})
        self.assertEqual(caught.exception.code, "unsupported_join_password")
        self.assertIn("--clear-join-password", str(caught.exception))
        self.assertNotIn("SYNTHETIC_JOIN_SECRET", str(caught.exception))

    def test_empty_join_password_keeps_rcon_configuration(self):
        rendered = model.render_cfg(model.Config(game_dir=str(pathlib.Path(__file__).resolve().parents[1] / "test-game")), {"join_password": "", "rcon_password": "SYNTHETIC_RCON"})
        self.assertIn('sv_password ""', rendered)
        self.assertIn('rcon_password "SYNTHETIC_RCON"', rendered)

    def test_existing_join_password_stops_before_launch(self):
        with patch.object(manager.storage, "load_secrets", return_value={"join_password": "PRIVATE"}), \
                patch.object(manager, "runtime_check") as runtime, \
                patch.object(manager.host, "systemctl") as systemctl:
            with self.assertRaises(CliError) as caught:
                manager._start(model.Config(game_dir=str(pathlib.Path(__file__).resolve().parents[1] / "test-game")), {}, 0)
        self.assertEqual(caught.exception.code, "unsupported_join_password")
        runtime.assert_not_called()
        systemctl.assert_not_called()

    def test_unusable_name_and_join_password_never_write_configuration(self):
        for config, secrets, expected in [
                (model.Config(game_dir=str(pathlib.Path(__file__).resolve().parents[1] / "test-game")), {"join_password": "PRIVATE"}, "unsupported_join_password"),
                (model.Config(name="中文房间", game_dir=str(pathlib.Path(__file__).resolve().parents[1] / "test-game")), {}, "unsupported_server_name")]:
            with self.subTest(expected=expected), \
                    patch.object(manager.host, "require_platform"), \
                    patch.object(manager, "installed", return_value=(model.Config(game_dir=str(pathlib.Path(__file__).resolve().parents[1] / "test-game")), {})), \
                    patch.object(manager, "apply_configuration") as apply:
                with self.assertRaises(CliError) as caught:
                    manager.configure(config, secrets, dry_run=False, yes=True)
                self.assertEqual(caught.exception.code, expected)
                apply.assert_not_called()


class QueryAddressTests(unittest.TestCase):
    def test_explicit_bind_is_used_without_route_probe(self):
        with patch.object(manager.socket, "socket") as socket:
            self.assertEqual(manager.query_address(model.Config(bind="10.1.2.3")), "10.1.2.3")
            socket.assert_not_called()

    def test_wildcard_uses_local_interface_without_sending_probe_packet(self):
        with patch.object(manager.socket, "socket") as socket:
            probe = socket.return_value.__enter__.return_value
            probe.getsockname.return_value = ("10.1.2.3", 34567)
            self.assertEqual(manager.query_address(model.Config()), "10.1.2.3")
            probe.connect.assert_called_once_with(("192.0.2.1", 9))
            probe.send.assert_not_called()
            probe.sendto.assert_not_called()

    def test_no_route_or_unusable_source_falls_back_to_loopback(self):
        for address in ("0.0.0.0", "127.0.0.1", "not-an-ip"):
            with self.subTest(address=address), patch.object(manager.socket, "socket") as socket:
                socket.return_value.__enter__.return_value.getsockname.return_value = (address, 1)
                self.assertEqual(manager.query_address(model.Config()), "127.0.0.1")
        with patch.object(manager.socket, "socket", side_effect=OSError("no route")):
            self.assertEqual(manager.query_address(model.Config()), "127.0.0.1")
