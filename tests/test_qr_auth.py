"""QR provider regressions. Synthetic helper is explicitly not Steam/DepotDownloader."""
from __future__ import annotations
import io,json,os,sys,tarfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
from test_install_recovery import ROOT,directory
from test_auth_first_login import tty_run
from l4d2_cli import auth,cli,depot,packages
from l4d2_cli.common import CliError

SIMULATOR=r"""#!/usr/bin/python3
import os,pathlib,sys
a=sys.argv[1:]
target=pathlib.Path(a[a.index("-dir")+1])
token=pathlib.Path(os.environ["XDG_DATA_HOME"])/"IsolatedStorage/account.config"
if "-qr" in a:
    assert "-username" not in a
    print("SYNTHETIC_QR_CHALLENGE",flush=True)
    if input()!="APPROVE":
        raise SystemExit(4)
    token.parent.mkdir(parents=True,exist_ok=True)
    token.write_text("SYNTHETIC_QR_TOKEN")
    token.chmod(0o600)
elif not token.is_file() or token.read_text()!="SYNTHETIC_QR_TOKEN":
    print("Access token was rejected (Expired).")
    raise SystemExit(5)
target.mkdir(parents=True,exist_ok=True)
if "-manifest-only" in a:
    (target/"manifest_222863_123.txt").write_text("SYNTHETIC Linux manifest")
else:
    (target/"srcds_run").write_text("#!/bin/sh\n")
    (target/"srcds_run").chmod(0o700)
    elf=bytearray(20);elf[:6]=b"\x7fELF\x01\x01";elf[18:20]=(3).to_bytes(2,"little")
    (target/"srcds_linux").write_bytes(elf)
    (target/"srcds_linux").chmod(0o700)
    (target/"bin").mkdir(exist_ok=True)
    (target/"left4dead2/maps").mkdir(parents=True,exist_ok=True)
    (target/"left4dead2/maps/c1m1_hotel.bsp").write_bytes(b"SYNTHETIC_RESOURCE")
    (target/"left4dead2/pak01_dir.vpk").write_bytes(b"SYNTHETIC_RESOURCE")
    (target/".DepotDownloader").mkdir(exist_ok=True)
    (target/".DepotDownloader/account.config").write_text("SYNTHETIC_QR_TOKEN")
print("SYNTHETIC helper completed.")
"""

class QrCommandTests(unittest.TestCase):
    def test_password_remains_default_and_explicit_option(self):
        for options in ([],["--method","password"]):
            args=cli.parser().parse_args(["auth","login","--steam-user","SYNTHETIC_USER",*options])
            self.assertEqual(args.method,"password")
            with patch.object(auth,"login",return_value={"provider":"SteamCMD"}) as password, \
                    patch.object(depot,"login") as qr:
                self.assertEqual(cli.dispatch(args)["provider"],"SteamCMD")
            password.assert_called_once()
            qr.assert_not_called()

    def test_qr_is_separate_and_rejects_mixed_steamcmd_flags(self):
        args=cli.parser().parse_args(["auth","login","--method","qr","--steam-user","SYNTHETIC_USER",
                                    "--depotdownloader-path","/trusted/helper","--cache","/private/qr"])
        with patch.object(depot,"login",return_value={}) as qr,patch.object(auth,"login") as password:
            cli.dispatch(args)
        qr.assert_called_once()
        password.assert_not_called()
        bad=cli.parser().parse_args(["auth","login","--method","qr","--steamcmd-path","/other/steamcmd.sh"])
        with self.assertRaises(CliError):
            cli.dispatch(bad)
        with self.assertRaises(CliError):
            cli.parser().parse_args(["download","server","--output","x.tar.gz","--qr-cache","/qr",
                                    "--steamcmd-path","/other/steamcmd.sh"])

    def test_manifest_arguments_never_mix_qr_username_or_password(self):
        qr=depot.manifest_arguments(Path("/tool"),Path("/new-probe"),qr=True)
        cached=depot.manifest_arguments(Path("/tool"),Path("/new-probe"),username="SYNTHETIC_USER")
        self.assertIn("-qr",qr)
        self.assertNotIn("-username",qr)
        self.assertNotIn("-password",qr+cached)
        self.assertNotIn("-qr",cached)
        self.assertIn("-manifest-only",qr)
        self.assertEqual(qr[qr.index("-depot")+1],"222863")
        self.assertGreater(int(qr[qr.index("-loginid")+1]),0)

    def test_zero_exit_without_fresh_manifest_is_not_success(self):
        with directory() as temporary:
            with self.assertRaises(CliError) as caught:
                depot.verify_manifest(Path(temporary))
        self.assertEqual(caught.exception.code,"qr_manifest_unverified")

    def test_qr_secrets_and_depot_metadata_are_not_bundle_payload(self):
        for path in (".DepotDownloader/account.config","account.config",".l4d2-qr-auth.json",
                     ".qr-content.json","data/IsolatedStorage/ssfn123"):
            self.assertTrue(packages.private_payload(path),path)

    def test_non_elf_helper_is_rejected(self):
        with directory() as temporary:
            p=Path(temporary)/"DepotDownloader";p.write_bytes(b"not an ELF")
            with self.assertRaises(CliError) as caught:
                depot.validate_helper(p)
        self.assertEqual(caught.exception.code,"invalid_qr_helper")

    def test_qr_download_dispatch_uses_only_verified_cache(self):
        args=cli.parser().parse_args(["download","server","--qr-cache","/private/qr",
                                    "--output","/tmp/qr-test.tar.gz","--yes"])
        with patch.object(cli.host,"require_platform"),patch.object(cli.storage,"reject_links"), \
                patch.object(cli.Path,"exists",return_value=False), \
                patch.object(depot,"prepare_bundle",return_value={"provider":"DepotDownloader"}) as prepare, \
                patch.object(cli.network,"check") as network:
            data=cli.dispatch(args)
        self.assertEqual(data["provider"],"DepotDownloader")
        prepare.assert_called_once()
        network.assert_not_called()


@unittest.skipUnless(os.name=="posix","Linux QR provider integration")
class QrIntegrationTests(unittest.TestCase):
    def driver(self):
        # Only the synthetic executable validation and disk budget are overridden.
        return ("import sys;sys.path.insert(0,sys.argv.pop(1));"
                "from l4d2_cli import cli,depot;"
                "depot.validate_helper=lambda path:None;"
                "depot.shutil.disk_usage=lambda path:type('Disk',(),{'free':40*1024**3})();"
                "raise SystemExit(cli.main())")

    def login(self, root):
        source=root/"helper";source.write_text(SIMULATOR);source.chmod(0o700)
        cache=root/"qr-cache"
        args=[sys.executable,"-c",self.driver(),str(ROOT/"src"),"auth","login","--method","qr",
              "--steam-user","SYNTHETIC_USER","--depotdownloader-path",str(source),
              "--fresh","--cache",str(cache),"--timeout","8"]
        code,text=tty_run(args,[(b"SYNTHETIC_QR_CHALLENGE",b"APPROVE")])
        self.assertEqual(code,0,text.decode(errors="replace"))
        self.assertIn(b'"cached_login_verified": true',text)
        self.assertIn(b'"steamcmd_cache_compatible": false',text)
        return cache

    def test_qr_approval_cache_verification_bundle_and_expiration(self):
        with directory() as temporary:
            root=Path(temporary);cache=self.login(root)
            token=cache/"data/IsolatedStorage/account.config"
            self.assertEqual(token.stat().st_mode&0o777,0o600)
            self.assertEqual(cache.stat().st_mode&0o777,0o700)
            self.assertFalse((cache/"tool/config").exists())
            output=root/"server.tar.gz"
            args=[sys.executable,"-c",self.driver(),str(ROOT/"src"),"download","server",
                  "--qr-cache",str(cache),"--output",str(output),"--yes"]
            code,text=tty_run(args)
            self.assertEqual(code,0,text.decode(errors="replace"))
            bundle=packages.validate_bundle(output)
            self.assertIn("DepotDownloader",bundle["manifest"]["source"])
            self.assertNotIn("SteamCMD",bundle["manifest"]["source"])
            with tarfile.open(output,"r:gz") as archive:
                names=archive.getnames()
                self.assertFalse(any(".DepotDownloader" in n or "account.config" in n for n in names))
                self.assertFalse(any(b"SYNTHETIC_QR_TOKEN" in archive.extractfile(m).read()
                                     for m in archive.getmembers() if m.isfile()))
            token.write_text("EXPIRED")
            args[args.index("--output")+1]=str(root/"second.tar.gz")
            code,text=tty_run(args)
            self.assertNotEqual(code,0)
            self.assertIn(b"qr_download_failed",text)
            self.assertTrue((cache/"content/srcds_linux").exists())

    def test_password_cache_is_not_accepted_as_a_qr_cache(self):
        with directory() as temporary:
            root=Path(temporary)
            (root/".l4d2-auth.json").write_text('{"schema_version":1,"purpose":"steamcmd_auth"}')
            with self.assertRaises(CliError) as caught:
                depot.read_state(root)
            self.assertEqual(caught.exception.code,"auth_cache_conflict")

    def test_fresh_qr_does_not_modify_an_existing_cache(self):
        with directory() as temporary:
            root=Path(temporary);file=root/"keep";file.write_text("KEEP");file.chmod(0o644)
            with patch.object(depot,"require_context"):
                with self.assertRaises(CliError) as caught:
                    depot.login(None,str(root),3,fresh=True)
            self.assertEqual(caught.exception.code,"fresh_cache_required")
            self.assertEqual(file.stat().st_mode&0o777,0o644)
