#!/usr/bin/env bash
# The session recorded into docs/assets/demo.cast and rendered to docs/assets/demo.svg.
# Regenerate both with `make demo-svg` from the repository root.
#
# Nothing here is faked: every command really runs, in a throwaway directory, and what
# you see under it is that command's own output. The prompt is a bare "$ " and the
# working directory is a temp folder, so no username, hostname or home path is recorded.
set -e

# The violet the README pictures use, as a 24 bit colour so it survives any terminal theme.
ACCENT='\033[38;2;155;133;255m'
PLAIN='\033[0m'

# Print the command one character at a time, so the recording reads like somebody typing.
type_line() {
    local text=$1 i
    printf "${ACCENT}\$${PLAIN} "
    for (( i = 0; i < ${#text}; i++ )); do
        printf '%s' "${text:i:1}"
        sleep 0.016
    done
    printf '\n'
}

run() {
    type_line "$1"
    sleep 0.35
    eval "$1"
    sleep "${2:-1.1}"
}

cd "$(mktemp -d)"
sleep 0.6

run 'tinycue init coffee' 1.3
run 'tinycue train coffee.yaml --extra coffee.extra.yaml --dev coffee.dev.yaml -o out/model' 1.3
run 'tinycue parse out/model "make me two lattes"'
run 'tinycue parse out/model "teen cup chai bana do"'
run 'tinycue parse out/model "who won the match last night"'
run 'tinycue parse out/model "kindly cease the brewing apparatus"' 2.6
