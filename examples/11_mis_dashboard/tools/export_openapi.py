#!/usr/bin/env python3
"""Generate or verify the checked-in FastAPI OpenAPI contract.

The application factory is inspected without entering its lifespan, so this
command does not connect to Netezza or load the data cache.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = EXAMPLE_ROOT / "docs" / "contracts" / "openapi.json"
if str(EXAMPLE_ROOT) not in sys.path:
    sys.path.insert(0, str(EXAMPLE_ROOT))


def build_openapi() -> dict[str, Any]:
    from main import create_app

    return create_app().openapi()


def serialise(specification: dict[str, Any]) -> str:
    return json.dumps(
        specification,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="OpenAPI JSON path (default: docs/contracts/openapi.json)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail when the generated document differs from the checked-in file",
    )
    args = parser.parse_args(argv)
    expected = serialise(build_openapi())

    if args.check:
        if not args.output.is_file():
            print(f"Missing OpenAPI contract: {args.output}", file=sys.stderr)
            return 1
        actual = args.output.read_text(encoding="utf-8")
        if actual != expected:
            print(
                "OpenAPI contract is out of date. Run "
                "python tools/export_openapi.py.",
                file=sys.stderr,
            )
            return 1
        print(f"OpenAPI contract is current: {args.output}")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(expected, encoding="utf-8")
    print(f"Wrote OpenAPI contract: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
