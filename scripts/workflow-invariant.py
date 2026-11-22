import json
import re
import subprocess
import sys
from pathlib import Path


SECRET = re.compile(r"\bsecrets\b", re.IGNORECASE)


def triggers(workflow):
    on = workflow.get("on")
    if isinstance(on, dict):
        return set(on)
    if isinstance(on, str):
        return {on}
    return set(on or [])


def main(directory, config_path):
    authors = json.loads(Path(config_path).read_text())["factory_authors"]
    if not authors or any(not re.fullmatch(r"[A-Za-z0-9-]+(?:\[bot\])?", name) for name in authors):
        raise ValueError("invalid factory_authors")
    author_guard = " || ".join(f"github.event.pull_request.user.login == '{name}'" for name in authors)
    guard_required = f"github.event.pull_request.head.repo.full_name == github.repository && ({author_guard})"
    run_guard = "github.event.workflow_run.head_repository.full_name == github.repository"
    failures = []
    paths = sorted([*Path(directory).glob("*.yml"), *Path(directory).glob("*.yaml")])
    if not paths:
        raise ValueError("no workflows found")
    for path in paths:
        result = subprocess.run(["yq", "-o=json", ".", str(path)], capture_output=True, text=True, check=True)
        workflow = json.loads(result.stdout)
        names = triggers(workflow)
        if not names:
            failures.append(f"{path}: no workflow trigger")
            continue
        target = "pull_request_target" in names
        if target:
            failures.append(f"{path}: pull_request_target is not permitted")
        if SECRET.search(json.dumps(workflow.get("env", {}))):
            failures.append(f"{path}: workflow-level environment references secrets")
        if workflow.get("name") == "AI Review":
            guards = [str(job.get("if", "")).strip().removeprefix("${{").removesuffix("}}").strip() for job in workflow.get("jobs", {}).values()]
            guards = [guard for guard in guards if "pull_request.user.login" in guard]
            if not guards:
                failures.append(f"{path}: AI Review job has no author guard")
            for guard in guards:
                if guard != guard_required:
                    failures.append(f"{path}: AI Review author guard does not match factory_authors")
        if not {"pull_request", "pull_request_target", "workflow_run"} & names:
            continue
        expected = guard_required if names & {"pull_request", "pull_request_target"} else run_guard
        for name, job in workflow.get("jobs", {}).items():
            guard = str(job.get("if", "")).strip().removeprefix("${{").removesuffix("}}").strip()
            secret = SECRET.search(json.dumps(job))
            if secret and guard != expected:
                failures.append(f"{path}: {name} has secrets without a same-repository guard")
    if failures:
        raise ValueError("\n".join(failures))


if __name__ == "__main__":
    try:
        main(sys.argv[1] if len(sys.argv) > 1 else ".github/workflows", sys.argv[2] if len(sys.argv) > 2 else Path(__file__).resolve().parent.parent / "repo-settings.json")
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(error, file=sys.stderr)
        sys.exit(1)
