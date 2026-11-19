#!/bin/sh
# shellcheck disable=SC2034
# zizmor: ignore[unpinned-uses]
# typos: prepare
# yamllint disable-line rule:line-length
# pinact: ignore
unused=ok
count="$#"
total=$#
first="${1#-}"
last="${1##*-}"
prefix=${1#-}
all=${#count}
elements="${#count}"
url="https://example.com/#section"
endpoint=https://example.com/#anchor
literal='a # not a comment'
printf '%s %s %s %s %s %s %s %s %s %s %s\n' "$unused" "$count" "$total" "$first" "$last" "$prefix" "$all" "$elements" "$url" "$endpoint" "$literal"
