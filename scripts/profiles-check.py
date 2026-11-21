#!/usr/bin/env python3
"""Check every package's Profiles line against its mise and hk includes."""

import argparse
import os
import re
import sys
import tomllib
from pathlib import Path

PRUNE = {'.git', 'node_modules', '.venv', 'venv', 'skills', 'patches'}
PROFILE = re.compile(r'^[a-z0-9][a-z0-9-]*$')
TAG = re.compile(r'^v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.]+)?$')
PROFILE_PATH = re.compile(r'(?:^|/)profiles/([a-z0-9][a-z0-9-]*)/(?:mise\.toml|tasks\.toml|hk\.pkl)$')
GIT_INCLUDE = re.compile(r'^git::(?P<repo>.+)//(?P<path>[^?]+)(?:\?ref=(?P<ref>[^&]+))?$')
RAW_FACTORY = re.compile(
    r'^https?://raw\.githubusercontent\.com/ranokay/factory/(?P<ref>[^/]+)/'
    r'(?P<path>profiles/[a-z0-9][a-z0-9-]*/hk\.pkl)$'
)
GITHUB_RAW = re.compile(
    r'^https?://github\.com/ranokay/factory/raw/(?P<ref>[^/]+)/'
    r'(?P<path>profiles/[a-z0-9][a-z0-9-]*/hk\.pkl)$'
)
FACTORY_REPO = re.compile(
    r'^(?:https?://github\.com/ranokay/factory(?:\.git)?|ssh://git@github\.com/ranokay/factory(?:\.git)?)/?$'
)
AMENDS = re.compile(r'^\s*amends\s+"([^"]+)"', re.MULTILINE)
PROFILES_LINE = re.compile(r'^[ \t]*Profiles:[ \t]*(.*)$', re.MULTILINE)


def relative(root, path):
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def profile_name(reference):
    match = PROFILE_PATH.search(reference)
    return match.group(1) if match else None


def check_tag(manifest, kind, reference, ref, problems):
    if not ref:
        problems.append(f'{manifest}: {kind} {reference} has no ref; pin a Factory tag such as v0.2.0')
    elif not TAG.match(ref):
        problems.append(f'{manifest}: {kind} {reference} is pinned to {ref}; pin a Factory tag such as v0.2.0')


def string_list(manifest, label, value, problems):
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        problems.append(f'{manifest}: {label} must be a list of strings')
        return []
    return value


def parse_mise(path, root):
    manifest = relative(root, path)
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as error:
        return set(), [f'{manifest}: cannot read: {error}']
    problems = []
    names = set()
    entries = string_list(manifest, 'include', data.get('include'), problems)
    task_config = data.get('task_config') or {}
    if not isinstance(task_config, dict):
        problems.append(f'{manifest}: task_config must be a table')
    else:
        entries += string_list(manifest, 'task_config.includes', task_config.get('includes'), problems)
    for entry in entries:
        if entry.startswith('git::'):
            match = GIT_INCLUDE.match(entry)
            if match is None:
                problems.append(f'{manifest}: cannot parse include {entry}')
                continue
            if not FACTORY_REPO.match(match.group('repo')):
                continue
            name = profile_name(match.group('path'))
            if name is None:
                continue
            names.add(name)
            check_tag(manifest, 'include', entry, match.group('ref'), problems)
        elif '::' not in entry and '://' not in entry:
            name = profile_name(entry)
            if name is not None:
                names.add(name)
    return names, problems


def parse_hk(path, root):
    manifest = relative(root, path)
    try:
        text = path.read_text()
    except OSError as error:
        return set(), [f'{manifest}: cannot read: {error}']
    problems = []
    names = set()
    match = AMENDS.search(text)
    reference = match.group(1) if match else None
    if reference is None:
        return names, problems
    name = profile_name(reference)
    if reference.startswith(('http://', 'https://')):
        factory = RAW_FACTORY.match(reference) or GITHUB_RAW.match(reference)
        if factory is None:
            return names, problems
        if name is not None:
            names.add(name)
        check_tag(manifest, 'amends', reference, factory.group('ref'), problems)
    elif '::' not in reference and '://' not in reference:
        if name is not None:
            names.add(name)
    return names, problems


def parse_profiles(root, agents, problems):
    name = relative(root, agents)
    try:
        text = agents.read_text()
    except OSError as error:
        problems.append(f'{name}: cannot read: {error}')
        return None
    matches = PROFILES_LINE.findall(text)
    if not matches:
        problems.append(f'{name}: has no Profiles line')
        return None
    if len(matches) > 1:
        problems.append(f'{name}: has more than one Profiles line')
        return None
    names = [entry.strip() for entry in matches[0].split(',') if entry.strip()]
    if not names:
        problems.append(f'{name}: has an empty Profiles line')
        return None
    invalid = [entry for entry in names if not PROFILE.match(entry)]
    if invalid:
        problems.append(f'{name}: lists invalid Profile names: {", ".join(invalid)}')
        return None
    duplicates = sorted({entry for entry in names if names.count(entry) > 1})
    if duplicates:
        problems.append(f'{name}: lists {", ".join(duplicates)} more than once')
        return None
    return set(names)


def nearest_agents(package, root):
    current = package
    while True:
        candidate = current / 'AGENTS.md'
        if candidate.is_file():
            return candidate
        if current == root:
            return None
        current = current.parent


def discover(root):
    candidates = []
    for base, directories, files in os.walk(root):
        directories[:] = sorted(name for name in directories if name not in PRUNE and not name.startswith('.'))
        if 'mise.toml' in files or 'hk.pkl' in files:
            candidates.append(Path(base))
    return candidates


def is_profile_definition(root, package):
    parts = package.relative_to(root).parts
    return bool(parts) and parts[0] == 'profiles'


def check(root):
    problems = []
    provided = {}
    for package in discover(root):
        if is_profile_definition(root, package):
            continue
        manifests = {}
        if (package / 'mise.toml').is_file():
            names, manifest_problems = parse_mise(package / 'mise.toml', root)
            problems += manifest_problems
            if names:
                manifests['mise.toml'] = names
        if (package / 'hk.pkl').is_file():
            names, manifest_problems = parse_hk(package / 'hk.pkl', root)
            problems += manifest_problems
            if names:
                manifests['hk.pkl'] = names
        provided[package] = manifests
    for package, manifests in provided.items():
        agents = nearest_agents(package, root)
        if agents is None:
            problems.append(f'{relative(root, package)}: has no AGENTS.md above it')
            continue
        agents_name = relative(root, agents)
        declared = parse_profiles(root, agents, problems)
        if declared is None:
            continue
        included = set().union(*manifests.values()) if manifests else set()
        for name in sorted(declared - included):
            problems.append(f'{agents_name} declares {name}, but no mise or hk include provides it')
        for name in sorted(included - declared):
            sources = ', '.join(sorted(manifest for manifest, names in manifests.items() if name in names))
            problems.append(f'{agents_name} does not declare {name}, which {sources} includes')
    return list(provided), problems


def main():
    parser = argparse.ArgumentParser(description='Compare each package Profiles line with its mise and hk Profile includes.')
    parser.add_argument('--root', default='.', type=Path, help='repository root to check')
    arguments = parser.parse_args()
    root = arguments.root.resolve()
    if not root.is_dir():
        print(f'profiles: {root} is not a directory', file=sys.stderr)
        return 2
    packages, problems = check(root)
    if problems:
        for problem in problems:
            print(f'profiles: {problem}', file=sys.stderr)
        return 1
    print(f'profiles: {len(packages)} checked; every Profiles line matches')
    return 0


if __name__ == '__main__':
    sys.exit(main())
