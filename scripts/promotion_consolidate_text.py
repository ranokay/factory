import math
import re
import subprocess  # nosec B404 -- git runs with an argument list, never a shell
from collections import Counter
from pathlib import Path


STOPWORDS = frozenset(
    "a an and any are as at be by each every for from in into is it not of on or only out "
    "outside the this that to when while with without must new has have was were its their "
    "than then so if but which who what do does did one two also via".split()
)


CORRECTION_RE = re.compile(r"### Correction\s*\n(.*?)(?=\n###|\Z)", re.S)


GATE_RE = re.compile(r"### Gate check\s*\n(.*?)(?=\n###|\Z)", re.S)


TRACKING_MARKER = "<!-- factory:promotion-consolidate-tracking -->"


PUSHBACK_MARKER = "<!-- factory:promotion-pushback -->"


MERGED_PREFIX = "<!-- factory:promotion-merged into="


COVERED_PREFIX = "<!-- factory:promotion-covered check="


LINKS_START = "<!-- factory:promotion-links -->"


LINKS_END = "<!-- /factory:promotion-links -->"


OCCURRENCES_HEADING = "### Merged occurrences"


OCCURRENCE_RE = re.compile(
    r"<!-- factory:promotion-occurrence source=(\d+) -->\n(.*?)\n<!-- /factory:promotion-occurrence -->",
    re.S,
)


RESHAPED_RE = re.compile(
    r"(?:^|\n)## Reshaped scope(?=\n|\Z).*?(?=\n## |\n### Merged occurrences|\n<!-- factory:promotion-links|\Z)", re.S
)


INTEGRATION_REF = "origin/main"


PUSHBACK_LABEL = "needs-triage"


class Refusal(Exception):
    pass


def occurrences(body):
    match = CORRECTION_RE.search(body or "")
    if not match:
        raise Refusal("a promotion has no ### Correction section")
    items = []
    for line in match.group(1).splitlines():
        text = " ".join(line.split())
        if not text:
            continue
        if line.startswith("- "):
            items.append(" ".join(line[2:].split()))
        elif items:
            items[-1] = f"{items[-1]} {text}"
        else:
            items.append(text)
    return items


def tokens(text):
    words = re.findall(r"[a-z][a-z0-9]+", text.lower())
    return [word for word in words if word not in STOPWORDS and len(word) > 2]


def document(item):
    correction = CORRECTION_RE.search(item["body"])
    gate = GATE_RE.search(item["body"])
    return " ".join([item["title"], correction.group(1) if correction else "", gate.group(1) if gate else ""])


def weights(pool, promotions):
    counts = {n: Counter(tokens(document(promotions[n]))) for n in pool}
    frequency = Counter()
    for count in counts.values():
        frequency.update(count.keys())
    idf = {word: math.log((1 + len(pool)) / (1 + df)) + 1 for word, df in frequency.items()}
    vectors = {}
    for number, count in counts.items():
        raw = {word: tf * idf[word] for word, tf in count.items()}
        norm = math.sqrt(sum(value * value for value in raw.values())) or 1.0
        vectors[number] = {word: value / norm for word, value in raw.items()}
    return vectors


def cosine(first, second):
    small, large = (first, second) if len(first) <= len(second) else (second, first)
    return sum(weight * large.get(word, 0.0) for word, weight in small.items())


def reshaped_scope(body):
    match = RESHAPED_RE.search(body or "")
    return match.group(0).strip() if match else ""


def unmanaged_text(body):
    managed = (body or "").split(LINKS_START, 1)
    if len(managed) == 1:
        return ""
    rest = managed[1].split(LINKS_END, 1)[-1]
    rest = RESHAPED_RE.sub("", rest)
    rest = re.sub(OCCURRENCE_RE, "", rest)
    rest = rest.replace(OCCURRENCES_HEADING, "")
    return rest.strip()


def survivor_of(members):
    return min(members)


def merged_body(survivor, body, sources, carried):
    body = body or ""
    base = RESHAPED_RE.sub("", body.split(LINKS_START, 1)[0]).rstrip()
    reshaped = reshaped_scope(body)
    blocks = {int(number): inner for number, inner in OCCURRENCE_RE.findall(body)}
    for number, inner in carried.items():
        blocks.setdefault(number, inner)
    for number, source_body in sources:
        if number not in blocks:
            lines = [f"- {item}" for item in occurrences(source_body)]
            blocks[number] = "\n".join([f"From #{number}:", *lines])
    members = ", ".join([f"#{survivor} (survivor)", *(f"#{number}" for number in sorted(blocks))])
    rendered = [
        f"<!-- factory:promotion-occurrence source={number} -->\n{blocks[number]}\n"
        "<!-- /factory:promotion-occurrence -->"
        for number in sorted(blocks)
    ]
    text = "\n".join(
        [
            base,
            "",
            LINKS_START,
            f"Cluster members: {members}. Their corrections and occurrences are copied below.",
            LINKS_END,
            "",
            OCCURRENCES_HEADING,
            "",
            *rendered,
            "",
        ]
    )
    if reshaped:
        text = f"{text}\n{reshaped}\n"
    return text


def check_defined(root, check):
    file, name = check["file"], check["name"]
    path = Path(file)
    if path.is_absolute() or ".." in path.parts:
        raise Refusal(f"check file {file} must be a path inside the Factory")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        raise Refusal(f"check name {name!r} is not a plain identifier")
    shown = subprocess.run(  # nosec B603 B607 -- git with an argument list
        ["git", "-C", str(root), "show", f"{INTEGRATION_REF}:{file}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if shown.returncode != 0:
        detail = shown.stderr.strip() or "git show failed"
        raise Refusal(f"cannot read {file} from {INTEGRATION_REF}: {detail}")
    text = shown.stdout
    escaped = re.escape(name)
    if file.endswith(".toml"):
        defined = re.search(rf'^\s*id\s*=\s*"{escaped}"\s*$', text, re.M)
    elif file.endswith(".pkl"):
        defined = re.search(rf'^\s*\["{escaped}"\]\s*=\s*(?:new Step\b|\(?Builtins\.)', text, re.M)
    elif file.endswith((".yml", ".yaml")):
        defined = name in workflow_names(text)
    else:
        raise Refusal(f"check file {file} has no supported check format (.toml, .pkl, .yml, .yaml)")
    if not defined:
        raise Refusal(f"{file} does not define the check {name}")


def workflow_names(text):
    lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]

    def indent(line):
        return len(line) - len(line.lstrip(" "))

    names = set()
    jobs = next((i for i, line in enumerate(lines) if indent(line) == 0 and line.rstrip() == "jobs:"), None)
    if jobs is None:
        return names
    end = next((i for i in range(jobs + 1, len(lines)) if indent(lines[i]) == 0), len(lines))
    body = lines[jobs + 1 : end]
    if not body:
        return names
    job_indent = indent(body[0])
    starts = [i for i, line in enumerate(body) if indent(line) == job_indent]
    for position, start in enumerate(starts):
        region_end = starts[position + 1] if position + 1 < len(starts) else len(body)
        job_line = body[start].strip()
        if re.fullmatch(r"[A-Za-z0-9_.-]+:", job_line):
            names.add(job_line[:-1])
        region = body[start + 1 : region_end]
        child_indent = min((indent(line) for line in region), default=None)
        steps = next(
            (i for i, line in enumerate(region) if line.strip() == "steps:" and indent(line) == child_indent),
            None,
        )
        if steps is None:
            continue
        steps_indent = indent(region[steps])
        dash = None
        for line in region[steps + 1 :]:
            if indent(line) <= steps_indent:
                break
            content = line.strip()
            if content.startswith("- "):
                dash = indent(line)
                content = content[2:].strip()
                if content.startswith("name:"):
                    names.add(content[len("name:") :].strip().strip("\"'"))
            elif dash is not None and indent(line) == dash + 2 and content.startswith("name:"):
                names.add(content[len("name:") :].strip().strip("\"'"))
    return names


def plain(text):
    while "<!--" in text or "-->" in text:
        text = text.replace("<!--", "").replace("-->", "")
    return text


def author_login(item):
    author = item["author"]
    return None if author is None else author["login"]
