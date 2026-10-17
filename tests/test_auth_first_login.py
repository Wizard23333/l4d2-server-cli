"""Full clean-cache CLI authentication under a real controlling PTY.

The tool fixture is a simulator, not Valve SteamCMD. No real Steam credentials.
"""
from __future__ import annotations
import json,os,select,shlex,signal,sys,time,unittest
from pathlib import Path
from unittest.mock import patch
from test_install_recovery import ROOT,directory
from l4d2_cli import auth,host,steam
from l4d2_cli.common import CliError
from l4d2_cli.model import Source


def tty_run(args, responses=(), timeout=12):
    import pty
    pid, fd = pty.fork()
    if pid == 0:
        os.execv(args[0], args)
    data = bytearray()
    pending = list(responses)
    status = None
    try:
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            ready,_,_ = select.select([fd],[],[],.1)
            if ready:
                try: block = os.read(fd,4096)
                except OSError: break
                if not block: break
                data.extend(block)
                if pending and pending[0][0] in data:
                    _, answer = pending.pop(0)
                    os.write(fd, answer + b"\n")
            done, result = os.waitpid(pid,os.WNOHANG)
            if done:
                status = result
                break
        if status is None:
            done, result = os.waitpid(pid,os.WNOHANG)
            if done: status = result
        if status is None:
            os.kill(pid,signal.SIGINT)
            until = time.monotonic()+6
            while time.monotonic()<until:
                done,result=os.waitpid(pid,os.WNOHANG)
                if done:
                    status=result
                    break
                time.sleep(.05)
            if status is None:
                os.kill(pid,signal.SIGKILL)
                _,status=os.waitpid(pid,0)
                raise AssertionError("PTY test exceeded deadline")
        return (os.WEXITSTATUS(status) if os.WIFEXITED(status) else -os.WTERMSIG(status)),bytes(data)
    finally:
        os.close(fd)


FAKE = r"""import getpass,os,pathlib,sys
root=pathlib.Path(__file__).resolve().parent.parent
config=root/"Steam/config/config.vdf"
name=sys.argv[sys.argv.index("+login")+1]
headless="+@NoPromptForPassword" in sys.argv and sys.argv[sys.argv.index("+@NoPromptForPassword")+1]=="1"
if headless:
    if not config.is_file() or config.read_text()!="SYNTHETIC_LOGIN_TOKEN":
        print("Cached credentials expired")
        raise SystemExit(0)
else:
    fd=os.open("/dev/tty",os.O_RDWR)
    try:
        assert os.tcgetpgrp(fd)==os.getpgrp(),"Native child lost foreground terminal"
    finally:
        os.close(fd)
    if config.exists():
        print("UNEXPECTED_IMPORTED_LOGIN")
        raise SystemExit(9)
    print("CONTROLLING_TTY_OK",flush=True)
    password=getpass.getpass("Native password: ")
    if password!="SYNTHETIC_PASSWORD":
        print("Invalid password")
        raise SystemExit(7)
    guard=getpass.getpass("Native Steam Guard: ")
    if guard!="SYNTHETIC_GUARD":
        print("Steam Guard rejected")
        raise SystemExit(8)
    config.parent.mkdir(parents=True,exist_ok=True)
    config.write_text("SYNTHETIC_LOGIN_TOKEN")
    config.chmod(0o600)
# Native SteamCMD also writes colors when stdout is captured for verification.
print("\x1b[0mLogging in user TEST to Steam Public...\x1b[0mOK")
print("\x1b[0mWaiting for user info...\x1b[0mOK")
"""


@unittest.skipUnless(os.name=="posix","Linux authentication integration")
class FirstLoginTests(unittest.TestCase):
    def fixture(self, root):
        source=root/"source";(source/"linux32").mkdir(parents=True)
        (source/"linux32/steamcmd").write_bytes(b"\x7fELFfixture")
        (source/"public").mkdir()
        (source/"public/login.py").write_text(FAKE)
        launch=source/"steamcmd.sh"
        launch.write_text('#!/bin/sh\nexec '+shlex.quote(sys.executable)+' "$(dirname "$0")/public/login.py" "$@"\n')
        launch.chmod(0o700)
        (source/"Steam/config").mkdir(parents=True)
        (source/"Steam/config/config.vdf").write_text("IMPORTED_CANARY")
        (source/"config").mkdir()
        (source/"config/config.vdf").write_text("IMPORTED_CANARY")
        return launch

    def invoke(self, source, cache, password=b"SYNTHETIC_PASSWORD", guard=b"SYNTHETIC_GUARD"):
        # A game-dependency query would fail this driver. Login must not need it.
        driver=(
            "import sys; sys.path.insert(0,sys.argv.pop(1)); "
            "from l4d2_cli import cli,host; "
            "host.missing_dependencies=lambda: (_ for _ in ()).throw(AssertionError('game dependency gate')); "
            "raise SystemExit(cli.main())"
        )
        args=[sys.executable,"-c",driver,str(ROOT/"src"),"auth","login",
              "--fresh","--steamcmd-path",str(source),"--cache",str(cache),"--timeout","8"]
        return tty_run(args,[(b"Steam account name:",b"SYNTHETIC_USER"),
                              (b"Native password:",password),(b"Native Steam Guard:",guard)])

    def test_zero_cache_account_password_guard_then_headless_reuse_and_clone(self):
        with directory() as folder:
            root=Path(folder);source=self.fixture(root);cache=root/"new-account"
            self.assertFalse(cache.exists())
            code,text=self.invoke(source,cache)
            self.assertEqual(code,0,text.decode(errors="replace"))
            self.assertIn(b"CONTROLLING_TTY_OK",text)
            self.assertIn(b'"cached_login_verified": true',text)
            self.assertIn(b'"started_with_empty_cache": true',text)
            self.assertIn(b'"login_cache_import_allowed": false',text)
            self.assertNotIn(b"SYNTHETIC_PASSWORD",text)
            self.assertNotIn(b"SYNTHETIC_GUARD",text)
            self.assertFalse((cache/"steamcmd/config").exists())
            self.assertEqual((cache/"steamcmd/Steam/config/config.vdf").stat().st_mode&0o777,0o600)
            self.assertEqual((source.parent/"Steam/config/config.vdf").read_text(),"IMPORTED_CANARY")
            tool=steam.prepare_tool(Source(steamcmd_path=str(cache/"steamcmd/steamcmd.sh")),root/"clone",10)
            env=os.environ.copy();env["HOME"]=str(tool.parent)
            auth.verify_cached_login(tool,"SYNTHETIC_USER",env,5,False)
            (tool.parent/"Steam/config/config.vdf").write_text("EXPIRED")
            with self.assertRaises(CliError) as caught:
                auth.verify_cached_login(tool,"SYNTHETIC_USER",env,5,False)
            self.assertEqual(caught.exception.code,"steam_auth_unverified")

    def test_wrong_first_password_never_reports_verified_cache(self):
        with directory() as folder:
            root=Path(folder);cache=root/"new-account"
            code,text=self.invoke(self.fixture(root),cache,password=b"INCORRECT_SYNTHETIC_INPUT")
            self.assertNotEqual(code,0)
            self.assertNotIn(b'"cached_login_verified": true',text)
            self.assertFalse((cache/"steamcmd/Steam/config/config.vdf").exists())
            self.assertNotIn(b"INCORRECT_SYNTHETIC_INPUT",text)
            self.assertIn(b"Invalid password",text)

    def test_wrong_steam_guard_never_reports_verified_cache(self):
        with directory() as folder:
            root=Path(folder);cache=root/"new-account"
            code,text=self.invoke(self.fixture(root),cache,guard=b"INCORRECT_SYNTHETIC_GUARD")
            self.assertIn(b"Steam Guard rejected",text)
            self.assertNotEqual(code,0)
            self.assertNotIn(b'"cached_login_verified": true',text)
            self.assertFalse((cache/"steamcmd/Steam/config/config.vdf").exists())

    def test_fresh_does_not_modify_an_existing_account_cache(self):
        with directory() as folder:
            cache=Path(folder)/"existing";cache.mkdir(mode=0o755)
            file=cache/"keep";file.write_text("SYNTHETIC_PRIVATE_STATE");file.chmod(0o644)
            with patch.object(auth.sys.stdin,"isatty",return_value=True), \
                    patch.object(auth.sys.stdout,"isatty",return_value=True), \
                    patch.object(auth.sys.stderr,"isatty",return_value=True):
                with self.assertRaises(CliError) as caught:
                    auth.login(Source(),"SYNTHETIC_USER",str(cache),5,fresh=True)
            self.assertEqual(caught.exception.code,"fresh_cache_required")
            self.assertEqual(file.stat().st_mode&0o777,0o644)
            self.assertEqual(file.read_text(),"SYNTHETIC_PRIVATE_STATE")

    def test_fresh_tool_clone_excludes_both_login_layouts(self):
        with directory() as folder:
            root=Path(folder);source=self.fixture(root)
            tool=steam.prepare_tool(Source(steamcmd_path=str(source)),root/"clean",5,include_login_cache=False)
            self.assertFalse((tool.parent/"Steam").exists())
            self.assertFalse((tool.parent/"config").exists())

    def test_fresh_rejects_a_bootstrap_archive_with_login_files(self):
        import io,tarfile
        with directory() as folder:
            root=Path(folder);archive=root/"bootstrap.tar.gz"
            with tarfile.open(archive,"w:gz") as out:
                for name,data in (("steamcmd.sh",b"#!/bin/sh\n"),("linux32/steamcmd",b"\x7fELFfixture"),
                                  ("Steam/config/config.vdf",b"IMPORTED_CANARY")):
                    item=tarfile.TarInfo(name);item.size=len(data);out.addfile(item,io.BytesIO(data))
            with self.assertRaises(CliError) as caught:
                steam.prepare_tool(Source(steamcmd_archive=str(archive)),root/"clean",5,include_login_cache=False)
            self.assertEqual(caught.exception.code,"fresh_source_contains_login")
            self.assertFalse((root/"clean/Steam/config/config.vdf").exists())
