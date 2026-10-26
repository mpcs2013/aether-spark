#!/usr/bin/env bash
# Lints L1-L3 (docs/architecture/egress.md, "Compose and config lint"). Needs Docker; no internet
# beyond the proxy image build. The rule load (L3) needs root on Linux: CI runs this with sudo; on
# the desktop it is skipped. The fixture tests of L1 and L2 run with pytest (deploy/compose/tests/).
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1

dc() { docker compose -f deploy/compose/egress/compose.yaml "$@"; }
# uv on the desktop; the system Python under sudo (lint.py uses the standard library only)
if command -v uv >/dev/null 2>&1; then py=(uv run --quiet python); else py=(python3); fi
fails=0

step() { # [-s] description, command...; prints the output on failure, and with -s also on success
    local show=0 d out
    if [ "$1" = -s ]; then show=1; shift; fi
    d=$1
    shift
    if out=$("$@" 2>&1); then
        printf 'PASS  %s\n' "$d"
        if [ "$show" -eq 1 ]; then printf '%s\n' "$out" | sed 's/^/      /'; fi
    else
        printf 'FAIL  %s\n%s\n' "$d" "$out"; fails=$((fails + 1))
    fi
}

# L1: the project as Compose renders it, every profile
l1() { dc --profile '*' config --format json | "${py[@]}" deploy/compose/lint.py compose -; }
step -s "L1 rendered Compose" l1

# L2: each proxy's allowlist file equals the design note's table
step -s "L2 allowlists" "${py[@]}" deploy/compose/lint.py allowlists

# L3: Squid parses each configuration with its real mounts, in a freshly built image
parse() { dc -f tests/egress/compose.lint.yaml --profile '*' run --rm --no-deps --build -T "$1" parse; }
step "L3 squid -k parse, runtime proxy" parse egress-runtime
step "L3 squid -k parse, ops proxy" parse egress-ops
# The check itself: a configuration error must fail the parse
broken() { ! printf 'no_such_directive on\n' \
    | docker run --rm -i --network none --entrypoint squid aetherspark/egress-proxy:local -k parse -f /dev/stdin; }
step "L3 a broken configuration fails the parse" broken

# L3: the host rules, rendered from the example settings, load in a scratch network namespace
if [ "$(uname -s)" != Linux ]; then
    echo "SKIP  L3 host rules (Linux only; CI runs it)"
elif [ "$(id -u)" -ne 0 ]; then
    echo "FAIL  L3 host rules: needs root (sudo bash tests/egress/lint.sh)"; fails=$((fails + 1))
else
    step -s "L3 host rules load in a scratch namespace" bash deploy/host/egress-rules.sh lint
fi

echo "lint: $fails failed"
[ "$fails" -eq 0 ]
