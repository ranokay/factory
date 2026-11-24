from __future__ import annotations

import argparse
import json

from .flow import load_findings, notify, summarise


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarise a review report.")
    parser.add_argument("report")
    parser.add_argument("--notify-channel", default="")
    args = parser.parse_args()
    findings = load_findings(args.report)
    if args.notify_channel:
        notify(args.notify_channel, json.dumps(summarise(findings)))
    print(json.dumps(summarise(findings)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
