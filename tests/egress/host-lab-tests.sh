#!/usr/bin/env bash
# Inside the lab container (tests/egress/host-lab.sh): the rule script in host mode against a LAN of
# network namespaces. The container's own namespace is the Spark; the router, the NAS, the admin
# desktop and another Home device sit on one bridge, and three Docker-style bridges hold stand-ins
# for the ops proxy, the runtime proxy and the stack collector. Every service answers "ok".
# Test ids as in docs/architecture/spark-host.md; B is the baseline, L3 the lint.
set -uo pipefail
R=/repo/deploy/host/egress-rules.sh
SPARK=192.0.2.10 ROUTER=192.0.2.1 NAS=192.0.2.20 DESKTOP=192.0.2.50 OTHER=192.0.2.60
PUBLIC=203.0.113.10 OPS=172.30.20.2 RUNTIME=172.30.10.2 COLLECTOR=172.30.40.2
fails=0

check() { # id, description, expected, actual
    if [ "$3" = "$4" ]; then printf 'PASS  %-4s %s\n' "$1" "$2"
    else printf 'FAIL  %-4s %s (expected %s, got %s)\n' "$1" "$2" "$3" "$4"; fails=$((fails + 1)); fi
}
# Every probe opens a new connection (existing ones would survive a rule change) and is judged by
# the answer it got, not by the client's exit status
answer() { if [ "$1" = ok ]; then echo ok; else echo fail; fi; }
tcp_out() { # user, address, port: ok when the Spark's connection gets the answer
    answer "$(timeout 6 runuser -u "$1" -- socat -T3 - "TCP4:$2:$3,connect-timeout=3" </dev/null 2>/dev/null)"
}
udp_out() { # user, address, port[, source port]
    answer "$(echo x | timeout 6 runuser -u "$1" -- socat -T3 - "UDP4:$2:$3${4:+,sourceport=$4}" 2>/dev/null)"
}
tcp_in() { # namespace, port: ok when a LAN device reaches the Spark
    answer "$(ip netns exec "$1" timeout 6 socat -T3 - "TCP4:$SPARK:$2,connect-timeout=3" </dev/null 2>/dev/null)"
}
check_finds() { # what check reports about the current state: found (it failed) or missed
    if bash "$R" check >/dev/null 2>&1; then echo missed; else echo found; fi
}
listen() { # namespace ("" for the Spark), tcp|udp, address, port
    local pre=()
    [ -n "$1" ] && pre=(ip netns exec "$1")
    if [ "$2" = tcp ]; then "${pre[@]}" socat "TCP4-LISTEN:$4,bind=$3,fork,reuseaddr" SYSTEM:'echo ok' 2>/dev/null &
    else "${pre[@]}" socat "UDP4-RECVFROM:$4,bind=$3,fork" SYSTEM:'echo ok' 2>/dev/null &
    fi
}

# --- The LAN ---------------------------------------------------------------------------------
ip link set lo up
ip netns add sw
ip -n sw link add br0 type bridge
ip -n sw link set br0 up
ip link add lan0 type veth peer name spark netns sw
ip -n sw link set spark master br0 up
ip addr add "$SPARK/24" dev lan0
ip link set lan0 up
ip route add default via "$ROUTER"
peer() { # name, address: a device on the LAN
    ip netns add "$1"
    ip -n sw link add "$1" type veth peer name eth0 netns "$1"
    ip -n sw link set "$1" master br0 up
    ip -n "$1" addr add "$2/24" dev eth0
    ip -n "$1" link set eth0 up
    ip -n "$1" link set lo up
}
peer router "$ROUTER"
peer nas "$NAS"
peer desktop "$DESKTOP"
peer other "$OTHER"
# A public server behind the router
ip -n router addr add "$PUBLIC/32" dev lo
dockerish() { # bridge, host address, container, container address: a Docker-style bridge
    ip link add "$1" type bridge
    ip addr add "$2/24" dev "$1"
    ip link set "$1" up
    ip netns add "$3"
    ip link add "$3" type veth peer name eth0 netns "$3"
    ip link set "$3" master "$1" up
    ip -n "$3" addr add "$4/24" dev eth0
    ip -n "$3" link set eth0 up
    ip -n "$3" link set lo up
    ip -n "$3" route add default via "$2"
}
dockerish as-eg-ops 172.30.20.1 opsproxy "$OPS"
dockerish as-eg-runtime 172.30.10.1 rtproxy "$RUNTIME"
dockerish as-obs-edge 172.30.40.1 collector "$COLLECTOR"

listen "" tcp 0.0.0.0 22
listen "" tcp 0.0.0.0 9100
listen router udp 0.0.0.0 67
listen router udp "$ROUTER" 53
listen router tcp "$ROUTER" 53
listen router udp "$ROUTER" 123
listen router tcp "$PUBLIC" 443
listen router udp "$PUBLIC" 53
listen nas tcp "$NAS" 3493
listen nas tcp "$NAS" 8002
listen opsproxy tcp "$OPS" 3128
listen rtproxy tcp "$RUNTIME" 3128
listen collector tcp "$COLLECTOR" 4317
listen collector tcp "$COLLECTOR" 9999
sleep 1
check LAB "the lab answers before any rule" ok "$(tcp_in desktop 9100)"

# --- Baseline: no settings yet, host mode on ---------------------------------------------------
mkdir -p /etc/aetherspark /etc/docker
touch /etc/aetherspark/host-mode
bash "$R" baseline
policies=$( { iptables -S; ip6tables -S; } | grep -cE '^-P (INPUT|OUTPUT|FORWARD) DROP$')
check B "baseline without settings: DROP policies, IPv4 and IPv6" 6 "$policies"
check B "SSH from the admin desktop under the baseline alone" fail "$(tcp_in desktop 22)"

# --- Apply ---------------------------------------------------------------------------------------
cat >/etc/aetherspark/egress-rules.env <<EOF
AS_RESOLVERS="$ROUTER"
AS_HOME_SUBNETS="198.51.100.0/24"
AS_NAS_ADDR="$NAS"
AS_NAS_PORT="8002"
AS_LAN_IF="lan0"
AS_ROUTER="$ROUTER"
AS_ADMIN_ADDR="$DESKTOP"
AS_UPS_PORT="3493"
AS_COLLECTOR="$COLLECTOR:4317"
AS_COLLECTOR_USER="otelcol"
EOF
printf '{"dns": ["%s"], "userland-proxy": false}\n' "$ROUTER" >/etc/docker/daemon.json
out=$(bash "$R" apply 2>&1)
check B "apply" "aetherspark-egress-rules: in place" "$(printf '%s\n' "$out" | tail -n 1)"

check S1 "SSH from the admin desktop" ok "$(tcp_in desktop 22)"
check S1 "SSH from another Home device" fail "$(tcp_in other 22)"
check S1 "another port from the admin desktop" fail "$(tcp_in desktop 9100)"
check S2 "DHCP renewal to the router (UDP 68 -> 67)" ok "$(udp_out root "$ROUTER" 67 68)"
check S2 "DHCP broadcast, the router's answer comes in" ok \
    "$(answer "$(echo x | timeout 6 socat -T3 - UDP4-DATAGRAM:255.255.255.255:67,broadcast,bind=0.0.0.0:68 2>/dev/null)")"
# The rule is tied to the client's port: the same broadcast from another port is refused
check S2 "DHCP broadcast from another source port" fail \
    "$(answer "$(echo x | timeout 6 socat -T3 - UDP4-DATAGRAM:255.255.255.255:67,broadcast,bind=0.0.0.0:6868 2>/dev/null)")"
check S3 "DNS (UDP) to the router as the resolver user" ok "$(udp_out systemd-resolve "$ROUTER" 53)"
check S3 "DNS (TCP) to the router as the resolver user" ok "$(tcp_out systemd-resolve "$ROUTER" 53)"
check S3 "DNS to the router as root" fail "$(udp_out root "$ROUTER" 53)"
check S3 "DNS to another server as the resolver user" fail "$(udp_out systemd-resolve "$PUBLIC" 53)"
check S4 "NTP to the router as the time user" ok "$(udp_out systemd-timesync "$ROUTER" 123)"
check S4 "NTP to the router as root" fail "$(udp_out root "$ROUTER" 123)"
check S5 "ops proxy as root" ok "$(tcp_out root "$OPS" 3128)"
check S5 "ops proxy as apt" ok "$(tcp_out _apt "$OPS" 3128)"
check S5 "ops proxy as an unprivileged user" fail "$(tcp_out nobody "$OPS" 3128)"
check S6 "HTTPS to a public address as root" fail "$(tcp_out root "$PUBLIC" 443)"
check S6 "the runtime proxy as root" fail "$(tcp_out root "$RUNTIME" 3128)"
check S7 "UPS status port on the NAS as the UPS user" ok "$(tcp_out nut "$NAS" 3493)"
check S7 "UPS status port on the NAS as root" fail "$(tcp_out root "$NAS" 3493)"
check S7 "another NAS port as the UPS user" fail "$(tcp_out nut "$NAS" 8002)"
check S12 "the stack collector as the collector user" ok "$(tcp_out otelcol "$COLLECTOR" 4317)"
check S12 "the stack collector as another user" fail "$(tcp_out nobody "$COLLECTOR" 4317)"
check S12 "another port of the collector's container" fail "$(tcp_out otelcol "$COLLECTOR" 9999)"
check H1 "loopback as an unprivileged user" ok "$(tcp_out nobody 127.0.0.1 9100)"
drops=$(iptables -w -nvxL AETHERSPARK-HOST-DROP | awk 'NR == 4 { print ($1 > 0) ? "yes" : "no" }')
check H1 "refused flows reach the logged drop" yes "$drops"

# --- check finds changes -------------------------------------------------------------------------
check S9 "check with the rules as applied" missed "$(check_finds)"
iptables -w -A INPUT -p tcp --dport 9100 -j ACCEPT
check S9 "check finds a rule added after the jump in INPUT" found "$(check_finds)"
iptables -w -D INPUT -p tcp --dport 9100 -j ACCEPT
iptables -w -P OUTPUT ACCEPT
check S9 "check finds the OUTPUT policy changed" found "$(check_finds)"
iptables -w -P OUTPUT DROP
iptables -w -I AETHERSPARK-OUTPUT 1 -j ACCEPT
check S9 "check finds a rule added to our OUTPUT chain" found "$(check_finds)"
iptables -w -D AETHERSPARK-OUTPUT 1

# --- Fail closed, and where it cannot ------------------------------------------------------------
iptables -w -F
check S9 "rule lists flushed: SSH from the admin desktop" fail "$(tcp_in desktop 22)"
check S9 "rule lists flushed: DNS as the resolver user" fail "$(udp_out systemd-resolve "$ROUTER" 53)"
bash "$R" apply >/dev/null 2>&1
check S9 "re-applied: SSH from the admin desktop" ok "$(tcp_in desktop 22)"
nft flush ruleset
check S9 "tables deleted: check finds it" found "$(check_finds)"
check S9 "tables deleted: the host is open (residual, caught by check)" ok "$(tcp_in desktop 9100)"
bash "$R" apply >/dev/null 2>&1
check S9 "re-applied: check" missed "$(check_finds)"
check S9 "re-applied: another port from the admin desktop" fail "$(tcp_in desktop 9100)"

# --- Lint, both modes ----------------------------------------------------------------------------
check L3 "the lint loads every variant" 4 "$(bash "$R" lint 2>/dev/null | grep -c '^L3 rules: load')"

if [ "$fails" -eq 0 ]; then echo "host lab: all passed"; else echo "host lab: $fails failed"; fi
[ "$fails" -eq 0 ]
