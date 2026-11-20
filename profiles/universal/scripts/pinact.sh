#!/bin/sh
set -eu

missing_token() {
	echo "pinact: no GitHub token; run 'gh auth login' or set GITHUB_TOKEN." >&2
	exit 1
}

if [ -z "${GITHUB_TOKEN:-}" ]; then
	GITHUB_TOKEN=$(gh auth token) || missing_token
	[ -n "$GITHUB_TOKEN" ] || missing_token
	export GITHUB_TOKEN
	echo "pinact: authenticated with the token from gh auth" >&2
else
	echo "pinact: authenticated with GITHUB_TOKEN from the environment" >&2
fi

exec pinact "$@"
