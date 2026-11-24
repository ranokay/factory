from __future__ import annotations

import argparse
import json

from report_flow.core import load, send, totals


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarise a review report.")
    parser.add_argument("report")
    parser.add_argument("--channel", default="")
    args = parser.parse_args()
    summary = totals(load(args.report))
    if args.channel:
        send(args.channel, summary)
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
