import sys

if sys.version_info < (3, 8):
    print("l4d2-server-cli requires Python 3.8 or newer.", file=sys.stderr)
    raise SystemExit(4)

from .cli import main

raise SystemExit(main())
