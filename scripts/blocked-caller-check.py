#!/usr/bin/env python3
"""Fail when the Project Template's Blocker caller drifts from the Factory's."""

import argparse
import json
import subprocess  # nosec B404 -- yq reads the workflow files under check
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from profiles_reader import BLOCKER_CALLER, BLOCKER_LOCAL, BLOCKER_WORKFLOW, TAG

FACTORY = ROOT / BLOCKER_CALLER
TEMPLATE = ROOT / "project" / BLOCKER_CALLER
LOCAL_USES = BLOCKER_LOCAL
PINNED_PREFIX = BLOCKER_WORKFLOW + "@"


class CheckError(Exception):
    pass


def pinned(uses):
    return uses.startswith(PINNED_PREFIX) and TAG.match(uses[len(PINNED_PREFIX):]) is not None


def load(path):
    path = Path(path)
    if not path.is_file():
        raise CheckError(f"{path}: is missing")
    result = subprocess.run(  # nosec B603 B607  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit -- yq reads the workflow file under check
        ["yq", "-o=json", ".", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise CheckError(f"{path}: {result.stderr.strip() or 'could not be read'}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise CheckError(f"{path}: {error}") from error


def uses_values(workflow, path):
    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        raise CheckError(f"{path}: has no jobs")
    values = []
    for name, job in jobs.items():
        uses = job.get("uses") if isinstance(job, dict) else None
        if not isinstance(uses, str):
            raise CheckError(f"{path}: job {name} has no reusable workflow call")
        values.append((name, uses))
    return values


def check(factory_path, template_path):
    factory = load(factory_path)
    template = load(template_path)
    for name, uses in uses_values(factory, factory_path):
        if uses != LOCAL_USES:
            raise CheckError(
                f"{factory_path}: job {name} must call the local reusable workflow {LOCAL_USES}"
            )
    for name, uses in uses_values(template, template_path):
        if not pinned(uses):
            raise CheckError(
                f"{template_path}: job {name} must call {BLOCKER_WORKFLOW}@vX.Y.Z"
            )
    normalized = []
    for workflow in (factory, template):
        for job in workflow["jobs"].values():
            job["uses"] = "<caller>"
        normalized.append(workflow)
    if normalized[0] != normalized[1]:
        raise CheckError(
            f"{template_path}: does not match {factory_path} outside the uses ref"
        )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Check the Project Template's Blocker caller against the Factory's."
    )
    parser.add_argument("--factory", type=Path, default=FACTORY)
    parser.add_argument("--template", type=Path, default=TEMPLATE)
    args = parser.parse_args(argv)
    try:
        check(args.factory, args.template)
    except CheckError as error:
        print(f"blocked-caller: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
