from __future__ import annotations

import argparse
import json

from .flow import load_findings, summarise


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarise a review report.")
    parser.add_argument("report")
    args = parser.parse_args()
    findings = load_findings(args.report)
    print(json.dumps(summarise(findings)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
