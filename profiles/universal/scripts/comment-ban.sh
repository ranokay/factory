#!/bin/sh
set -eu

usage() {
	cat <<'EOF'
Usage: comment-ban.sh FILE...

Fails on banned markers and commented-out code in source comments.

Scanned file types: shell, Python, Ruby, Pkl, JavaScript/TypeScript, Go,
Rust, Swift, C-family, Java/Kotlin/Scala, Lua/SQL, CSS/SCSS, HTML/XML/SVG,
TOML, YAML, INI/properties, Make, .gitignore and .editorconfig. Markdown
is never scanned.

A comment is a marker that starts a word: #, // or --, ; in INI-style
files, plus the block forms /* ... */ and <!-- ... -->. Quoted strings,
shebangs, $#, ${x#prefix}, ${#array[@]} and URLs such as
https://host/#anchor are not comments.

Banned markers are case-sensitive: TODO, FIXME, HACK, XXX.

A comment is commented-out code when its body starts with a shell,
build or language keyword (if, echo, git, mise, const, import, return,
SELECT and the like), is name=value, is [table], or ends in ;, { or }.

Exempt tool directives: shellcheck, zizmor:, typos:, yamllint, pinact,
oxlint-, biome-, eslint-, prettier-ignore, shfmt, tombi, editorconfig.
EOF
}

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
	usage
	exit 0
fi

[ "$#" -eq 0 ] && exit 0

status=0

for file in "$@"; do
	case "$file" in
	*.md | *.markdown) continue ;;
	*.sh | *.bash | *.zsh | *.bats | *.py | *.rb | *.toml | *.yml | *.yaml | *.conf | *.mk | Makefile | .gitignore)
		markers='#'
		block_start=''
		block_end=''
		;;
	*.ini | *.properties | .editorconfig)
		markers='# ;'
		block_start=''
		block_end=''
		;;
	*.pkl | *.js | *.jsx | *.ts | *.tsx | *.mjs | *.cjs | *.go | *.rs | *.swift | *.c | *.h | *.cc | *.cpp | *.hpp | *.java | *.kt | *.kts | *.scala)
		markers='//'
		block_start='/*'
		block_end='*/'
		;;
	*.css | *.scss)
		markers=''
		block_start='/*'
		block_end='*/'
		;;
	*.html | *.xml | *.svg)
		markers=''
		block_start='<!--'
		block_end='-->'
		;;
	*.lua | *.sql)
		markers='--'
		block_start=''
		block_end=''
		;;
	*) continue ;;
	esac

	if awk \
		-v markers="$markers" \
		-v block_start="$block_start" \
		-v block_end="$block_end" '
		function trim(s) {
			sub(/^[ \t]+/, "", s)
			sub(/[ \t]+$/, "", s)
			return s
		}

		function exempt(s) {
			return s ~ /^(shellcheck|zizmor:|typos:|yamllint|pinact|oxlint-|biome-|eslint-|prettier-ignore|shfmt|tombi|editorconfig)/
		}

		function is_code(s) {
			if (s ~ /^(if|then|else|elif|fi|for|while|do|done|case|esac|function|def|class|import|from|return|export|local|const|let|var|set|echo|printf|cd|git|gh|npm|bun|pnpm|yarn|uv|pip|brew|mise|docker|kubectl|curl|wget|sudo|rm|cp|mv|mkdir|touch|source|exec|await|async|throw|raise|try|catch|finally|switch|package|type|interface|struct|enum|public|private|protected|static|final|void|int|string|bool|SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP)([ \t]|$)/) return 1
			if (s ~ /^[A-Za-z_][A-Za-z0-9_]*[ \t]*=[^=]/) return 1
			if (s ~ /^\[[^]]*\][ \t]*$/) return 1
			if (s ~ /[;{}][ \t]*$/) return 1
			if (s ~ /^<\/?[A-Za-z]/) return 1
			return 0
		}

		function inspect(text,    s) {
			s = trim(text)
			sub(/^\*+[ \t]*/, "", s)
			sub(/[ \t]*\*+\/[ \t]*$/, "", s)
			if (s == "") return
			if (s ~ /(^|[^A-Za-z0-9_])(TODO|FIXME|HACK|XXX)([^A-Za-z0-9_]|$)/) {
				printf "%s:%d: banned comment marker: %s\n", FILENAME, FNR, s
				failed = 1
				return
			}
			if (exempt(s)) return
			if (is_code(s)) {
				printf "%s:%d: commented-out code: %s\n", FILENAME, FNR, s
				failed = 1
			}
		}

		function marker_length(line, i,    k, m, prev) {
			for (k = 1; k <= count; k++) {
				m = marker[k]
				if (substr(line, i, length(m)) != m) continue
				if (i == 1) return length(m)
				prev = substr(line, i - 1, 1)
				if (prev ~ /[ \t;&|=,]/) return length(m)
			}
			return 0
		}

		BEGIN {
			count = split(markers, marker, " ")
		}

		{
			line = $0
			if (FNR == 1 && substr(line, 1, 2) == "#!") next
			n = length(line)
			i = 1
			inq = ""
			while (i <= n) {
				if (inblock) {
					p = index(substr(line, i), block_end)
					if (p == 0) {
						inspect(substr(line, i))
						i = n + 1
					} else {
						inspect(substr(line, i, p - 1))
						i += p - 1 + length(block_end)
						inblock = 0
					}
					continue
				}
				c = substr(line, i, 1)
				if (inq != "") {
					if (c == "\\") {
						i += 2
						continue
					}
					if (c == inq) inq = ""
					i++
					continue
				}
				if (block_start != "" && substr(line, i, length(block_start)) == block_start) {
					inblock = 1
					i += length(block_start)
					continue
				}
				mlen = marker_length(line, i)
				if (mlen > 0) {
					inspect(substr(line, i + mlen))
					i = n + 1
					continue
				}
				if (c == "\"" || c == "\047" || c == "`") {
					inq = c
					i++
					continue
				}
				i++
			}
		}

		END {
			exit failed ? 1 : 0
		}
	' "$file"; then
		:
	else
		status=1
	fi
done

exit "$status"
