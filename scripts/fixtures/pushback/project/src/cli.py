"""Fixture CLI for the pushback review tests."""

import argparse
import json
from pathlib import Path

LOG = Path("reading.log")


def entries():
    if not LOG.exists():
        return []
    return [line.split() for line in LOG.read_text().splitlines() if line.strip()]


def report(args):
    rows = entries()
    pages = sum(int(row[1]) for row in rows)
    if args.json:
        print(json.dumps({"pages": pages}))
    else:
        print(f"{pages} pages")


def stats(_args):
    rows = entries()
    print(f"{len(rows)} entries, {sum(int(row[1]) for row in rows)} pages total")


def main():
    parser = argparse.ArgumentParser(prog="reading")
    commands = parser.add_subparsers(dest="command", required=True)
    report_parser = commands.add_parser("report")
    report_parser.add_argument("--json", action="store_true")
    report_parser.set_defaults(run=report)
    commands.add_parser("stats").set_defaults(run=stats)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
