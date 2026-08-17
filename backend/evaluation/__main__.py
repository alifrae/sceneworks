"""``python -m evaluation`` entry point."""

from __future__ import annotations

import sys

from evaluation.cli import main

if __name__ == "__main__":
    sys.exit(main())
