"""Linux directory bundle entry; system tools retain the user's library path."""
import os
import sys


def restore_external_library_path(environment):
    # PyInstaller prepends its own libraries. All child programs used by this
    # CLI are external system/Steam tools, not frozen helper executables.
    original = environment.pop("LD_LIBRARY_PATH_ORIG", None)
    if original is None:
        environment.pop("LD_LIBRARY_PATH", None)
    else:
        environment["LD_LIBRARY_PATH"] = original


if __name__ == "__main__":
    from l4d2_cli.cli import main
    if getattr(sys, "frozen", False) and sys.platform.startswith("linux"):
        restore_external_library_path(os.environ)
    raise SystemExit(main())
