#!/usr/bin/env python3
"""Run the full C-CDA + claims pipeline. See README.md for options."""
import sys

from pipeline.cli import main

if __name__ == "__main__":
    sys.exit(main())
