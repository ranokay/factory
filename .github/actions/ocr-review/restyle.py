#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Iterable, NamedTuple

from restyle_summary import (
    CATEGORIES,
    ROUTED_ANY,
    SEVERITY_RANK,
    SUMMARY_MARKER,
    Carried,
    Coordinates,
    FileDiff,
    RewriteError,
    badge_of,
    build_policy,
    decode_routed,
    encode_routed,
    integer,
    is_diagram_carrier,
    markers,
    normalize,
    parse_hunks,
    remap_span,
    rewrite_inline,
    rewrite_summary,
    title_of,
    validate_carried,
)

MAX_BODY = 65000
ROUTED_FULL = re.compile(r"<!--\s*ocr-routed:v1\s+\S+\s*-->")
CHECKPOINT = re.compile(r"<!--\s*ocr-checkpoint:v1\s+(\S+)\s*-->")

__all__ = [
    "decode_routed",
    "markers",
    "match_inline",
    "rewrite_inline",
    "rewrite_summary",
]


class Write(NamedTuple):
    kind: str
    identifier: Any
    original: str
    body: str


def warn(message: str) -> None:
    text = str(message).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::warning::{text}")


def load_result(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            result = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        raise RewriteError(f"cannot read {path}: {error}") from error
    validate(result)
    return result


def validate_comment(index: int, comment: Any) -> None:
    if not isinstance(comment, dict):
        raise RewriteError(f"OCR result comment {index} is not an object")
    severity = comment.get("severity")
    category = comment.get("category")
    if not isinstance(severity, str) or not normalize(severity):
        raise RewriteError(f"OCR result comment {index} has no severity")
    if normalize(severity) not in SEVERITY_RANK:
        raise RewriteError(f"OCR result comment {index} has unknown severity {severity!r}")
    if category is not None and not isinstance(category, str):
        raise RewriteError(f"OCR result comment {index} category is not a string")
    named = normalize(category)
    if named and named not in CATEGORIES:
        raise RewriteError(f"OCR result comment {index} has unknown category {category!r}")


def validate(result: Any) -> None:
    if not isinstance(result, dict):
        raise RewriteError("OCR result is not a JSON object")
    comments = result.get("comments")
    if comments is None:
        result["comments"] = []
    elif not isinstance(comments, list):
        raise RewriteError("OCR result comments is not a list")
    else:
        for index, comment in enumerate(comments):
            validate_comment(index, comment)
    for key in ("summary", "manifest"):
        value = result.get(key)
        if value is not None and not isinstance(value, dict):
            raise RewriteError(f"OCR result {key} is not an object")
    warnings = result.get("warnings")
    if warnings is not None and not isinstance(warnings, list):
        raise RewriteError("OCR result warnings is not a list")
    for key in ("message", "project_summary", "analysis", "status"):
        value = result.get(key)
        if value is not None and not isinstance(value, str):
            raise RewriteError(f"OCR result {key} is not a string")


def meta_from(result: dict[str, Any], environment: dict[str, str]) -> dict[str, str]:
    manifest = result.get("manifest") if isinstance(result.get("manifest"), dict) else {}
    source = manifest.get("input") if isinstance(manifest.get("input"), dict) else {}
    execution = (
        manifest.get("execution") if isinstance(manifest.get("execution"), dict) else {}
    )
    head = str(source.get("resolved_head") or "")
    target = str(environment.get("RANGE_TO") or "")
    return {
        "head": head or target,
        "from": str(environment.get("RANGE_FROM") or source.get("resolved_base") or ""),
        "to": target or head,
        "version": str(execution.get("ocr_version") or environment.get("VERSION") or ""),
        "model": str(execution.get("model") or environment.get("MODEL") or ""),
        "effort": str(environment.get("EFFORT") or ""),
    }


def path_matches(item: dict[str, Any], path: str) -> bool:
    item_path = str(item.get("path") or "")
    return not path or not item_path or item_path == path


def inline_match(
    comment: dict[str, Any],
    posted: list[dict[str, Any]],
    used: set[int],
) -> tuple[int, dict[str, Any]] | None:
    path = str(comment.get("path") or "")
    start = integer(comment.get("start_line"))
    end = integer(comment.get("end_line"))
    content = str(comment.get("content") or "").strip()
    expected = f"**{badge_of(comment)} · {title_of(comment)}**"
    matches: list[tuple[int, dict[str, Any]]] = []
    for position, item in enumerate(posted):
        if position in used or not path_matches(item, path):
            continue
        if content and content in str(item.get("body") or ""):
            matches.append((position, item))
    if matches:
        for position, item in matches:
            line = integer(item.get("line"))
            if line and line in (start, end):
                return position, item
        return matches[0]
    if content:
        return None
    titled = [
        (position, item)
        for position, item in enumerate(posted)
        if position not in used
        and path_matches(item, path)
        and expected in str(item.get("body") or "")
    ]
    return titled[0] if len(titled) == 1 else None


def match_inline(
    result: dict[str, Any], posted: list[dict[str, Any]]
) -> tuple[dict[int, str], list[tuple[dict[str, Any], dict[str, Any]]]]:
    links: dict[int, str] = {}
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    used: set[int] = set()
    for index, comment in enumerate(result.get("comments") or []):
        if is_diagram_carrier(comment):
            continue
        match = inline_match(comment, posted, used)
        if match is None:
            continue
        position, item = match
        links[index] = str(item.get("html_url") or "")
        pairs.append((item, comment))
        used.add(position)
    return links, pairs


class GitHub:
    def __init__(self, api_url: str, token: str) -> None:
        self.api_url = api_url.rstrip("/")
        self.token = token

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            self.api_url + path,
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "factory-ocr-restyle",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310 -- the base URL is the trusted GitHub API, overridable only by this workflow
                body = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:300]
            raise RewriteError(
                f"{method} {path} failed with HTTP {error.code}: {detail}"
            ) from error
        except urllib.error.URLError as error:
            raise RewriteError(f"{method} {path} failed: {error.reason}") from error
        if not body:
            return None
        try:
            return json.loads(body)
        except json.JSONDecodeError as error:
            raise RewriteError(f"{method} {path} returned invalid JSON") from error

    def paginate(self, path: str) -> Iterable[dict[str, Any]]:
        page = 1
        while True:
            separator = "&" if "?" in path else "?"
            batch = self.request("GET", f"{path}{separator}per_page=100&page={page}")
            if not isinstance(batch, list):
                raise RewriteError(f"GET {path} did not return a list")
            yield from batch
            if len(batch) < 100:
                return
            page += 1

    def write(self, kind: str, repo: str, identifier: Any, body: str) -> None:
        path = "issues" if kind == "issue" else "pulls"
        self.request("PATCH", f"/repos/{repo}/{path}/comments/{identifier}", {"body": body})


def checkpoint_run(body: str) -> str:
    found = CHECKPOINT.findall(body)
    if not found:
        return ""
    if len(found) > 1:
        raise RewriteError("sticky summary has more than one checkpoint marker")
    try:
        payload = json.loads(base64.b64decode(found[0], validate=True))
    except (binascii.Error, ValueError) as error:
        raise RewriteError(f"cannot decode the checkpoint marker: {error}") from error
    if not isinstance(payload, dict):
        raise RewriteError("checkpoint marker is not a JSON object")
    return str(payload.get("run") or "")


def find_summary(
    github: GitHub, repo: str, pr: Any, run_id: str, attempt: str
) -> dict[str, Any]:
    tag = f"<!-- ocr-summary-run:{run_id}-{attempt} -->"
    tagged: dict[str, Any] | None = None
    candidates: list[dict[str, Any]] = []
    malformed = 0
    for comment in github.paginate(f"/repos/{repo}/issues/{pr}/comments"):
        body = comment.get("body")
        if not isinstance(body, str) or SUMMARY_MARKER not in body:
            continue
        if tag in body:
            if tagged is not None:
                raise RewriteError("more than one sticky summary carries this run's tag")
            tagged = comment
            continue
        # A clean run posts no findings, so OCR omits the per-run tag and only
        # the checkpoint carries its run id; see the zero-comment exit in
        # https://github.com/alibaba/open-code-review/blob/d29ca2044a42ea297a7fa93692d0f5ac8f38d84e/scripts/github-actions/post-review-comments.js
        # Any comment can quote the marker, so a corrupt checkpoint on a
        # foreign comment means "not ours"; if nothing else matches, the count
        # is named in the error instead of passing silently.
        try:
            ours = checkpoint_run(body) == run_id
        except RewriteError:
            malformed += 1
            continue
        if ours:
            candidates.append(comment)
    if tagged is not None:
        return tagged
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise RewriteError("more than one sticky summary matches this run")
    if malformed:
        raise RewriteError(
            "no sticky summary comment for this run "
            f"({malformed} malformed candidate(s) ignored)"
        )
    raise RewriteError("no sticky summary comment for this run")


def find_inline(
    github: GitHub, repo: str, pr: Any, run_id: str, attempt: str
) -> list[dict[str, Any]]:
    pattern = re.compile(
        rf"<!--\s*ocr-{re.escape(run_id)}-{re.escape(attempt)}-[a-f0-9]+\s*-->"
    )
    return [
        comment
        for comment in github.paginate(f"/repos/{repo}/pulls/{pr}/comments")
        if pattern.search(str(comment.get("body") or ""))
    ]


def previous_carried(github: GitHub, repo: str, pr: Any) -> Carried:
    summaries = [
        str(comment.get("body") or "")
        for comment in github.paginate(f"/repos/{repo}/issues/{pr}/comments")
        if SUMMARY_MARKER in str(comment.get("body") or "")
    ]
    marked = [body for body in summaries if ROUTED_ANY.search(body)]
    if not marked:
        return Carried([])
    if len(marked) > 1:
        raise RewriteError("more than one sticky summary carries routed findings")
    return decode_routed(marked[0])


def write_carried(path: str, carried: Carried) -> None:
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"findings": carried.findings}, handle)
    except OSError as error:
        raise RewriteError(f"cannot write carried findings to {path}: {error}") from error


def read_carried(path: str) -> Carried:
    if not path:
        return Carried([])
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        raise RewriteError(f"cannot read carried findings from {path}: {error}") from error
    return validate_carried(payload)


def compare_diffs(
    github: GitHub, repo: str, base: str, head: str
) -> tuple[dict[str, FileDiff], set[str]]:
    if not base or not head or base == head:
        return {}, set()
    data = github.request("GET", f"/repos/{repo}/compare/{base}...{head}")
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, list):
        raise RewriteError("compare response has no files")
    if len(files) >= 300:
        raise RewriteError("compare returned 300 files; its diff may be truncated")
    diffs: dict[str, FileDiff] = {}
    unpatched: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            continue
        path = str(item.get("filename") or "").strip()
        previous = str(item.get("previous_filename") or "").strip()
        patch = item.get("patch")
        if not path:
            continue
        if not isinstance(patch, str) or not patch:
            unpatched.add(path)
            if previous:
                unpatched.add(previous)
            continue
        diff = FileDiff(path, parse_hunks(patch))
        diffs[path] = diff
        if previous:
            diffs[previous] = diff
    return diffs, unpatched


def coordinates_for(
    github: GitHub, repo: str, carried: list[dict[str, Any]], head: str
) -> dict[str, Coordinates]:
    coordinates: dict[str, Coordinates] = {}
    warned: set[str] = set()
    bases = sorted({str(item.get("head") or "") for item in carried} - {head, ""})
    for base in bases:
        try:
            diffs, unpatched = compare_diffs(github, repo, base, head)
        except RewriteError as error:
            warn(
                f"OCR restyle: cannot map carried findings from {base[:7]} "
                f"through the reviewed range: {error}; keeping them unremapped"
            )
            continue
        for item in carried:
            path = str(item.get("path") or "")
            if (
                item.get("head") == base
                and path in unpatched
                and path not in diffs
                and path not in warned
            ):
                warned.add(path)
                warn(
                    f"OCR restyle: no diff for {path}; "
                    "keeping its carried finding unremapped"
                )
        coordinates[base] = Coordinates(diffs, frozenset(unpatched))
    return coordinates


def restore_body(body: str, marker: str) -> str:
    if not marker or ROUTED_ANY.search(body):
        return body
    return f"{marker}\n\n{body}"


def apply(
    github: GitHub,
    repo: str,
    summary: dict[str, Any],
    summary_body: str,
    inline_bodies: list[tuple[dict[str, Any], str]],
) -> list[Write]:
    writes = [
        Write("issue", summary.get("id"), str(summary.get("body") or ""), summary_body)
    ]
    for posted, body in inline_bodies:
        writes.append(
            Write("review", posted.get("id"), str(posted.get("body") or ""), body)
        )
    for write in writes:
        if len(write.body) > MAX_BODY:
            raise RewriteError(
                f"{write.kind} comment {write.identifier} would exceed GitHub's size limit"
            )
        if write.identifier is None:
            raise RewriteError(f"{write.kind} comment has no id")
    applied: list[Write] = []
    try:
        for write in writes:
            github.write(write.kind, repo, write.identifier, write.body)
            applied.append(write)
    except Exception:
        for write in reversed(applied):
            try:
                github.write(write.kind, repo, write.identifier, write.original)
            except Exception as error:
                warn(
                    f"OCR restyle rollback failed for {write.kind} "
                    f"{write.identifier}: {error}"
                )
        raise
    return writes


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Restyle OCR's PR comments")
    parser.add_argument("--result")
    parser.add_argument("--repo")
    parser.add_argument("--pr")
    parser.add_argument("--run-id")
    parser.add_argument("--run-attempt")
    parser.add_argument("--token")
    parser.add_argument("--api-url")
    parser.add_argument("--range-reason")
    parser.add_argument("--range-from")
    parser.add_argument("--range-to")
    parser.add_argument("--route-severity-below")
    parser.add_argument("--route-categories")
    parser.add_argument("--effort")
    parser.add_argument("--model")
    parser.add_argument("--version")
    parser.add_argument("--carried")
    parser.add_argument("--snapshot")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    environment = {
        "RANGE_TO": args.range_to or os.environ.get("OCR_RANGE_TO", ""),
        "RANGE_FROM": args.range_from or os.environ.get("OCR_RANGE_FROM", ""),
        "EFFORT": args.effort or os.environ.get("OCR_EFFORT", ""),
        "MODEL": args.model or os.environ.get("OCR_MODEL", ""),
        "VERSION": args.version or os.environ.get("OCR_VERSION", ""),
    }
    try:
        repo = args.repo or os.environ.get("GITHUB_REPOSITORY", "")
        token = args.token or os.environ.get("GITHUB_TOKEN", "")
        pr = args.pr or os.environ.get("PR_NUMBER") or os.environ.get("OCR_PR_NUMBER") or ""
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise RewriteError("GITHUB_REPOSITORY is not owner/repo")
        if not re.fullmatch(r"\d+", str(pr)):
            raise RewriteError("PR number is missing or not numeric")
        if not token:
            raise RewriteError("GITHUB_TOKEN is missing")

        github = GitHub(
            args.api_url or os.environ.get("GITHUB_API_URL", "https://api.github.com"),
            token,
        )
        if args.snapshot:
            try:
                carried = previous_carried(github, repo, pr)
            except RewriteError as error:
                warn(f"OCR carry skipped: {error}")
                carried = Carried([])
            write_carried(args.snapshot, carried)
            print(
                "OCR restyle: carrying "
                f"{len(carried.findings)} routed finding(s) from the previous summary"
            )
            return 0

        run_id = args.run_id or os.environ.get("GITHUB_RUN_ID", "")
        attempt = args.run_attempt or os.environ.get("GITHUB_RUN_ATTEMPT", "1")
        if not re.fullmatch(r"\d+", str(run_id)) or not re.fullmatch(r"\d+", str(attempt)):
            raise RewriteError("run id or attempt is not numeric")

        result = load_result(
            args.result or os.environ.get("OCR_RESULT", "/tmp/ocr-result.json")
        )
        range_reason = args.range_reason or os.environ.get("OCR_RANGE_REASON", "")
        if not range_reason:
            raise RewriteError("OCR_RANGE_REASON is missing")
        if range_reason == "same_head_noop":
            print("OCR restyle: same head as the checkpoint; nothing to rewrite")
            return 0
        if result.get("status") == "skipped":
            print("OCR restyle: review skipped; nothing to rewrite")
            return 0

        policy = build_policy(
            args.route_severity_below
            or os.environ.get("OCR_ROUTE_SEVERITY_BELOW", ""),
            args.route_categories or os.environ.get("OCR_ROUTE_CATEGORIES", ""),
        )
        meta = meta_from(result, environment)
        meta["run"] = str(run_id)
        try:
            carried = read_carried(
                args.carried or os.environ.get("OCR_CARRIED", "")
            )
        except RewriteError as error:
            warn(f"OCR restyle: {error}; continuing without carried findings")
            carried = Carried([])
        coordinates = (
            coordinates_for(github, repo, carried.findings, meta["to"])
            if carried.findings
            else {}
        )
        summary = find_summary(github, repo, pr, str(run_id), str(attempt))
        posted = find_inline(github, repo, pr, str(run_id), str(attempt))
        links, pairs = match_inline(result, posted)
        expected: dict[str, int] = {}
        for name in ("OCR_INLINE", "OCR_SKIPPED"):
            value = os.environ.get(name, "")
            if not value.isdigit():
                raise RewriteError(f"{name} is missing or not numeric: {value!r}")
            expected[name.removeprefix("OCR_").lower()] = int(value)
        summary_body = rewrite_summary(
            str(summary.get("body") or ""),
            result,
            links,
            meta,
            policy,
            expected,
            carried=carried.findings,
            coordinates=coordinates,
        )
        inline_bodies = [
            (item, rewrite_inline(str(item.get("body") or ""), comment))
            for item, comment in pairs
        ]
        built = ROUTED_FULL.search(summary_body)
        marker = built.group(0) if built and built.group(0) != encode_routed([]) else ""
        restore = {
            **summary,
            "body": restore_body(str(summary.get("body") or ""), marker),
        }
        apply(github, repo, restore, summary_body, inline_bodies)
        print(
            f"OCR restyle: updated the summary and {len(inline_bodies)} inline comment(s)"
        )
    except Exception as error:
        warn(f"OCR restyle skipped: {error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
