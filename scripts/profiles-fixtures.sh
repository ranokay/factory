#!/bin/sh
set -eu

script_dir=$(CDPATH='' cd "$(dirname "$0")" && pwd)
root=$(CDPATH='' cd "$script_dir/.." && pwd)
python=$(mise -C "$root" which python)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/factory-profiles.XXXXXX")
trap 'rm -rf "$tmp"' EXIT HUP INT TERM

mkdir -p "$tmp/profiles"
for profile in universal typescript python web; do
	cp -R "$root/profiles/$profile" "$tmp/profiles/$profile"
done

run_fixture() {
	name=$1
	repo=$tmp/$name
	mkdir -p "$repo"
	cp -R "$root/profiles/$name/tests/fixture/." "$repo/"
	git -C "$repo" init -q
	git -C "$repo" add -A
	git -C "$repo" -c user.name=profiles-fixtures -c user.email=fixtures@invalid commit -qm fixture
	(
		cd "$repo"
		export MISE_CEILING_PATHS="$tmp"
		export MISE_IGNORED_CONFIG_PATHS="${HOME}/.config/mise/config.toml:${HOME}/.mise.toml"
		mise install
		mise run check
		mise exec -- hk check --all --plan -a --json --no-progress >"$tmp/$name-steps.json"
		mise ls --current --json >"$tmp/$name-tools.json"
	)
	uv run --offline --no-project --no-config --python "$python" "$root/scripts/profiles-check.py" --root "$repo"
	uv run --offline --no-project --no-config --python "$python" python "$script_dir/profiles-fixtures.py" "$name" "$tmp" "$tmp/$name-tools.json" "$tmp/$name-steps.json"
}

run_fixture typescript
run_fixture python
run_fixture web

echo "profiles:fixtures: every fixture passed in isolation"
