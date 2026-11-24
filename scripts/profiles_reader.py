import re


PROFILES_LINE = re.compile(r'^[ \t]*Profiles:[ \t]*(.*)$')
TAG = re.compile(r'^v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.]+)?$')
GIT_REF = re.compile(r'[?&]ref=([^&]+)')
FACTORY_INCLUDE = re.compile(
    r'^(?:git::)?(?:'
    r'https?://(?:github\.com|raw\.githubusercontent\.com)/ranokay/factory(?:\.git)?'
    r'|ssh://git@github\.com/ranokay/factory(?:\.git)?'
    r'|git@github\.com:ranokay/factory(?:\.git)?'
    r')',
    re.IGNORECASE,
)
RAW_FACTORY = re.compile(
    r'^https?://raw\.githubusercontent\.com/ranokay/factory/(?P<ref>[^/]+)/', re.IGNORECASE
)
GITHUB_RAW = re.compile(
    r'^https?://github\.com/ranokay/factory/raw/(?P<ref>[^/]+)/', re.IGNORECASE
)
GITHUB_BLOB = re.compile(
    r'^https?://github\.com/ranokay/factory/blob/(?P<ref>[^/]+)/', re.IGNORECASE
)
GITHUB_TREE = re.compile(
    r'^https?://github\.com/ranokay/factory/tree/(?P<ref>[^/]+)/', re.IGNORECASE
)
REF_PATH = re.compile(
    r'^https?://(?:raw\.githubusercontent\.com|github\.com)/ranokay/factory/refs/(?:heads|tags)/(?P<ref>[^/]+)/',
    re.IGNORECASE,
)
PROFILE_PATH = re.compile(
    r'(?:^|/)profiles/([a-z0-9][a-z0-9-]*)/(?:mise\.toml|tasks\.toml|hk\.pkl)(?:\?|$)'
)


def profiles_entries(text):
    entries = []
    fenced = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(('```', '~~~')):
            fenced = not fenced
            continue
        if fenced:
            continue
        match = PROFILES_LINE.match(line)
        if match is not None:
            entries.append(match.group(1))
    return entries


def factory_ref(reference):
    match = GIT_REF.search(reference)
    if match is not None and FACTORY_INCLUDE.match(reference):
        return match.group(1)
    match = REF_PATH.match(reference)
    if match is not None:
        return match.group('ref')
    for pattern in (RAW_FACTORY, GITHUB_RAW, GITHUB_BLOB, GITHUB_TREE):
        match = pattern.match(reference)
        if match is not None:
            return match.group('ref')
    return None
