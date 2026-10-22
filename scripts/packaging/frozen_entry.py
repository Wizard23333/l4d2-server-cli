"""Linux directory bundle entry; system tools retain the user's library path."""
import os
import sys


def restore_external_library_path(environment):
    # PyInstaller prepends its own libraries. All child programs used by this
    # CLI are external system/Steam tools. The internal probe worker branches
    # before this restoration and keeps the bundle's library search path.
    original = environment.pop("LD_LIBRARY_PATH_ORIG", None)
    if original is None:
        environment.pop("LD_LIBRARY_PATH", None)
    else:
        environment["LD_LIBRARY_PATH"] = original


def main(argv=None):
    from l4d2_cli.network_probe import WORKER_FLAG, main as probe_main
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == WORKER_FLAG:
        return probe_main(arguments[1:])
    if getattr(sys, "frozen", False) and sys.platform.startswith("linux"):
        restore_external_library_path(os.environ)
    from l4d2_cli.cli import main as cli_main
    return cli_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
