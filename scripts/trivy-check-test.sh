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

write_dependency() {
	mkdir -p "$1"
	cat >"$1/package.json" <<JSON
{"name":"trivy-fixture","version":"1.0.0","dependencies":{"$2":"$3"}}
JSON
	cat >"$1/package-lock.json" <<JSON
{"name":"trivy-fixture","version":"1.0.0","lockfileVersion":3,"requires":true,"packages":{"":{"name":"trivy-fixture","version":"1.0.0","dependencies":{"$2":"$3"}},"node_modules/$2":{"version":"$3","resolved":"https://registry.npmjs.org/$2/-/$2-$3.tgz"}}}
JSON
}

case_vulnerable_dependency() {
	write_dependency "$test_root/vulnerable-dependency" lodash 4.17.20
	run_check "$test_root/vulnerable-dependency"
	[ "$code" -ne 0 ] || fail "a known-vulnerable pinned dependency passed"
	case $output in
	*HIGH*CVE-2021-23337*) ;;
	*) fail "expected the known HIGH vulnerability: $output" ;;
	esac
	printf 'ok: a known-vulnerable pinned dependency fails the Gate\n'
}

case_repository_config() {
	write_dependency "$test_root/repository-config" lodash 4.17.20
	printf '%s\n' 'severity:' '  - CRITICAL' >"$test_root/repository-config/trivy.yaml"
	run_check "$test_root/repository-config"
	[ "$code" -ne 0 ] || fail "trivy.yaml raised the Gate threshold above HIGH"
	case $output in
	*HIGH*CVE-2021-23337*) ;;
	*) fail "trivy.yaml hid a HIGH finding: $output" ;;
	esac
	printf 'ok: repository config cannot raise the HIGH/CRITICAL threshold\n'
}

case_lower_severity() {
	write_dependency "$test_root/lower-severity" tough-cookie 4.1.2
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
	write_dependency "$test_root/explained-ignore" tough-cookie 4.1.2
	cat >"$test_root/explained-ignore/.trivyignore" <<'IGNORE'
CVE-2023-26136 # fixture dependency is never executed
IGNORE
	run_check "$test_root/explained-ignore"
	[ "$code" -eq 0 ] || fail "an explained .trivyignore entry did not produce a clean scan: $output"
	case $output in
	*CVE-2023-26136*) fail "an explained .trivyignore entry did not suppress its finding: $output" ;;
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

case_malformed_report() {
	mkdir -p "$test_root/malformed-report/repo" "$test_root/malformed-report/bin"
	cat >"$test_root/malformed-report/bin/trivy" <<'SH'
#!/bin/sh
printf '%s\n' '{}'
SH
	chmod +x "$test_root/malformed-report/bin/trivy"
	run_command env PATH="$test_root/malformed-report/bin:$PATH" "$checker" "$test_root/malformed-report/repo"
	[ "$code" -ne 0 ] || fail "a malformed Trivy report passed as clean"
	case $output in
	*"invalid JSON report: missing numeric SchemaVersion"*) ;;
	*) fail "expected a malformed-report diagnostic: $output" ;;
	esac
	printf 'ok: a malformed Trivy report fails clearly\n'
}

case_scan_failure() {
	mkdir -p "$test_root/scan-failure/repo" "$test_root/scan-failure/bin"
	cat >"$test_root/scan-failure/bin/trivy" <<'SH'
#!/bin/sh
printf '%s\n' '{"SchemaVersion":2}'
printf '%s\n' '2026-11-26T00:00:00Z FATAL TLS handshake timeout while fetching the vulnerability DB' >&2
SH
	chmod +x "$test_root/scan-failure/bin/trivy"
	run_command env PATH="$test_root/scan-failure/bin:$PATH" "$checker" "$test_root/scan-failure/repo"
	[ "$code" -ne 0 ] || fail "a fatal Trivy diagnostic passed with an empty report"
	case $output in
	*"scan reported a fatal or analysis diagnostic"*) ;;
	*) fail "expected a fatal-scan diagnostic: $output" ;;
	esac
	printf 'ok: a fatal Trivy diagnostic fails even with a valid empty report\n'
}

case_nonfatal_error() {
	mkdir -p "$test_root/nonfatal-error/repo" "$test_root/nonfatal-error/bin"
	cat >"$test_root/nonfatal-error/bin/trivy" <<'SH'
#!/bin/sh
printf '%s\n' '{"SchemaVersion":2}'
printf '%s\n' '2026-11-26T00:00:00Z ERROR [npm] npm error optional install output' >&2
SH
	chmod +x "$test_root/nonfatal-error/bin/trivy"
	run_command env PATH="$test_root/nonfatal-error/bin:$PATH" "$checker" "$test_root/nonfatal-error/repo"
	[ "$code" -eq 0 ] || fail "a nonfatal Trivy ERROR failed a clean scan: $output"
	printf 'ok: a nonfatal Trivy ERROR does not fail a clean scan\n'
}

case_parse_failure() {
	mkdir -p "$test_root/parse-failure/repo" "$test_root/parse-failure/bin"
	cat >"$test_root/parse-failure/bin/trivy" <<'SH'
#!/bin/sh
printf '%s\n' '{"SchemaVersion":2}'
printf '%s\n' '2026-11-26T00:00:00Z ERROR [npm] failed to analyze package-lock.json' >&2
SH
	chmod +x "$test_root/parse-failure/bin/trivy"
	run_command env PATH="$test_root/parse-failure/bin:$PATH" "$checker" "$test_root/parse-failure/repo"
	[ "$code" -ne 0 ] || fail "a dependency parse failure passed with an empty report"
	case $output in
	*"scan reported a fatal or analysis diagnostic"*) ;;
	*) fail "expected an analysis-failure diagnostic: $output" ;;
	esac
	printf 'ok: a dependency parse or analysis failure fails closed\n'
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
	mkdir -p "$test_root/cache-location/repo/symlink-target"
	ln -s "$test_root/cache-location/repo/symlink-target" "$test_root/cache-location/cache-link"
	run_command env TRIVY_CACHE_DIR="$test_root/cache-location/cache-link" "$checker" "$test_root/cache-location/repo"
	[ "$code" -ne 0 ] || fail "a symlinked cache inside the repository was accepted"
	case $output in
	*"cache directory must be outside the repository"*) ;;
	*) fail "expected a physical external-cache diagnostic: $output" ;;
	esac
	[ -z "$(ls -A "$test_root/cache-location/repo/symlink-target")" ] || fail "a rejected symlink cache landed in the fixture"
	printf 'ok: Trivy caches outside the repository and refuses lexical or symlinked in-repo caches\n'
}

case_all() {
	case_unexplained_ignore
	case_vulnerable_dependency
	case_repository_config
	case_lower_severity
	case_secret
	case_explained_ignore
	case_db_failure
	case_malformed_report
	case_scan_failure
	case_nonfatal_error
	case_parse_failure
	case_cache_outside_repo
}

case $case_name in
unexplained-ignore) case_unexplained_ignore ;;
vulnerable-dependency) case_vulnerable_dependency ;;
repository-config) case_repository_config ;;
lower-severity) case_lower_severity ;;
secret) case_secret ;;
explained-ignore) case_explained_ignore ;;
db-failure) case_db_failure ;;
malformed-report) case_malformed_report ;;
scan-failure) case_scan_failure ;;
nonfatal-error) case_nonfatal_error ;;
parse-failure) case_parse_failure ;;
cache-outside-repo) case_cache_outside_repo ;;
all) case_all ;;
*)
	printf 'unknown case: %s\n' "$case_name" >&2
	exit 2
	;;
esac
