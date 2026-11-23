import re


PROFILES_LINE = re.compile(r'^[ \t]*Profiles:[ \t]*(.*)$')


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
