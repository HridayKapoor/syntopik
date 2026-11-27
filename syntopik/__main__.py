"""Allow ``python -m syntopik`` when the ``syntopik`` script is not on PATH."""

import sys

from syntopik.cli import main

if __name__ == "__main__":
    sys.exit(main())
