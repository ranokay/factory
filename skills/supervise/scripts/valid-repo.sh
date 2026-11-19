#!/bin/sh

valid_repo() {
	case $1 in
	*/*/* | /* | */ | *[!A-Za-z0-9_./-]*) return 1 ;;
	esac
	case $1 in
	*..* | .* | */.*) return 1 ;;
	esac
	return 0
}
