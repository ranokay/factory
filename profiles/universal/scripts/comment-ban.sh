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

A comment is commented-out code when its body is name=value, a known
workflow key (run:, uses:, jobs: and the like), a YAML list item that
starts code, starts with a shell, build or language keyword (if, echo,
git, mise, const, import, return, SELECT and the like), starts with a
./path or flag (./build.sh, --release), is [table], starts a call (name(
or receiver.name(), or ends in ;, { or }.

Documentation comments (///, //!, and /** ... */ lines in a file whose
line-comment marker is //) are not inspected for code, though banned
markers still fail.

A plain line comment (#, //, ; or --) that continues a multi-line
comment block is prose when it has no code operators and carries
sentence punctuation. A continuation ending in .:,!? needs three or more
words, one ending in ; needs four, and one with internal . ; or , needs
four. Known workflow keys are code unless the line reads as prose. A
comment ending in a flag is code.

A PEP 723 script block in a .py file (a comment line /// script through the
next ///) is executable script metadata, not commented-out code, and is
skipped; an unterminated block fails.

Exempt tool directives: shellcheck, zizmor:, typos:, yamllint, pinact,
oxlint-, biome-, eslint-, prettier-ignore, shfmt, tombi, editorconfig,
yaml-language-server: and NOSONAR.
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
	*.sh | *.bash | *.zsh | *.bats | *.py | *.rb | *.toml | *.yml | *.yaml | *.conf | *.mk | Makefile | */Makefile | .gitignore | */.gitignore)
		markers='#'
		block_start=''
		block_end=''
		;;
	*.ini | *.properties | .editorconfig | */.editorconfig)
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
			return s ~ /^(shellcheck|zizmor:|typos:|yamllint|pinact|oxlint-|biome-|eslint-|prettier-ignore|shfmt|tombi|editorconfig|yaml-language-server:|NOSONAR(:|[ \t]|$))/
		}

		function known_key(s) {
			return s ~ /^(on|push|pull_request|workflow_call|jobs|steps|uses|run|name|with|permissions|contents|version|timeout-minutes|env|id|needs|concurrency|group|cancel-in-progress|runs-on):[ \t]/
		}

		function is_prose(s,    n, words) {
			if (s ~ /[(){}=$"`]/) return 0
			if (s ~ /\[/ || s ~ /\]/) return 0
			if (s ~ /(^|[ \t])(\.\/|->|=>|&&|\|\|)/) return 0
			n = split(s, words, /[ \t]+/)
			if (s ~ /;[ \t]*$/) return n >= 4
			if (s ~ /[.:,!?][ \t]*$/) return n >= 3
			if (s ~ /[.;,][ \t]+[A-Za-z]/) return n >= 4
			return 0
		}

		function marker_at(line, i, word_boundary,    k, m, prev) {
			for (k = 1; k <= count; k++) {
				m = marker[k]
				if (substr(line, i, length(m)) != m) continue
				if (i == 1 || !word_boundary) return length(m)
				prev = substr(line, i - 1, 1)
				if (prev ~ /[ \t;&|=,]/) return length(m)
			}
			return 0
		}

		function starts_comment_line(line,    s) {
			s = trim(line)
			return marker_at(s, 1, 0) > 0
		}

		function keyed_code(s,    v) {
			if (s !~ /^[A-Za-z0-9_.-]+:[ \t]/) return 0
			if (known_key(s)) return 1
			v = s
			sub(/^[A-Za-z0-9_.-]+:[ \t]+/, "", v)
			if (is_code(v)) return 1
			if (v ~ /^(\$[A-Za-z_{(]|\{\{)/) return 1
			if (v ~ /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.\/-]+@[^ \t]+$/) return 1
			return 0
		}

		function is_code(s,    v) {
			if (s ~ /^(if|then|else|elif|fi|for|while|do|done|case|esac|function|def|class|import|from|return|export|local|const|let|var|set|echo|printf|cd|git|gh|npm|bun|pnpm|yarn|uv|pip|brew|mise|docker|kubectl|curl|wget|sudo|rm|cp|mv|mkdir|touch|source|exec|await|async|throw|raise|try|catch|finally|switch|package|type|interface|struct|enum|public|private|protected|static|final|void|int|string|bool|SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP)([ \t]|$)/) return 1
			if (s ~ /^[A-Za-z_$][A-Za-z0-9_.$]*\(/) return 1
			if (s ~ /(^|[ \t])-{1,2}[A-Za-z][A-Za-z0-9-]*[ \t]*$/) return 1
			if (s ~ /^[A-Za-z_][A-Za-z0-9_]*[ \t]*=[^=]/) return 1
			if (keyed_code(s)) return 1
			if (s ~ /^-[ \t]+/) {
				v = s
				sub(/^-[ \t]+/, "", v)
				if (is_code(v)) return 1
			}
			if (s ~ /^(-{1,2}[A-Za-z]|\.\/[^ \t])/) return 1
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
			if (FILENAME ~ /\.py$/ && s == "/// script") {
				inpep = 1
				return
			}
			if (inpep) {
				if (s == "///") inpep = 0
				return
			}
			if (s ~ /(^|[^A-Za-z0-9_])(TODO|FIXME|HACK|XXX)([^A-Za-z0-9_]|$)/) {
				printf "%s:%d: banned comment marker: %s\n", FILENAME, FNR, s
				failed = 1
				return
			}
			if (exempt(s)) return
			if (docblock || doc_comment) return
			if (!inblock && is_continuation && is_prose(s)) return
			if (is_code(s)) {
				printf "%s:%d: commented-out code: %s\n", FILENAME, FNR, s
				failed = 1
			}
		}

		BEGIN {
			count = split(markers, marker, " ")
			slashes = markers == "//"
			inpep = 0
			inblock = 0
			docblock = 0
			doc_comment = 0
			prev_comment = 0
			is_continuation = 0
		}

		{
			line = $0
			doc_comment = 0
			if (FNR == 1 && substr(line, 1, 2) == "#!") next
			starts_comment = starts_comment_line(line)
			is_continuation = prev_comment && starts_comment
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
						docblock = 0
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
					docblock = slashes && substr(line, i, 3) == "/**"
					i += length(block_start)
					continue
				}
				mlen = marker_at(line, i, 1)
				if (mlen > 0) {
					doc_comment = slashes && (substr(line, i + mlen, 1) == "/" || substr(line, i + mlen, 1) == "!")
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
			prev_comment = starts_comment
		}

		END {
			if (docblock) {
				printf "%s: unterminated documentation block\n", FILENAME
				failed = 1
			}
			if (inpep) {
				printf "%s: unterminated PEP 723 script block\n", FILENAME
				failed = 1
			}
			exit failed ? 1 : 0
		}
	' "$file"; then
		:
	else
		status=1
	fi
done

exit "$status"
