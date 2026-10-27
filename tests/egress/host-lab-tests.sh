#!/usr/bin/env bash
# Inside the lab container (tests/egress/host-lab.sh): the rule script in host mode against a LAN of
# network namespaces. The container's own namespace is the Spark; the router, the NAS, the admin
# desktop and another Home device sit on one bridge, and three Docker-style bridges hold stand-ins
# for the ops proxy, the runtime proxy and the stack collector. Every service answers "ok".
# Test ids as in docs/architecture/spark-host.md; LAB is the lab itself, B the baseline, L3 the lint.
set -uo pipefail
R=/repo/deploy/host/egress-rules.sh
ENV=/etc/aetherspark/egress-rules.env
SPARK=192.0.2.10 ROUTER=192.0.2.1 NAS=192.0.2.20 DESKTOP=192.0.2.50 OTHER=192.0.2.60
PUBLIC=203.0.113.10 OPS=172.30.20.2 RUNTIME=172.30.10.2 COLLECTOR=172.30.40.2
SPARK6=fd00:a::10 ROUTER6=fd00:a::1
fails=0

check() { # id, description, expected, actual
    if [ "$3" = "$4" ]; then printf 'PASS  %-4s %s\n' "$1" "$2"
    else printf 'FAIL  %-4s %s (expected %s, got %s)\n' "$1" "$2" "$3" "$4"; fails=$((fails + 1)); fi
}
# Every probe opens a new connection (existing ones would survive a rule change) and is judged by
# the answer it got, not by the client's exit status. -t3: after its input ends, the client waits
# up to 3 s for the answer (the default, 0.5 s, is too short on a busy machine).
answer() { if [ "$1" = ok ]; then echo ok; else echo fail; fi; }
tcp_out() { # user, address, port: ok when the Spark's connection gets the answer
    answer "$(timeout 8 runuser -u "$1" -- socat -t3 -T3 - "TCP4:$2:$3,connect-timeout=3" </dev/null 2>/dev/null)"
}
udp_out() { # user, address, port[, source port]
    answer "$(echo x | timeout 8 runuser -u "$1" -- socat -t3 -T3 - "UDP4:$2:$3${4:+,sourceport=$4}" 2>/dev/null)"
}
tcp_in() { # namespace, port: ok when a LAN device reaches the Spark
    answer "$(ip netns exec "$1" timeout 8 socat -t3 -T3 - "TCP4:$SPARK:$2,connect-timeout=3" </dev/null 2>/dev/null)"
}
broadcast() { # source port: the DHCP client's broadcast; ok when the router's answer comes in
    answer "$(echo x | timeout 8 socat -t3 -T3 - "UDP4-DATAGRAM:255.255.255.255:67,broadcast,bind=0.0.0.0:$1" 2>/dev/null)"
}
check_finds() { # what check reports about the current state: found (it failed) or missed
    if bash "$R" check >/dev/null 2>&1; then echo missed; else echo found; fi
}
drop_policies() { { iptables -S; ip6tables -S; } | grep -cE '^-P (INPUT|OUTPUT|FORWARD) DROP$'; }
open_policies() { local i c; for i in iptables ip6tables; do for c in INPUT OUTPUT FORWARD; do "$i" -w -P "$c" ACCEPT; done; done; }
listen() { # namespace ("" for the Spark), tcp|udp|tcp6, address, port[, command]
    local pre=() cmd=${5:-echo ok}
    [ -n "$1" ] && pre=(ip netns exec "$1")
    case "$2" in
        tcp) "${pre[@]}" socat "TCP4-LISTEN:$4,bind=$3,fork,reuseaddr" SYSTEM:"$cmd" 2>/dev/null & ;;
        udp) "${pre[@]}" socat "UDP4-RECVFROM:$4,bind=$3,fork" SYSTEM:"$cmd" 2>/dev/null & ;;
        tcp6) "${pre[@]}" socat "TCP6-LISTEN:$4,bind=[$3],fork,reuseaddr,ipv6only=1" SYSTEM:"$cmd" 2>/dev/null & ;;
    esac
}
listening() { # namespace, ss filter: wait up to 5 s until something listens there
    local pre=() i
    [ -n "$1" ] && pre=(ip netns exec "$1")
    for i in $(seq 50); do
        [ -n "$("${pre[@]}" ss -Hlnut "$2" 2>/dev/null)" ] && return 0
        sleep 0.1
    done
    echo "lab: nothing listens in ${1:-the Spark} at $2" >&2
    return 1
}

# --- The LAN ---------------------------------------------------------------------------------
sysctl -qw net.ipv6.conf.all.disable_ipv6=0 net.ipv6.conf.default.disable_ipv6=0
ip link set lo up
ip netns add sw
ip -n sw link add br0 type bridge
ip -n sw link set br0 up
ip link add lan0 type veth peer name spark netns sw
ip -n sw link set spark master br0 up
ip addr add "$SPARK/24" dev lan0
ip -6 addr add "$SPARK6/64" dev lan0 nodad
ip link set lan0 up
ip route add default via "$ROUTER"
peer() { # name, address: a device on the LAN
    ip netns add "$1"
    ip -n "$1" link set lo up
    ip netns exec "$1" sysctl -qw net.ipv6.conf.all.disable_ipv6=0 net.ipv6.conf.default.disable_ipv6=0
    ip -n sw link add "$1" type veth peer name eth0 netns "$1"
    ip -n sw link set "$1" master br0 up
    ip -n "$1" addr add "$2/24" dev eth0
    ip -n "$1" link set eth0 up
}
peer router "$ROUTER"
peer nas "$NAS"
peer desktop "$DESKTOP"
peer other "$OTHER"
ip -n router -6 addr add "$ROUTER6/64" dev eth0 nodad
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

# Services, each waited for. The router's DHCP service writes down each sender's port, so a test
# can tell what reached it.
listen "" tcp 0.0.0.0 22;              listening "" "sport = :22"
listen "" tcp 0.0.0.0 9100;            listening "" "sport = :9100"
listen "" tcp6 :: 22;                  listening "" "src [::]:22"
listen "" tcp6 ::1 9101;               listening "" "sport = :9101"
# shellcheck disable=SC2016 # expands in socat's child shell, where socat sets it
listen router udp 0.0.0.0 67 'echo $SOCAT_PEERPORT >>/tmp/router-dhcp-ports; echo ok'
listening router "sport = :67"
listen router udp "$ROUTER" 53;        listening router "src $ROUTER:53"
listen router tcp "$ROUTER" 53
listen router udp "$ROUTER" 123;       listening router "sport = :123"
listen router tcp "$PUBLIC" 443;       listening router "src $PUBLIC:443"
listen router udp "$PUBLIC" 53;        listening router "src $PUBLIC:53"
listen router tcp6 "$ROUTER6" 443;     listening router "src [$ROUTER6]:443"
listen nas tcp "$NAS" 3493;            listening nas "sport = :3493"
listen nas tcp "$NAS" 8002;            listening nas "sport = :8002"
listen other udp "$OTHER" 123;         listening other "sport = :123"
listen other tcp "$OTHER" 3493;        listening other "sport = :3493"
listen opsproxy tcp "$OPS" 3128;       listening opsproxy "sport = :3128"
listen rtproxy tcp "$RUNTIME" 3128;    listening rtproxy "sport = :3128"
listen collector tcp "$COLLECTOR" 4317; listening collector "sport = :4317"
listen collector tcp "$COLLECTOR" 9999; listening collector "sport = :9999"
listening router "src $ROUTER:53" && sleep 0.2

# Before any rule, every path answers: a "fail" below is the rules' doing, not the lab's
lab_paths=$(
    for p in "$(tcp_in desktop 22)" "$(tcp_in other 22)" "$(tcp_in desktop 9100)" \
        "$(udp_out root "$ROUTER" 53)" "$(tcp_out root "$ROUTER" 53)" "$(udp_out root "$ROUTER" 123)" \
        "$(udp_out root "$PUBLIC" 53)" "$(tcp_out root "$PUBLIC" 443)" "$(tcp_out root "$NAS" 3493)" \
        "$(tcp_out root "$NAS" 8002)" "$(udp_out root "$OTHER" 123)" "$(tcp_out root "$OTHER" 3493)" \
        "$(tcp_out root "$OPS" 3128)" "$(tcp_out root "$RUNTIME" 3128)" "$(tcp_out root "$COLLECTOR" 4317)" \
        "$(tcp_out root "$COLLECTOR" 9999)" \
        "$(answer "$(timeout 8 socat -t3 -T3 - "TCP6:[$ROUTER6]:443,connect-timeout=3" </dev/null 2>/dev/null)")" \
        "$(answer "$(ip netns exec router timeout 8 socat -t3 -T3 - "TCP6:[$SPARK6]:22,connect-timeout=3" </dev/null 2>/dev/null)")"
    do echo "$p"; done | sort | uniq -c | tr -s ' ' | sed 's/^ //')
check LAB "every path answers before any rule (18 paths)" "18 ok" "$lab_paths"

# --- Baseline: host mode on, no settings yet ---------------------------------------------------
mkdir -p /etc/aetherspark /etc/docker
touch /etc/aetherspark/host-mode
bash "$R" baseline
check B "baseline without settings: DROP policies, IPv4 and IPv6" 6 "$(drop_policies)"
check B "SSH from the admin desktop under the baseline alone" fail "$(tcp_in desktop 22)"

# --- Apply ---------------------------------------------------------------------------------------
write_settings() { # LAN interface, collector bridge
    cat >"$ENV" <<EOF
AS_RESOLVERS="$ROUTER"
AS_HOME_SUBNETS="198.51.100.0/24"
AS_NAS_ADDR="$NAS"
AS_NAS_PORT="8002"
AS_LAN_IF="$1"
AS_ROUTER="$ROUTER"
AS_ADMIN_ADDR="$DESKTOP"
AS_UPS_PORT="3493"
AS_COLLECTOR="$COLLECTOR:4317"
AS_COLLECTOR_IF="$2"
AS_COLLECTOR_USER="otelcol"
EOF
}
write_settings lan0 as-obs-edge
printf '{"dns": ["%s"], "userland-proxy": false}\n' "$ROUTER" >/etc/docker/daemon.json
out=$(bash "$R" apply 2>&1)
check B "apply" "aetherspark-egress-rules: in place" "$(printf '%s\n' "$out" | tail -n 1)"

check S1 "SSH from the admin desktop" ok "$(tcp_in desktop 22)"
check S1 "SSH from another Home device" fail "$(tcp_in other 22)"
check S1 "another port from the admin desktop" fail "$(tcp_in desktop 9100)"
# The broadcast before the renewal: a renewal's connection entry would also let the broadcast's
# answer in as a reply, and the rule for the router's answers would go untested
check S2 "DHCP broadcast, the router's answer comes in" ok "$(broadcast 68)"
check S2 "DHCP broadcast from another source port, seen at the router" no \
    "$(broadcast 6868 >/dev/null; if grep -qx 6868 /tmp/router-dhcp-ports 2>/dev/null; then echo yes; else echo no; fi)"
check S2 "DHCP renewal to the router (UDP 68 -> 67)" ok "$(udp_out root "$ROUTER" 67 68)"
listen "" udp 0.0.0.0 68
dhcp_client=$!
listening "" "sport = :68"
check S2 "a DHCP answer from another device" fail \
    "$(answer "$(echo x | ip netns exec other timeout 8 socat -t3 -T3 - "UDP4:$SPARK:68,sourceport=67" 2>/dev/null)")"
kill "$dhcp_client" 2>/dev/null
check S3 "DNS (UDP) to the router as the resolver user" ok "$(udp_out systemd-resolve "$ROUTER" 53)"
check S3 "DNS (TCP) to the router as the resolver user" ok "$(tcp_out systemd-resolve "$ROUTER" 53)"
check S3 "DNS to the router as root" fail "$(udp_out root "$ROUTER" 53)"
check S3 "DNS to another server as the resolver user" fail "$(udp_out systemd-resolve "$PUBLIC" 53)"
check S4 "NTP to the router as the time user" ok "$(udp_out systemd-timesync "$ROUTER" 123)"
check S4 "NTP to the router as root" fail "$(udp_out root "$ROUTER" 123)"
check S4 "NTP to another device as the time user" fail "$(udp_out systemd-timesync "$OTHER" 123)"
check S5 "ops proxy as root" ok "$(tcp_out root "$OPS" 3128)"
check S5 "ops proxy as apt" ok "$(tcp_out _apt "$OPS" 3128)"
check S5 "ops proxy as an unprivileged user" fail "$(tcp_out nobody "$OPS" 3128)"
check S6 "HTTPS to a public address as root" fail "$(tcp_out root "$PUBLIC" 443)"
check S6 "the runtime proxy as root" fail "$(tcp_out root "$RUNTIME" 3128)"
check S6 "IPv6 out, to the router" fail \
    "$(answer "$(timeout 8 socat -t3 -T3 - "TCP6:[$ROUTER6]:443,connect-timeout=3" </dev/null 2>/dev/null)")"
check S6 "IPv6 in, SSH from the router" fail \
    "$(answer "$(ip netns exec router timeout 8 socat -t3 -T3 - "TCP6:[$SPARK6]:22,connect-timeout=3" </dev/null 2>/dev/null)")"
check S6 "IPv6 on loopback" ok \
    "$(answer "$(timeout 8 runuser -u nobody -- socat -t3 -T3 - "TCP6:[::1]:9101,connect-timeout=3" </dev/null 2>/dev/null)")"
check S7 "UPS status port on the NAS as the UPS user" ok "$(tcp_out nut "$NAS" 3493)"
check S7 "UPS status port on the NAS as root" fail "$(tcp_out root "$NAS" 3493)"
check S7 "another NAS port as the UPS user" fail "$(tcp_out nut "$NAS" 8002)"
check S7 "the same port on another device as the UPS user" fail "$(tcp_out nut "$OTHER" 3493)"
check S12 "the stack collector as the collector user" ok "$(tcp_out otelcol "$COLLECTOR" 4317)"
check S12 "the stack collector as another user" fail "$(tcp_out nobody "$COLLECTOR" 4317)"
check S12 "another port of the collector's container" fail "$(tcp_out otelcol "$COLLECTOR" 9999)"
check H1 "loopback as an unprivileged user" ok "$(tcp_out nobody 127.0.0.1 9100)"
logged=$(iptables -w -nvxL AETHERSPARK-HOST-DROP | awk 'NR == 3 { print ($1 > 0) ? "yes" : "no" }')
check H1 "refused flows reach the logging rule" yes "$logged"

# --- Rules tied to their interfaces --------------------------------------------------------------
write_settings lan9 as-obs-edge
bash "$R" apply >/dev/null 2>&1
check IF "LAN rules on another interface: SSH from the admin desktop" fail "$(tcp_in desktop 22)"
check IF "LAN rules on another interface: DNS as the resolver user" fail "$(udp_out systemd-resolve "$ROUTER" 53)"
write_settings lan0 as-eg-runtime
bash "$R" apply >/dev/null 2>&1
check IF "collector rule on another bridge: the collector user" fail "$(tcp_out otelcol "$COLLECTOR" 4317)"
write_settings lan0 as-obs-edge
bash "$R" apply >/dev/null 2>&1
check IF "settings restored: the collector user" ok "$(tcp_out otelcol "$COLLECTOR" 4317)"

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
iptables -w -I DOCKER-USER 1 -j RETURN
check S9 "check finds a RETURN at the top of DOCKER-USER" found "$(check_finds)"
iptables -w -D DOCKER-USER 1
iptables -w -I AETHERSPARK-INPUT 1 -j RETURN
check S9 "check finds a RETURN at the top of our INPUT chain" found "$(check_finds)"
iptables -w -D AETHERSPARK-INPUT 1
iptables -w -A DOCKER-USER -j RETURN
check S9 "check accepts the trailing RETURN older Docker appends" missed "$(check_finds)"
iptables -w -D DOCKER-USER -j RETURN
iptables -w -I FORWARD 2 -j ACCEPT
check S9 "check finds a rule added to FORWARD" found "$(check_finds)"
iptables -w -D FORWARD 2
check S9 "all changes undone" missed "$(check_finds)"

# --- Fail closed, and where it cannot ------------------------------------------------------------
rm /etc/aetherspark/host-mode
if AS_REQUIRE_HOST_MODE=1 bash "$R" check >/dev/null 2>&1; then r=missed; else r=found; fi
check B "marker deleted, host mode required: check" found "$r"
open_policies
AS_REQUIRE_HOST_MODE=1 bash "$R" baseline
check B "marker deleted, host mode required: baseline closes the host" 6 "$(drop_policies)"
touch /etc/aetherspark/host-mode
# A boot with a broken settings file: no rules yet, then the unit's baseline and apply. An unquoted
# list is enough to break the file.
cp "$ENV" "$ENV.good"
echo 'AS_HOME_SUBNETS=198.51.100.0/24 203.0.113.0/24' >>"$ENV"
nft flush ruleset
bash "$R" baseline
check B "broken settings file at boot: baseline closes the host" 6 "$(drop_policies)"
if bash "$R" apply >/dev/null 2>&1; then r=applied; else r=stopped; fi
check B "broken settings file at boot: apply stops" stopped "$r"
check B "broken settings file at boot: SSH from the admin desktop" fail "$(tcp_in desktop 22)"
mv "$ENV.good" "$ENV"
bash "$R" apply >/dev/null 2>&1
check B "settings repaired: SSH from the admin desktop" ok "$(tcp_in desktop 22)"

iptables -w -F
check S9 "IPv4 rule lists flushed: SSH from the admin desktop" fail "$(tcp_in desktop 22)"
check S9 "IPv4 rule lists flushed: DNS as the resolver user" fail "$(udp_out systemd-resolve "$ROUTER" 53)"
check S9 "IPv4 rule lists flushed: check finds it" found "$(check_finds)"
bash "$R" apply >/dev/null 2>&1
ip6tables -w -F
check S9 "IPv6 rule lists flushed: the policies stay DROP" 6 "$(drop_policies)"
check S9 "IPv6 rule lists flushed: check finds it" found "$(check_finds)"
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
