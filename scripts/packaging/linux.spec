# Only fixed runtime libraries may be distributed; host preloads are excluded.
from pathlib import Path
import sys
import runpy

root = Path(SPECPATH).resolve().parents[1]
runtime = Path(sys.base_prefix).resolve()
a = Analysis(
    [str(root / "scripts/packaging/frozen_entry.py")],
    pathex=[str(root / "src")],
    binaries=[],
    datas=[(str(root / "src/l4d2_cli/templates"), "l4d2_cli/templates"),
           (str(root / "src/l4d2_cli/skills"), "l4d2_cli/skills"),
           (str(root / "src/l4d2_cli/ops/assets"), "l4d2_cli/ops/assets"),
           (str(root / "src/l4d2_cli/web/static"), "l4d2_cli/web/static"),
           (str(root / "src/l4d2_cli/web/licenses"), "l4d2_cli/web/licenses"),
           (str(root / "src/l4d2_cli/_vendor/tomli/LICENSE"), "l4d2_cli/_vendor/tomli"),
           (str(root / "src/l4d2_cli/_vendor/tomli/SOURCE.json"), "l4d2_cli/_vendor/tomli")],
    hiddenimports=[], hookspath=[], runtime_hooks=[],
    excludes=["tkinter", "unittest", "pip"], noarchive=False)
policy = runpy.run_path(str(root / "scripts/packaging/build_linux.py"))["runtime_libraries"]
accepted = policy(a.binaries, runtime)
if len(accepted) != len(a.binaries):
    print("Excluded %d binary inputs outside the pinned Python runtime." % (len(a.binaries) - len(accepted)))
a.binaries = accepted
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="l4d2", debug=False,
          bootloader_ignore_signals=False, strip=True, upx=False, console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=True, upx=False, name="l4d2")
