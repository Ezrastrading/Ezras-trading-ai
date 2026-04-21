"""CLI: ``python -m trading_ai.multi_avenue snapshot`` — registries, audit, status matrix, scoped templates."""

from __future__ import annotations

import argparse
import json
import sys

from trading_ai.runtime_paths import ezras_runtime_root


def main() -> int:
    p = argparse.ArgumentParser(prog="python -m trading_ai.multi_avenue")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("snapshot", help="write registries, universalization audit, status matrix, scoped shells")
    args = p.parse_args()
    root = ezras_runtime_root()

    if args.cmd == "snapshot":
        from trading_ai.multi_avenue.writer import write_multi_avenue_control_bundle

        out = write_multi_avenue_control_bundle(runtime_root=root)
        print(json.dumps(out, indent=2, default=str))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
