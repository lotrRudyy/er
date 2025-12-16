#!/usr/bin/env python3
"""
Compatibility shim that forwards to piano_riddle reduce.
"""
import sys

from er1.tools.piano_riddle.cli import main


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] != "reduce":
        sys.argv.insert(1, "reduce")
    main()
