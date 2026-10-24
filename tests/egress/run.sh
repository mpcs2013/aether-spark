#!/usr/bin/env bash
# Egress tests (docs/architecture/egress.md, "Tests"): layers 1 and 2. Layer-3 tests (E9, E16-E20)
# come with the host rules. Needs Docker and internet access; runs on the desktop and in CI.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1

dc() { docker compose -f deploy/compose/egress/compose.yaml -f tests/egress/compose.test.yaml "$@"; }
RT=http://172.30.10.2:3128   # runtime proxy
OP=http://172.30.20.2:3128   # ops proxy
fails=0

check() { # id, description, expected, actual
    if [ "$3" = "$4" ]; then printf 'PASS  %-4s %s\n' "$1" "$2"
    else printf 'FAIL  %-4s %s (expected %s, got %s)\n' "$1" "$2" "$3" "$4"; fails=$((fails + 1)); fi
}
# CONNECT status the proxy answered (200 tunnel accepted, 000 no proxy reachable)
connect() { dc exec -T "$1" curl -s -o /dev/null -m 15 -w '%{http_connect}' --proxy "$2" "${@:3}" 2>/dev/null; }
# A refused CONNECT on an ssl-bump port: Squid 6 accepts the tunnel (200), opens no upstream
# connection and serves its 403 inside TLS under the throwaway certificate. "200 403" is only
# possible from Squid itself; an upstream server would answer with its own status.
refused() { dc exec -T "$1" curl -s -k -o /dev/null -m 15 -w '%{http_connect} %{http_code}' --proxy "$2" "${@:3}" 2>/dev/null; }
# "fail" if the command fails, "ok" otherwise
outcome() { if dc exec -T "$@" >/dev/null 2>&1; then echo ok; else echo fail; fi; }

trap 'dc --profile egress-ops down --remove-orphans >/dev/null 2>&1' EXIT
dc --profile egress-ops down --remove-orphans >/dev/null 2>&1
out=$(dc up -d --build --wait egress-runtime client-runtime client-agent 2>&1) || { echo "$out"; echo "stack did not start"; exit 1; }

# E1 allowed host through the runtime proxy
check E1 "www.sec.gov via runtime proxy" 200 "$(connect client-runtime "$RT" https://www.sec.gov/)"

# E2 no way out without the proxy
check E2 "direct HTTPS by name" fail "$(outcome client-runtime curl -s -m 10 https://www.sec.gov/)"
check E2 "direct HTTPS by address" fail "$(outcome client-runtime curl -s -m 10 -k https://1.1.1.1/)"
check E2 "ICMP out" fail "$(outcome client-runtime ping -c 1 -W 3 1.1.1.1)"
check E2 "DNS to a public resolver" fail "$(outcome client-runtime nslookup www.sec.gov 1.1.1.1)"
check E2 "DNS over HTTPS" fail "$(outcome client-runtime curl -s -m 10 -k https://1.1.1.1/dns-query)"
check E2 "DNS over TLS (853)" fail "$(outcome client-runtime nc -z -w 5 1.1.1.1 853)"
check E2 "UDP 123 out" fail "$(outcome client-runtime nc -u -z -w 5 1.1.1.1 123)"
check E2 "UDP 443 out" fail "$(outcome client-runtime nc -u -z -w 5 1.1.1.1 443)"
check E2 "IPv6 out" fail "$(outcome client-runtime curl -s -m 10 -6 -k 'https://[2606:4700:4700::1111]/')"
# Nothing listens on the gateway here, so these two only show no answer; host services with a
# listener are tested by E16 (layer3.sh)
check E2 "bridge gateway, TCP 443" fail "$(outcome client-runtime nc -z -w 5 172.30.10.1 443)"
check E2 "bridge gateway as resolver" fail "$(outcome client-runtime nslookup www.sec.gov 172.30.10.1)"
# Docker Desktop's host names and host address (no such names or route on Linux, so these pass there too)
check E2 "host.docker.internal" fail "$(outcome client-runtime curl -s -m 10 -k https://host.docker.internal/)"
check E2 "gateway.docker.internal" fail "$(outcome client-runtime nslookup gateway.docker.internal)"
check E2 "Docker Desktop host address" fail "$(outcome client-runtime nc -z -w 5 192.168.65.254 443)"

# E3 host on no list; E4 an ops host on the runtime proxy
check E3 "unlisted host refused" "200 403" "$(refused client-runtime "$RT" https://example.com/)"
check E3 "refused host fails TLS without -k" fail "$(outcome client-runtime curl -s -m 15 --proxy "$RT" https://example.com/)"
check E4 "ops host refused on runtime proxy" "200 403" "$(refused client-runtime "$RT" https://github.com/)"

# E6 literals, ports, methods, odd names
check E6 "IPv4 literal" "200 403" "$(refused client-runtime "$RT" https://1.1.1.1/)"
check E6 "integer literal" "200 403" "$(refused client-runtime "$RT" https://16843009/)"
check E6 "IPv6 literal" "200 403" "$(refused client-runtime "$RT" -g 'https://[2606:4700:4700::1111]/')"
check E6 "allowed host, port 22" "200 403" "$(refused client-runtime "$RT" https://www.sec.gov:22/)"
check E6 "trailing dot" "200 403" "$(refused client-runtime "$RT" https://www.sec.gov./)"
check E6 "look-alike suffix" "200 403" "$(refused client-runtime "$RT" https://www.sec.gov.example.com/)"
check E6 "plain GET" 403 "$(dc exec -T client-runtime curl -s -o /dev/null -m 15 -w '%{http_code}' --proxy "$RT" http://www.sec.gov/ 2>/dev/null)"

# E6b CONNECT to an allowed host, TLS name of another: tunnel opens, TLS is terminated
dc exec -T client-runtime curl -s -o /dev/null -m 15 --proxy "$RT" \
    --connect-to example.com:443:www.sec.gov:443 https://example.com/ >/dev/null 2>&1
rc=$?
check E6b "other SNI inside an allowed tunnel" terminated "$( [ "$rc" -ne 0 ] && echo terminated || echo passed)"

# E7 no proxy network: neither proxy nor a direct route
check E7 "agent via runtime proxy" 000 "$(connect client-agent "$RT" https://www.sec.gov/)"
check E7 "agent direct" fail "$(outcome client-agent curl -s -m 10 https://www.sec.gov/)"
check E7 "agent to its bridge gateway" fail "$(outcome client-agent nc -z -w 5 172.30.90.1 443)"

# E8 no external names inside (a name that exists)
check E8 "DNS for www.sec.gov" fail "$(outcome client-runtime nslookup www.sec.gov)"

# E11 cache manager
check E11 "cache manager" 403 "$(dc exec -T client-runtime curl -s -o /dev/null -m 10 -w '%{http_code}' http://172.30.10.2:3128/squid-internal-mgr/info 2>/dev/null)"

# E5 ops proxy: allowed while up, unreachable for the runtime client, gone after down
dc --profile egress-ops up -d --wait egress-ops client-ops >/dev/null 2>&1
check E5 "github.com via ops proxy (up)" 200 "$(connect client-ops "$OP" https://github.com/)"
check E5 "runtime client cannot reach ops proxy" 000 "$(connect client-runtime "$OP" https://github.com/)"
check E7 "agent via ops proxy" 000 "$(connect client-agent "$OP" https://github.com/)"
dc --profile egress-ops stop egress-ops >/dev/null 2>&1
check E5 "ops proxy down" 000 "$(connect client-ops "$OP" https://github.com/)"

# E10 log lines with decision and SNI, no query strings
logs=$(dc logs --no-log-prefix egress-runtime 2>/dev/null)
check E10 "tunnel logged with SNI" yes "$(grep -q 'target=www.sec.gov:443 sni=www.sec.gov .*result=TCP_TUNNEL' <<<"$logs" && echo yes || echo no)"
check E10 "refusal logged" yes "$(grep -q 'target=example.com:443 .*result=TCP_DENIED' <<<"$logs" && echo yes || echo no)"
check E10 "terminated SNI logged" yes "$(grep -q 'target=www.sec.gov:443 sni=example.com .*down=0' <<<"$logs" && echo yes || echo no)"
check E10 "no query strings" no "$(grep -q 'target=[^ ]*?' <<<"$logs" && echo yes || echo no)"

echo "---"
if [ "$fails" -eq 0 ]; then echo "egress tests: all passed"; else echo "egress tests: $fails failed"; fi
exit $((fails > 0))
