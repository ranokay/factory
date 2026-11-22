import json
import re
import subprocess
import sys
from pathlib import Path


GUARD = "github.event.pull_request.head.repo.full_name == github.repository && (github.event.pull_request.user.login == 'ranokay' || github.event.pull_request.user.login == 'renovate[bot]')"
SECRET = re.compile(r"\bsecrets\b", re.IGNORECASE)


def main(directory):
    failures = []
    paths = sorted([*Path(directory).glob("*.yml"), *Path(directory).glob("*.yaml")])
    if not paths:
        raise ValueError("no workflows found")
    for path in paths:
        result = subprocess.run(["yq", "-o=json", ".", str(path)], capture_output=True, text=True, check=True)
        workflow = json.loads(result.stdout)
        triggers = workflow.get("on", {})
        target = "pull_request_target" in triggers if isinstance(triggers, (dict, list, str)) else False
        if target:
            failures.append(f"{path}: pull_request_target is not permitted")
        if SECRET.search(json.dumps(workflow.get("env", {}))):
            failures.append(f"{path}: workflow-level environment references secrets")
        for name, job in workflow.get("jobs", {}).items():
            guard = str(job.get("if", "")).strip().removeprefix("${{").removesuffix("}}").strip()
            secret = SECRET.search(json.dumps(job)) or job.get("secrets") == "inherit"
            if secret and guard != GUARD:
                failures.append(f"{path}: {name} has secrets without a same-repository guard")
    if failures:
        raise ValueError("\n".join(failures))


if __name__ == "__main__":
    try:
        main(sys.argv[1] if len(sys.argv) > 1 else ".github/workflows")
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(error, file=sys.stderr)
        sys.exit(1)
