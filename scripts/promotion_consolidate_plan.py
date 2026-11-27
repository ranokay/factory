import json
from pathlib import Path
from promotion_consolidate_text import COVERED_PREFIX, CORRECTION_RE, GATE_RE, MERGED_PREFIX, Refusal, check_defined, survivor_of, unmanaged_text


def load_plan(path):
    try:
        plan = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise Refusal(f"cannot read the plan {path}: {error}") from None
    if not isinstance(plan, dict):
        raise Refusal("the plan must be a JSON object")
    for key in ("clusters", "covered"):
        if not isinstance(plan.get(key), list):
            raise Refusal(f"the plan needs a {key} list, even an empty one")
    return plan


def completed(members, promotions, trust):
    survivor, sources = members[0], members[1:]
    if promotions[survivor]["state"] != "CLOSED":
        return False
    return all(
        promotions[number]["state"] == "CLOSED" and trust.has(number, f"{MERGED_PREFIX}{survivor} -->")
        for number in sources
    )


def planned_closures(plan):
    closures = set()
    for cluster in plan["clusters"]:
        members = sorted(cluster["members"])
        closures.update(members[1:])
    closures.update(entry["issue"] for entry in plan["covered"])
    return closures


class Ledger:
    def __init__(self, promotions, trust):
        self.promotions = promotions
        self.trust = trust
        self.problems = []
        self.claims = {}

    def claim(self, number, where):
        if number in self.claims:
            self.problems.append(f"#{number} appears in {self.claims[number]} and in {where}")
        self.claims[number] = where

    def known(self, number, where, closed_marker, closed_as):
        if not isinstance(number, int) or isinstance(number, bool):
            self.problems.append(f"{where} lists {number!r}, which is not an issue number")
            return False
        if number not in self.promotions:
            self.problems.append(f"#{number} is not a promotion issue ({where})")
            return False
        if self.promotions[number]["state"] == "CLOSED":
            if closed_marker is None:
                self.problems.append(f"#{number} is closed, so it cannot be used here ({where})")
                return False
            if not self.trust.has(number, closed_marker):
                self.problems.append(f"#{number} is closed and this pass did not close it as {closed_as} ({where})")
                return False
        return True


def one_line(value):
    return isinstance(value, str) and bool(value.strip()) and "\n" not in value


def check_member(ledger, number, where, survivor):
    if number == survivor:
        valid = ledger.known(number, where, None, None)
    else:
        valid = ledger.known(number, where, f"{MERGED_PREFIX}{survivor} -->", f"a duplicate of survivor #{survivor}")
    if valid:
        ledger.claim(number, where)


def check_cluster(ledger, index, cluster):
    where = f"cluster {index + 1}"
    if not isinstance(cluster, dict):
        ledger.problems.append(f"{where} must be an object")
        return
    members = cluster.get("members")
    if not isinstance(members, list) or len(members) < 2:
        ledger.problems.append(f"{where} needs at least two members")
        return
    if not one_line(cluster.get("reason")):
        ledger.problems.append(f"{where} needs a one-line reason")
    numbers = [m for m in members if isinstance(m, int) and not isinstance(m, bool)]
    survivor = survivor_of(numbers) if numbers else None
    if (
        len(numbers) == len(members)
        and all(n in ledger.promotions for n in numbers)
        and completed(sorted(numbers), ledger.promotions, ledger.trust)
    ):
        for number in numbers:
            ledger.claim(number, where)
        return
    for number in members:
        check_member(ledger, number, where, survivor)
    if survivor in ledger.promotions and unmanaged_text(ledger.promotions[survivor]["body"]):
        ledger.problems.append(
            f"survivor #{survivor} has text outside the merge blocks; move it out before merging"
        )


def check_text(plan, promotions, problems):
    touched = set()
    for cluster in plan["clusters"]:
        if isinstance(cluster, dict) and isinstance(cluster.get("members"), list):
            touched.update(m for m in cluster["members"] if isinstance(m, int))
    touched.update(
        entry["issue"]
        for entry in plan["covered"]
        if isinstance(entry, dict) and isinstance(entry.get("issue"), int)
    )
    for number, item in promotions.items():
        if item["state"] != "OPEN" and number not in touched:
            continue
        if not CORRECTION_RE.search(item["body"]):
            problems.append(f"#{number} has no ### Correction section")
        if not GATE_RE.search(item["body"]):
            problems.append(f"#{number} has no ### Gate check section")


def check_covered(ledger, index, entry, root):
    where = f"covered entry {index + 1}"
    if not isinstance(entry, dict):
        ledger.problems.append(f"{where} must be an object")
        return
    number = entry.get("issue")
    check = entry.get("check")
    well_formed = (
        isinstance(check, dict) and isinstance(check.get("file"), str) and isinstance(check.get("name"), str)
    )
    if well_formed:
        covered_marker = f"{COVERED_PREFIX}{check['file']}#{check['name']} -->"
        covered_as = f"covered by {check['file']}#{check['name']}"
    else:
        covered_marker, covered_as = None, None
    if ledger.known(number, where, covered_marker, covered_as):
        ledger.claim(number, where)
    if not one_line(entry.get("reason")):
        ledger.problems.append(f"{where} needs a one-line reason")
    if not well_formed:
        ledger.problems.append(f"{where} needs a check with file and name")
        return
    try:
        check_defined(root, check)
    except Refusal as error:
        ledger.problems.append(f"{where} (#{number}): {error}")


def validate(plan, promotions, trust, root):
    ledger = Ledger(promotions, trust)
    for index, cluster in enumerate(plan["clusters"]):
        check_cluster(ledger, index, cluster)
    check_text(plan, promotions, ledger.problems)
    for index, entry in enumerate(plan["covered"]):
        check_covered(ledger, index, entry, root)
    return ledger.problems
