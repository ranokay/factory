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


def condition(value):
    return str(value or "").strip().removeprefix("${{").removesuffix("}}").strip()


def job_name(job_id, job):
    return job.get("name", job_id)


def main(directory, config_path):
    config = json.loads(Path(config_path).read_text())
    authors = config["factory_authors"]
    if not authors or any(not re.fullmatch(r"[A-Za-z0-9-]+(?:\[bot\])?", name) for name in authors):
        raise ValueError("invalid factory_authors")
    author_guard = " || ".join(f"github.event.pull_request.user.login == '{name}'" for name in authors)
    guard_required = f"github.event.pull_request.head.repo.full_name == github.repository && ({author_guard})"
    run_guard = "github.event.workflow_run.head_repository.full_name == github.repository"
    required = {check["context"] for check in config["required_checks"]}
    root = Path(directory)
    cache = {}

    def load(path):
        if path not in cache:
            result = subprocess.run(["yq", "-o=json", ".", str(path)], capture_output=True, text=True, check=True)
            cache[path] = json.loads(result.stdout)
        return cache[path]

    def called_jobs(caller, job):
        uses = str(job.get("uses") or "")
        if not (uses.startswith("$/") or uses.startswith("./")):
            return {}
        relative = Path(uses[2:])
        if relative.parts[:2] != (".github", "workflows") or len(relative.parts) != 3:
            raise ValueError(f"{caller}: {uses} is not a workflow directly in .github/workflows")
        path = root / relative.name
        if not path.is_file():
            raise ValueError(f"{caller}: {uses} does not exist")
        called = load(path)
        if "workflow_call" not in triggers(called):
            raise ValueError(f"{caller}: {uses} is not a reusable workflow")
        return called.get("jobs", {})

    def called_guarded(caller, job, guard):
        callees = called_jobs(caller, job)
        return bool(callees) and all(condition(called.get("if")) == guard for called in callees.values())

    failures = []
    paths = sorted([*root.glob("*.yml"), *root.glob("*.yaml")])
    if not paths:
        raise ValueError("no workflows found")
    for path in paths:
        workflow = load(path)
        names = triggers(workflow)
        if not names:
            failures.append(f"{path}: no workflow trigger")
            continue
        if "pull_request_target" in names:
            failures.append(f"{path}: pull_request_target is not permitted")
        if SECRET.search(json.dumps(workflow.get("env", {}))):
            failures.append(f"{path}: workflow-level environment references secrets")
        jobs = workflow.get("jobs", {})
        if workflow.get("name") == "AI Review":
            guards = [condition(job.get("if")) for job in jobs.values()]
            for job in jobs.values():
                guards.extend(condition(called.get("if")) for called in called_jobs(path, job).values())
            guards = [guard for guard in guards if "pull_request.user.login" in guard]
            if not guards:
                failures.append(f"{path}: AI Review job has no author guard")
            for guard in guards:
                if guard != guard_required:
                    failures.append(f"{path}: AI Review author guard does not match factory_authors")
        for job_id, job in jobs.items():
            callees = called_jobs(path, job)
            for called_id, called in callees.items():
                context = f"{job_name(job_id, job)} / {job_name(called_id, called)}"
                if context in required and condition(job.get("if")):
                    failures.append(f"{path}: {job_id} guards required check {context} at the caller")
            if not callees and "uses" in job and condition(job.get("if")):
                failures.append(f"{path}: {job_id} guards an external reusable workflow at the caller")
        if not {"pull_request", "pull_request_target", "workflow_run"} & names:
            continue
        expected = guard_required if names & {"pull_request", "pull_request_target"} else run_guard
        for name, job in jobs.items():
            guard = condition(job.get("if"))
            if not SECRET.search(json.dumps(job)) or guard == expected or called_guarded(path, job, expected):
                continue
            failures.append(f"{path}: {name} has secrets without a same-repository guard")
    if failures:
        raise ValueError("\n".join(failures))


if __name__ == "__main__":
    try:
        main(sys.argv[1] if len(sys.argv) > 1 else ".github/workflows", sys.argv[2] if len(sys.argv) > 2 else Path(__file__).resolve().parent.parent / "repo-settings.json")
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(error, file=sys.stderr)
        sys.exit(1)
