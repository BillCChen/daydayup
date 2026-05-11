#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
macOS app launcher for the local web console.
"""

from __future__ import annotations

import multiprocessing
import sys


def main() -> int:
    multiprocessing.freeze_support()
    if len(sys.argv) > 1 and sys.argv[1] == "--booking-worker":
        import enhanced_book_smart_v2

        sys.argv = ["enhanced_book_smart_v2.py", *sys.argv[2:]]
        enhanced_book_smart_v2.main()
        return 0

    import web_console

    if "--open-browser" not in sys.argv:
        sys.argv.append("--open-browser")
    return web_console.main()


if __name__ == "__main__":
    raise SystemExit(main())
