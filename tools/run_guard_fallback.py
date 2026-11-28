#!/usr/bin/env python3

from __future__ import annotations

import re

from run_targets import load_targets, next_target
from shell_parser import Invocation, invocations

UNAVAILABLE_TERMS = (
    "quota",
    "rate limit",
    "rate_limit",
    "overload",
    "overloaded",
    "unavailable",
    "availability",
    "refused",
    "refusal",
    "capacity",
    "exhausted",
    "usage limit",
    "usage_limit",
)
MODEL_ROLE_TERMS = (
    "agent",
    "dispatcher",
    "model",
    "models",
    "orchestrator",
    "reviewer",
    "supervisor",
    "worker",
)
OTHER_CAUSE_TERMS = (
    "gate",
    "ci",
    "check",
    "test",
    "permission",
    "credential",
    "authentication",
    "network",
    "timeout",
    "crash",
    "bug",
    "regression",
    "conflict",
    "merge",
    "secret",
)


def block_issue_arguments(invocation: Invocation) -> tuple[str, str, str, str, str] | None:
    if invocation.executable != "block-issue" or len(invocation.arguments) < 2:
        return None
    texts = [word.text for word in invocation.arguments]
    repository, issue = texts[:2]
    reason = ""
    unblock = ""
    findings: list[str] = []
    index = 2
    while index < len(texts):
        option = texts[index]
        if option in {"--reason", "--unblock", "--finding"}:
            if index + 1 >= len(texts):
                return None
            value = texts[index + 1]
            if option == "--reason":
                reason = value
            elif option == "--unblock":
                unblock = value
            else:
                findings.append(value)
            index += 2
            continue
        index += 2 if option in {"--evidence", "--detail"} else 1
    return repository, issue, reason, unblock, "\n".join(findings)


def contains_term(text: str, terms: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(term)}\b", text, re.IGNORECASE) for term in terms)


def model_named(text: str, model: str) -> bool:
    names = {model, model.rsplit("/", 1)[-1]}
    return any(
        re.search(rf"(?<![\w./-]){re.escape(name)}(?![\w./-])", text, re.IGNORECASE)
        for name in names
    )


def fallback_reason(repository: str, issue: str, reason: str, unblock: str, findings: str) -> str | None:
    targets = load_targets(repository, issue)
    if not targets:
        return None
    cause = "\n".join((reason, findings))
    named_target = any(model_named(cause, target["model"]) for target in targets)
    role_unavailability = contains_term(cause, MODEL_ROLE_TERMS) and contains_term(cause, UNAVAILABLE_TERMS)
    unavailable_cause = (named_target and contains_term(cause, UNAVAILABLE_TERMS)) or role_unavailability
    if not unavailable_cause or contains_term(cause, OTHER_CAUSE_TERMS):
        return None
    fallback = next_target(repository, issue)
    if fallback is None:
        return None
    variant = fallback.get("options", {}).get("variant")
    name = fallback["model"] + (f" at variant {variant}" if variant else "")
    return f"Factory Run guard: an untried model fallback remains: {name}. Launch it instead of posting a Blocker."


def fallback_rule(command: str) -> str | None:
    for invocation in invocations(command):
        arguments = block_issue_arguments(invocation)
        if arguments is not None:
            reason = fallback_reason(*arguments)
            if reason:
                return reason
    return None
