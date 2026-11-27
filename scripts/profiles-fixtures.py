#!/usr/bin/env python3
"""Assert that one Profile fixture declares only its Profiles' tools and steps."""

import json
import os
import re
import sys
import tomllib
from pathlib import Path

COMMON_TOOLS = {
    'aqua:jdx/jactionlint',
    'aqua:mikefarah/yq',
    'editorconfig-checker',
    'gitleaks',
    'gh',
    'hk',
    'npm:@codacy/analysis-cli',
    'node',
    'pinact',
    'shellcheck',
    'shfmt',
    'tombi',
    'trivy',
    'typos',
    'yamlfmt',
    'zizmor',
}
PROFILE_TOOLS = {
    'universal': {'resvg'},
    'typescript': {'bun', 'node', 'npm:knip', 'npm:typescript', 'oxfmt', 'oxlint'},
    'python': {'ruff', 'ty', 'uv'},
    'web': {'npm:@playwright/test', 'resvg'},
    'swift': {'github:nicklockwood/SwiftFormat', 'aqua:realm/SwiftLint'},
    'apple': set(),
    'go': {'go', 'aqua:golangci/golangci-lint', 'aqua:mvdan/gofumpt', 'go:golang.org/x/vuln/cmd/govulncheck'},
}
COMMON_STEPS = {
    'assets',
    'comment-ban',
    'editorconfig-checker',
    'gate-groups',
    'gitleaks',
    'jactionlint',
    'pinact',
    'shellcheck',
    'shfmt',
    'tombi',
    'trivy',
    'typos',
    'yamlfmt',
    'zizmor',
}
PROFILE_STEPS = {
    'universal': set(),
    'typescript': {'knip', 'oxfmt', 'oxlint', 'tsc'},
    'python': {'ruff', 'ruff-format', 'ty'},
    'web': {'web-assets'},
    'swift': {'swiftformat', 'swiftlint'},
    'apple': set(),
    'go': {'golangci-lint', 'gofumpt', 'govulncheck'},
}
DECLARED = {
    'typescript': {'universal', 'typescript'},
    'python': {'universal', 'python'},
    'web': {'universal', 'typescript', 'web'},
    'swift': {'universal', 'swift'},
    'apple': {'universal', 'swift', 'apple'},
    'go': {'universal', 'go'},
}


def fail(message: str) -> None:
    print(f'profiles:fixtures: {message}', file=sys.stderr)
    raise SystemExit(1)


def declared_root(prefix: str, source: object) -> bool:
    if not isinstance(source, dict):
        return False
    path = source.get('path')
    return isinstance(path, str) and os.path.realpath(path).startswith(prefix)


def check_combined(path: Path) -> None:
    tasks = {task['name']: task for task in json.loads(path.read_text())}
    expected = {
        'typecheck:python': 'ty check',
        'typecheck:typescript': 'tsc --noEmit',
        'build:swift': 'swift build',
        'test:go': 'go test ./...',
    }
    missing = sorted(set(expected) - set(tasks))
    if missing:
        fail(f'combined tasks missing {missing}')
    wrong = sorted(name for name, command in expected.items() if tasks[name].get('run') != [command])
    if wrong:
        fail(f'combined tasks have the wrong command: {wrong}')
    print(f'profiles:fixtures: combined: tasks: {", ".join(sorted(expected))}')


def main() -> None:
    if sys.argv[1] == '--combined':
        check_combined(Path(sys.argv[2]))
        return
    name, tmp, tools_path, steps_path = sys.argv[1:5]
    if name == 'swift':
        profile = Path(tmp) / 'profiles' / 'swift'
        configured = tomllib.loads((profile / 'mise.toml').read_text())['tools']
        gate = (profile / 'hk.pkl').read_text().split('["swiftlint"] =', 1)[1].split('tests {', 1)[0]
        lookups = re.findall(r'^\s*(swift_prefix|libxml_prefix)=\$\(mise where ([^)]+)\)', gate, re.MULTILINE)
        expected_lookups = [
            ('swift_prefix', f"swift@{configured['swift']['version']}"),
            ('libxml_prefix', f"conda:libxml2@{configured['conda:libxml2']['version']}"),
        ]
        if lookups != expected_lookups:
            fail('swift: Gate lookups do not match mise pins')
    profiles = DECLARED[name]
    prefix = os.path.realpath(tmp) + os.sep

    tools = json.loads(Path(tools_path).read_text())
    declared_tools = sorted(
        tool
        for tool, entries in tools.items()
        if any(declared_root(prefix, entry.get('source', {})) for entry in entries)
    )
    expected = COMMON_TOOLS.union(*(PROFILE_TOOLS[profile] for profile in profiles))
    if 'swift' in profiles and sys.platform.startswith('linux'):
        expected.update({'swift', 'conda:libxml2'})
    expected_tools = sorted(expected)
    if declared_tools != expected_tools:
        missing = sorted(set(expected_tools) - set(declared_tools))
        extra = sorted(set(declared_tools) - set(expected_tools))
        fail(f'{name}: tools mismatch; missing {missing}, extra {extra}')

    text = Path(steps_path).read_text()
    plan = json.loads(text[text.index('{') :])
    steps = sorted(step['name'] for step in plan['steps'])
    expected_steps = COMMON_STEPS.union(*(PROFILE_STEPS[profile] for profile in profiles))
    if set(steps) != expected_steps:
        missing = sorted(expected_steps - set(steps))
        extra = sorted(set(steps) - expected_steps)
        fail(f'{name}: steps mismatch; missing {missing}, extra {extra}')

    print(f'profiles:fixtures: {name}: tools: {", ".join(declared_tools)}')
    print(f'profiles:fixtures: {name}: steps: {", ".join(steps)}')


if __name__ == '__main__':
    main()
