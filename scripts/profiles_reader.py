import re


PROFILES_LINE = re.compile(r'^[ \t]*Profiles:[ \t]*(.*)$')
TAG = re.compile(r'^v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.]+)?$')
GIT_REF = re.compile(r'[?&]ref=([^&]+)')
RAW_FACTORY = re.compile(r'^https?://raw\.githubusercontent\.com/ranokay/factory/(?P<ref>[^/]+)/')
GITHUB_RAW = re.compile(r'^https?://github\.com/ranokay/factory/raw/(?P<ref>[^/]+)/')
GITHUB_BLOB = re.compile(r'^https?://github\.com/ranokay/factory/blob/(?P<ref>[^/]+)/')
GITHUB_TREE = re.compile(r'^https?://github\.com/ranokay/factory/tree/(?P<ref>[^/]+)/')
REF_PATH = re.compile(
    r'^https?://(?:raw\.githubusercontent\.com|github\.com)/ranokay/factory/refs/(?:heads|tags)/(?P<ref>[^/]+)/'
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
    if match is not None and 'ranokay/factory' in reference:
        return match.group(1)
    match = REF_PATH.match(reference)
    if match is not None:
        return match.group('ref')
    for pattern in (RAW_FACTORY, GITHUB_RAW, GITHUB_BLOB, GITHUB_TREE):
        match = pattern.match(reference)
        if match is not None:
            return match.group('ref')
    return None
