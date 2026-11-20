#!/bin/sh
set -eu

missing_token() {
	echo "pinact: no GitHub token; run 'gh auth login' or set GITHUB_TOKEN." >&2
	exit 1
}

if [ -z "${GITHUB_TOKEN:-}" ]; then
	GITHUB_TOKEN=$(gh auth token 2>/dev/null) || missing_token
	[ -n "$GITHUB_TOKEN" ] || missing_token
	export GITHUB_TOKEN
fi

exec pinact "$@"
