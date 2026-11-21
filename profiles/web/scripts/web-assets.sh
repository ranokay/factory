#!/bin/sh
set -eu

status=0

fail() {
	echo "web-assets: $1" >&2
	status=1
}

valid_name() {
	case "$1" in
	'' | *[!a-z0-9-]* | -* | *- | *--*) return 1 ;;
	esac
	return 0
}

check_directories() {
	directories=$1
	label=$2
	old_ifs=$IFS
	IFS=/
	# shellcheck disable=SC2086
	# Splitting the path on IFS=/ is the point.
	set -- $directories
	IFS=$old_ifs
	for segment in "$@"; do
		valid_name "$segment" || fail "$label has a directory that is not lowercase kebab-case"
	done
}

for path in "$@"; do
	relative=${path#./}
	case "$relative" in
	public/assets/*) ;;
	*) continue ;;
	esac
	within=${relative#public/assets/}
	case "$within" in
	*/*) check_directories "${within%/*}" "$relative" ;;
	esac
	filename=${within##*/}
	case "$filename" in
	*.svg | *.webp | *.avif) ;;
	*)
		fail "$relative must be .svg, .webp or .avif"
		continue
		;;
	esac
	valid_name "${filename%.*}" || fail "$relative has a name that is not lowercase kebab-case"
done

exit "$status"
