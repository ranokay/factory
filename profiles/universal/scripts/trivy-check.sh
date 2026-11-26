#!/bin/sh
set -eu

root=${1:-.}
root=$(CDPATH='' cd "$root" && pwd -P)
ignore="$root/.trivyignore"

if [ -f "$ignore" ]; then
	awk '
		{
			line = $0
			sub(/\r$/, "", line)
			sub(/^[[:space:]]+/, "", line)
			sub(/[[:space:]]+$/, "", line)
			if (line == "" || line ~ /^#/) next
			hash = index(line, "#")
			reason = hash ? substr(line, hash + 1) : ""
			id = hash ? substr(line, 1, hash - 1) : line
			gsub(/[[:space:]]+/, " ", id)
			sub(/^ /, "", id)
			sub(/ $/, "", id)
			gsub(/[[:space:]]+/, " ", reason)
			sub(/^ /, "", reason)
			sub(/ $/, "", reason)
			if (id == "" || reason == "") {
				printf "trivy: %s:%d: ignore entry requires a reason after #\n", FILENAME, FNR > "/dev/stderr"
				bad = 1
			}
		}
		END { if (bad) exit 1 }
	' "$ignore"
fi

cache_dir=${TRIVY_CACHE_DIR:-}
if [ -z "$cache_dir" ]; then
	if [ -n "${XDG_CACHE_HOME:-}" ]; then
		cache_dir="$XDG_CACHE_HOME/trivy"
	elif [ "$(uname -s)" = Darwin ]; then
		cache_dir="${HOME:?HOME is required for the Trivy cache}/Library/Caches/trivy"
	else
		cache_dir="${HOME:?HOME is required for the Trivy cache}/.cache/trivy"
	fi
fi
case $cache_dir in
/*) ;;
*) cache_dir="$PWD/$cache_dir" ;;
esac
cache_dir=$(node -e 'process.stdout.write(require("node:path").resolve(process.argv[1]))' "$cache_dir")
cache_tail=$(basename "$cache_dir")
cache_parent=$(dirname "$cache_dir")
while [ ! -d "$cache_parent" ]; do
	cache_tail="$(basename "$cache_parent")/$cache_tail"
	cache_parent=$(dirname "$cache_parent")
done
cache_dir="$(CDPATH='' cd "$cache_parent" && pwd -P)/$cache_tail"
case $cache_dir in
"$root" | "$root"/*)
	printf 'trivy: cache directory must be outside the repository: %s\n' "$cache_dir" >&2
	exit 1
	;;
esac
mkdir -p "$cache_dir"
cache_dir=$(CDPATH='' cd "$cache_dir" && pwd -P)
case $cache_dir in
"$root" | "$root"/*)
	printf 'trivy: cache directory must be outside the repository: %s\n' "$cache_dir" >&2
	exit 1
	;;
esac

report=$(mktemp "$cache_dir/factory-trivy-report.XXXXXX")
log=$(mktemp "$cache_dir/factory-trivy-log.XXXXXX")
ignorefile=$(mktemp "$cache_dir/factory-trivy-ignore.XXXXXX")
trap 'rm -f "$report" "$log" "$ignorefile"' EXIT HUP INT TERM

set -- --cache-dir "$cache_dir" \
	--scanners vuln,misconfig \
	--severity UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL \
	--format json \
	--exit-code 0 \
	--no-progress \
	--skip-version-check
if [ -f "$ignore" ]; then
	# Trivy treats extra legacy-ignore fields as expiration dates; reasons stay in the repository's file.
	# https://github.com/aquasecurity/trivy/blob/main/pkg/result/ignore.go
	sed 's/[[:space:]]*#.*$//' "$ignore" >"$ignorefile"
	set -- "$@" --ignorefile "$ignorefile"
fi

if trivy fs "$root" "$@" >"$report" 2>"$log"; then
	:
else
	code=$?
	[ "$code" -ne 0 ] || code=1
	cat "$log" >&2
	printf 'trivy: filesystem scan failed (exit %s); check the vulnerability DB and checks bundle download; cache: %s\n' "$code" "$cache_dir" >&2
	exit "$code"
fi
cat "$log" >&2
if grep -Ei '(failed|failure|error).*download.*(DB|database|checks bundle)|(DB|database|checks bundle).*download.*(failed|failure|error)' "$log" >/dev/null; then
	printf 'trivy: vulnerability DB or checks bundle download failed; cache: %s\n' "$cache_dir" >&2
	exit 1
fi

node - "$report" <<'NODE'
const fs = require('node:fs')

let report
try {
	report = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'))
	if (report === null || typeof report !== 'object' || Array.isArray(report) || typeof report.SchemaVersion !== 'number') {
		throw new Error('missing numeric SchemaVersion')
	}
	if (report.Results !== undefined && !Array.isArray(report.Results)) {
		throw new Error('Results is not an array')
	}
} catch (error) {
	console.error(`trivy: invalid JSON report: ${error.message}`)
	process.exit(1)
}

const clean = (value) => String(value ?? '').replace(/\s+/g, ' ').trim()
let findings = 0
let blocking = 0

for (const result of report.Results ?? []) {
	for (const finding of result.Vulnerabilities ?? []) {
		findings++
		const severity = clean(finding.Severity).toUpperCase()
		if (severity === 'HIGH' || severity === 'CRITICAL') blocking++
		console.log([
			'vulnerability', severity, clean(finding.VulnerabilityID),
			clean(result.Target), clean(finding.PkgName),
			clean(finding.InstalledVersion), `fixed:${clean(finding.FixedVersion) || 'none'}`,
			clean(finding.Title),
		].join('\t'))
	}
	for (const finding of result.Misconfigurations ?? []) {
		findings++
		const severity = clean(finding.Severity).toUpperCase()
		if (severity === 'HIGH' || severity === 'CRITICAL') blocking++
		console.log([
			'misconfiguration', severity, clean(finding.ID),
			clean(result.Target), clean(finding.Title), clean(finding.Message),
		].join('\t'))
	}
	for (const finding of result.Secrets ?? []) {
		findings++
		blocking++
		console.log(['unexpected-secret', clean(finding.RuleID), clean(result.Target)].join('\t'))
	}
}

if (findings === 0) {
	console.log('trivy: no vulnerabilities or misconfigurations')
} else {
	console.log(`trivy: ${findings} finding(s), ${blocking} HIGH/CRITICAL or unexpected secret(s)`)
}
process.exit(blocking === 0 ? 0 : 1)
NODE
