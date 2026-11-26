#!/bin/sh
set -eu

script_dir=$(CDPATH='' cd "$(dirname "$0")" && pwd)
root=$(CDPATH='' cd "$script_dir/.." && pwd)
checker="$root/profiles/universal/scripts/trivy-check.sh"
case_name=${TRIVY_CHECK_CASE:-all}

test_root=$(mktemp -d "${TMPDIR:-/tmp}/factory-trivy-test.XXXXXX")
trap 'rm -rf "$test_root"' EXIT HUP INT TERM

fail() {
	printf 'trivy-check-test: %s\n' "$*" >&2
	exit 1
}

run_command() {
	if output=$("$@" 2>&1); then
		code=0
	else
		code=$?
	fi
}

run_check() {
	run_command "$checker" "$1"
}

case_unexplained_ignore() {
	mkdir -p "$test_root/unexplained-ignore"
	printf '%s\n' 'CVE-2021-23337' >"$test_root/unexplained-ignore/.trivyignore"
	run_check "$test_root/unexplained-ignore"
	[ "$code" -ne 0 ] || fail "an unexplained .trivyignore entry passed"
	case $output in
	*".trivyignore:1"*"requires a reason"*) ;;
	*) fail "unexpected diagnostic: $output" ;;
	esac
	printf 'ok: an unexplained .trivyignore entry fails with its line\n'
}

write_vulnerable_dependency() {
	mkdir -p "$1"
	cat >"$1/package.json" <<'JSON'
{"name":"trivy-vulnerable-fixture","version":"1.0.0","dependencies":{"lodash":"4.17.20"}}
JSON
	cat >"$1/package-lock.json" <<'JSON'
{"name":"trivy-vulnerable-fixture","version":"1.0.0","lockfileVersion":3,"requires":true,"packages":{"":{"name":"trivy-vulnerable-fixture","version":"1.0.0","dependencies":{"lodash":"4.17.20"}},"node_modules/lodash":{"version":"4.17.20","resolved":"https://registry.npmjs.org/lodash/-/lodash-4.17.20.tgz"}}}
JSON
}

case_vulnerable_dependency() {
	write_vulnerable_dependency "$test_root/vulnerable-dependency"
	run_check "$test_root/vulnerable-dependency"
	[ "$code" -ne 0 ] || fail "a known-vulnerable pinned dependency passed"
	case $output in
	*HIGH*CVE-2021-23337*) ;;
	*) fail "expected the known HIGH vulnerability: $output" ;;
	esac
	printf 'ok: a known-vulnerable pinned dependency fails the Gate\n'
}

case_lower_severity() {
	mkdir -p "$test_root/lower-severity"
	cat >"$test_root/lower-severity/package.json" <<'JSON'
{"name":"trivy-lower-severity-fixture","version":"1.0.0","dependencies":{"tough-cookie":"4.1.2"}}
JSON
	cat >"$test_root/lower-severity/package-lock.json" <<'JSON'
{"name":"trivy-lower-severity-fixture","version":"1.0.0","lockfileVersion":3,"requires":true,"packages":{"":{"name":"trivy-lower-severity-fixture","version":"1.0.0","dependencies":{"tough-cookie":"4.1.2"}},"node_modules/tough-cookie":{"version":"4.1.2","resolved":"https://registry.npmjs.org/tough-cookie/-/tough-cookie-4.1.2.tgz"}}}
JSON
	run_check "$test_root/lower-severity"
	[ "$code" -eq 0 ] || fail "a MEDIUM finding failed the Gate: $output"
	case $output in
	*MEDIUM*CVE-2023-26136*) ;;
	*) fail "expected the known MEDIUM vulnerability to be printed: $output" ;;
	esac
	printf 'ok: a MEDIUM finding is printed without failing the Gate\n'
}

random_alnum() {
	dd if=/dev/urandom bs=48 count=1 2>/dev/null | base64 | tr -dc 'A-Za-z0-9' | cut -c1-"$1"
}

case_secret() {
	fake=$(random_alnum 32)
	mkdir -p "$test_root/secret"
	printf 'apiToken = %s\n' "$fake" >"$test_root/secret/settings.txt"
	run_check "$test_root/secret"
	[ "$code" -eq 0 ] || fail "Trivy reported a secret or failed to scan the fixture: $output"
	case $output in
	*"$fake"* | *unexpected-secret*) fail "Trivy reported the fixture secret: $output" ;;
	esac
	run_command gitleaks dir --config "$root/.gitleaks.toml" --redact --verbose --no-banner "$test_root/secret"
	[ "$code" -ne 0 ] || fail "gitleaks did not catch the fixture secret"
	case $output in
	*RuleID:*) ;;
	*) fail "gitleaks did not name its matching rule: $output" ;;
	esac
	case $output in
	*"$fake"*) fail "gitleaks printed the fabricated secret" ;;
	esac
	printf 'ok: Trivy ignores secrets while gitleaks catches them\n'
}

case_explained_ignore() {
	write_vulnerable_dependency "$test_root/explained-ignore"
	cat >"$test_root/explained-ignore/.trivyignore" <<'IGNORE'
CVE-2021-23337 # fixture dependency is never executed
IGNORE
	run_check "$test_root/explained-ignore"
	case $output in
	*CVE-2021-23337*) fail "an explained .trivyignore entry did not suppress its finding: $output" ;;
	esac
	printf 'ok: an explained .trivyignore entry suppresses its finding\n'
}

case_db_failure() {
	mkdir -p "$test_root/db-failure"
	run_command env \
		TRIVY_CACHE_DIR="$test_root/db-failure-cache" \
		HTTPS_PROXY=http://127.0.0.1:1 \
		HTTP_PROXY=http://127.0.0.1:1 \
		ALL_PROXY=http://127.0.0.1:1 \
		NO_PROXY= \
		"$checker" "$test_root/db-failure"
	[ "$code" -ne 0 ] || fail "a vulnerability DB download failure passed"
	case $output in
	*"vulnerability DB"*) ;;
	*) fail "expected a clear vulnerability DB diagnostic: $output" ;;
	esac
	printf 'ok: a vulnerability DB download failure fails clearly\n'
}

case_cache_outside_repo() {
	mkdir -p "$test_root/cache-location/repo"
	run_command env TRIVY_CACHE_DIR="$test_root/cache-location/cache" "$checker" "$test_root/cache-location/repo"
	[ "$code" -eq 0 ] || fail "a clean fixture with an external cache failed: $output"
	[ -d "$test_root/cache-location/cache" ] || fail "the external Trivy cache was not used"
	run_command env TRIVY_CACHE_DIR="$test_root/cache-location/repo/cache" "$checker" "$test_root/cache-location/repo"
	[ "$code" -ne 0 ] || fail "a cache inside the repository was accepted"
	case $output in
	*"cache directory must be outside the repository"*) ;;
	*) fail "expected an external-cache diagnostic: $output" ;;
	esac
	[ ! -e "$test_root/cache-location/repo/cache" ] || fail "a rejected cache was created inside the fixture"
	printf 'ok: Trivy caches outside the repository and refuses an in-repo cache\n'
}

case_all() {
	case_unexplained_ignore
	case_vulnerable_dependency
	case_lower_severity
	case_secret
	case_explained_ignore
	case_db_failure
	case_cache_outside_repo
}

case $case_name in
unexplained-ignore) case_unexplained_ignore ;;
vulnerable-dependency) case_vulnerable_dependency ;;
lower-severity) case_lower_severity ;;
secret) case_secret ;;
explained-ignore) case_explained_ignore ;;
db-failure) case_db_failure ;;
cache-outside-repo) case_cache_outside_repo ;;
all) case_all ;;
*)
	printf 'unknown case: %s\n' "$case_name" >&2
	exit 2
	;;
esac
