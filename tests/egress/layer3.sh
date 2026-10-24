#!/usr/bin/env bash
# Egress tests, layer 3 (docs/architecture/egress.md, "Tests"): installs the host rules, reruns
# layers 1-2 with them in place, then E9, E16, E17, E18, E19, E21 and E22. Changes the host
# firewall and adds two network namespaces: a Linux host with Docker Engine only (CI, the Spark),
# as root. Every "fails" check has a control showing the target answers where allowed.
#
# AS_LAB=1: a Docker-in-Docker lab without systemd or kernel log (local work on the rules):
# applies the rules directly, restarts dockerd with `l3-dockerd`, and skips the systemd and
# kernel-log checks with a visible SKIP. CI never sets it.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
if docker info --format '{{.OperatingSystem}}' | grep -q 'Docker Desktop'; then
    echo "Docker Desktop: no layer 3 (see the design note)"; exit 1
fi

dc() { docker compose -f deploy/compose/egress/compose.yaml -f tests/egress/compose.test.yaml \
    -f tests/egress/compose.layer3.yaml "$@"; }
RT=http://172.30.10.2:3128
HOME_NET=192.168.77   # stands in for the Home subnet; .20 is the NAS
HOME_V6=fd77::        # the same network's IPv6 (ULA); the stack has no IPv6
OTHER_NET=192.168.78  # another non-Docker network on the host; .53 a resolver for E22
fails=0
lab=${AS_LAB:-}

check() { # id, description, expected, actual
    if [ "$3" = "$4" ]; then printf 'PASS  %-4s %s\n' "$1" "$2"
    else printf 'FAIL  %-4s %s (expected %s, got %s)\n' "$1" "$2" "$3" "$4"; fails=$((fails + 1)); fi
}
skip() { printf 'SKIP  %-4s %s (%s)\n' "$1" "$2" "$3"; }
# "open" if a TCP connection got an HTTP answer, "closed" otherwise; the prefix runs curl somewhere
reach() { # url, prefix command...
    local url=$1; shift
    [ "$("$@" curl -s -o /dev/null -m 5 -g -w '%{http_code}' "$url" 2>/dev/null)" != 000 ] && echo open || echo closed
}
ex() { dc exec -T "$@"; }
outcome() { if "$@" >/dev/null 2>&1; then echo ok; else echo fail; fi; }
# Kernel log lines from this run only (earlier runs leave theirs)
klog_from=$(dmesg 2>/dev/null | wc -l)
logged() { # log prefix, interface prefix: was such a packet logged during this run?
    dmesg | tail -n +"$((klog_from + 1))" | grep -q "$1:IN=$2" && echo yes || echo no
}
# Checks that need the kernel log: skipped in the lab
check_logged() { # id, description, log prefix, interface prefix
    if [ -n "$lab" ]; then skip "$1" "$2" lab; else check "$1" "$2" yes "$(logged "$3" "$4")"; fi
}

# Resolvers Docker hands its embedded DNS: systemd-resolved's upstreams when the host uses the stub
resolvers() {
    local f=/etc/resolv.conf
    grep -q '^nameserver 127\.0\.0\.53' "$f" && f=/run/systemd/resolve/resolv.conf
    awk '/^nameserver/ && $2 !~ /:/ {print $2}' "$f" | tr '\n' ' '
}

pids=()
# shellcheck disable=SC2317 # called by the EXIT trap
cleanup() {
    kill "${pids[@]}" 2>/dev/null
    dc --profile egress-ops down --remove-orphans >/dev/null 2>&1
    ip netns del t3home 2>/dev/null; ip netns del t3other 2>/dev/null
    rm -f "$env_file" "$udp_log"
}
env_file=$(mktemp)
udp_log=$(mktemp)
trap cleanup EXIT

# Build and pull first: with the rules in place, build steps on the default bridge have no way out,
# not even DNS. On a host that already has the rules (a second run on the Spark), build the images
# beforehand; on the Spark, builds go through the ops proxy (WP6.4).
out=$(dc --profile egress-ops build 2>&1 && dc --profile egress-ops pull --ignore-buildable 2>&1) || {
    echo "$out"
    echo "build or pull failed; if the layer-3 rules are already in place, that is them (see above)"
    exit 1
}

# --- rules ----------------------------------------------------------------------------------
# The test resolver comes last: Docker asks the real ones first
cat > "$env_file" <<EOF
AS_RESOLVERS="$(resolvers)$OTHER_NET.53"
AS_HOME_SUBNETS="$HOME_NET.0/24"
AS_NAS_ADDR="$HOME_NET.20"
AS_NAS_PORT="8002"
EOF
cat "$env_file"
# The rule script with these settings: apply, check or remove
# shellcheck source=/dev/null
rules() { (set -a; . "$env_file"; "$rules_cmd" "$1"); }
if [ -n "$lab" ]; then
    rules_cmd=deploy/host/egress-rules.sh
    # What install.sh does to daemon.json, without merging (the lab has no other settings)
    mkdir -p /etc/docker
    # shellcheck source=/dev/null
    (. "$env_file"; read -ra r <<<"$AS_RESOLVERS"
     printf '{"dns": [%s], "userland-proxy": false}\n' "$(printf '"%s",' "${r[@]}" | sed 's/,$//')") \
        > /etc/docker/daemon.json
    l3-dockerd || exit 1
    rules apply || exit 1
else
    rules_cmd=/usr/local/sbin/aetherspark-egress-rules
    deploy/host/install.sh "$env_file" || exit 1
fi

# --- layers 1-2 again, now with layer 3 in place --------------------------------------------
echo "--- layers 1-2 with layer 3"
bash tests/egress/run.sh || fails=$((fails + 1))

# --- fixtures -------------------------------------------------------------------------------
netns() { # name, network, namespace hosts...
    local ns=$1 net=$2 h; shift 2
    ip netns add "$ns"
    ip link add "$ns" type veth peer name eth0 netns "$ns"
    ip addr add "$net.1/24" dev "$ns"; ip link set "$ns" up
    for h in "$@"; do ip -n "$ns" addr add "$net.$h/24" dev eth0; done
    ip -n "$ns" link set lo up; ip -n "$ns" link set eth0 up
    ip -n "$ns" route add default via "$net.1"
}
serve() { # port, bind address, prefix command...
    local port=$1 addr=$2; shift 2
    "$@" python3 -m http.server "$port" --bind "$addr" >/dev/null 2>&1 &
    pids+=($!)
}
netns t3home "$HOME_NET" 10 20
netns t3other "$OTHER_NET" 10 53
# IPv6 on the Home network, as a LAN has link-local addresses even with IPv6 off at the router
sysctl -qw net.ipv6.conf.t3home.disable_ipv6=0
ip netns exec t3home sysctl -qw net.ipv6.conf.eth0.disable_ipv6=0
ip addr add "${HOME_V6}1/64" dev t3home nodad
ip -n t3home addr add "${HOME_V6}10/64" dev eth0 nodad
serve 8002 "$HOME_NET.20" ip netns exec t3home   # the NAS rest-server
serve 443 "$HOME_NET.20" ip netns exec t3home    # another port on the NAS
serve 8002 "$HOME_NET.10" ip netns exec t3home   # another Home device, same port
serve 8003 "$OTHER_NET.10" ip netns exec t3other # a device on the other network
serve 8099 0.0.0.0                               # a host service
serve 8098 "${HOME_V6}1"                         # a host service on the Home IPv6 address
ip netns exec t3other python3 tests/egress/udp.py serve "$OTHER_NET.53" 53 "$udp_log" >/dev/null 2>&1 &
pids+=($!)
out=$(dc up -d --build --wait egress-runtime client-runtime client-agent client-plain client-backup \
    probe-out sender-out forger-backup web-ingress 2>&1) || { echo "$out"; echo "stack did not start"; exit 1; }
for _ in $(seq 1 20); do [ "$(reach "http://$HOME_NET.20:8002/")" = open ] && break; sleep 0.5; done

echo "--- layer 3"
# E9 a container on a network outside the matrix: no way out, and the drop is logged
check E9 "plain bridge: HTTPS by address" fail "$(outcome ex client-plain curl -s -m 5 -k https://1.1.1.1/)"
check E9 "plain bridge: DNS" fail "$(outcome ex client-plain nslookup www.sec.gov)"
check_logged E9 "plain bridge: drop logged" aetherspark-drop br-

# E9 backup: the NAS address and port only
check E9 "backup: NAS rest-server" open "$(reach "http://$HOME_NET.20:8002/" ex client-backup)"
check E9 "control: NAS port 443 answers the host" open "$(reach "http://$HOME_NET.20:443/")"
check E9 "backup: NAS on another port" closed "$(reach "http://$HOME_NET.20:443/" ex client-backup)"
check E9 "control: another Home device answers the host" open "$(reach "http://$HOME_NET.10:8002/")"
check E9 "backup: another Home device" closed "$(reach "http://$HOME_NET.10:8002/" ex client-backup)"
check E9 "backup: the internet" fail "$(outcome ex client-backup curl -s -m 5 -k https://1.1.1.1/)"
check E9 "backup: DNS" fail "$(outcome ex client-backup nslookup www.sec.gov)"
check_logged E9 "backup: drop logged" aetherspark-drop as-backup

# E21 the proxies' way out: public TCP 443 and the resolver, nothing else
check E21 "out-runtime: public 443" ok "$(outcome ex probe-out curl -s -m 10 -k -o /dev/null https://1.1.1.1/)"
check E21 "out-runtime: public 80" fail "$(outcome ex probe-out curl -s -m 5 -o /dev/null http://1.1.1.1/)"
check E21 "out-runtime: private address on 443" closed "$(reach "http://$HOME_NET.20:443/" ex probe-out)"
if ping -c 1 -W 3 1.1.1.1 >/dev/null 2>&1; then
    check E21 "out-runtime: ICMP" fail "$(outcome ex probe-out ping -c 1 -W 3 1.1.1.1)"
else skip E21 "out-runtime: ICMP" "the host itself gets no ICMP reply"; fi
check E21 "out-runtime: DNS via Docker's resolver" ok "$(outcome ex probe-out nslookup www.sec.gov)"
check E21 "out-runtime: DNS to a public resolver" fail "$(outcome ex probe-out nslookup www.sec.gov 9.9.9.9)"

# E22 a container forging the source address of a client on the proxies' way out, to ride that
# client's open connection to the resolver
sender=$(docker inspect -f '{{(index .NetworkSettings.Networks "aetherspark-egress_out-runtime").IPAddress}}' \
    "$(dc ps -q sender-out)")
check E22 "control: genuine datagram answered" pong "$(ex sender-out python3 /udp.py send "$OTHER_NET.53" 53 40053 first)"
ex forger-backup python3 /udp.py forge "$sender" 40053 "$OTHER_NET.53" 53 forged
check E22 "control: same connection still answered" pong "$(ex sender-out python3 /udp.py send "$OTHER_NET.53" 53 40053 second)"
check E22 "forged source dropped" no "$(grep -qx forged "$udp_log" && echo yes || echo no)"
check_logged E22 "forged source logged" aetherspark-spoof as-backup

# E16 host services through the bridge address
check E16 "control: host service answers locally" open "$(reach http://127.0.0.1:8099/)"
check E16 "internal network to host service" closed "$(reach http://172.30.10.1:8099/ ex client-runtime)"
check E16 "agent network to host service" closed "$(reach http://172.30.90.1:8099/ ex client-agent)"
plain_gw=$(docker network inspect aetherspark-egress_plain --format '{{(index .IPAM.Config 0).Gateway}}')
check E16 "plain bridge to host service" closed "$(reach "http://$plain_gw:8099/" ex client-plain)"
if timeout 3 bash -c '</dev/tcp/127.0.0.1/22' 2>/dev/null; then
    check E16 "internal network to host sshd" fail "$(outcome ex client-runtime nc -z -w 3 172.30.10.1 22)"
else skip E16 "internal network to host sshd" "no sshd on this host"; fi

# E18 no forwarding between two non-Docker networks, and not only by Docker's FORWARD policy
check E18 "control: other network answers the host" open "$(reach "http://$OTHER_NET.10:8003/")"
check E18 "Home to other network" closed "$(reach "http://$OTHER_NET.10:8003/" ip netns exec t3home)"
check_logged E18 "Home to other network: drop logged" aetherspark-drop t3home

# E19 ingress: 443 from the Home subnet only, over IPv4 only
check E19 "control: published 8080 answers locally" open "$(reach http://127.0.0.1:8080/)"
check E19 "Home to 443" open "$(reach "http://$HOME_NET.1:443/" ip netns exec t3home)"
check E19 "Home to another published port" closed "$(reach "http://$HOME_NET.1:8080/" ip netns exec t3home)"
check E19 "other network to 443" closed "$(reach "http://$OTHER_NET.1:443/" ip netns exec t3other)"
# Docker 29 lets other networks reach published ports through a host address (no userland proxy)
check E19 "backup network to published 443 via a host address" closed "$(reach "http://$HOME_NET.1:443/" ex client-backup)"
check E19 "out network to published 443 via a host address" closed "$(reach "http://$HOME_NET.1:443/" ex probe-out)"
check E19 "control: host answers on Home IPv6" open "$(reach "http://[${HOME_V6}1]:8098/" ip netns exec t3home)"
check E19 "Home IPv6 to published 443" closed "$(reach "http://[${HOME_V6}1]:443/" ip netns exec t3home)"
check E19 "Home IPv6 to published 8080" closed "$(reach "http://[${HOME_V6}1]:8080/" ip netns exec t3home)"

# E17 Docker restart: the rules stay in place and still work
echo "--- Docker restart"
if [ -n "$lab" ]; then l3-dockerd; else systemctl restart docker; fi
for _ in $(seq 1 60); do
    [ "$(docker inspect -f '{{.State.Health.Status}}' aetherspark-egress-egress-runtime-1 2>/dev/null)" = healthy ] && break
    sleep 1
done
check E17 "runtime proxy back on its own" healthy "$(docker inspect -f '{{.State.Health.Status}}' aetherspark-egress-egress-runtime-1 2>/dev/null)"
check E17 "rules in place" ok "$(outcome rules check)"
if [ -n "$lab" ]; then skip E17 "docker.service requires and follows the rules" lab; else
    check E17 "docker.service requires and follows the rules" 2 \
        "$(systemctl show docker.service -p Requires -p After | grep -c aetherspark-egress-rules.service)"; fi
dc up -d --wait client-runtime client-plain client-backup >/dev/null 2>&1
check E17 "www.sec.gov via runtime proxy" 200 "$(ex client-runtime curl -s -o /dev/null -m 15 -w '%{http_connect}' --proxy "$RT" https://www.sec.gov/)"
check E17 "plain bridge: HTTPS by address" fail "$(outcome ex client-plain curl -s -m 5 -k https://1.1.1.1/)"
check E17 "internal network to host service" closed "$(reach http://172.30.10.1:8099/ ex client-runtime)"
check E17 "backup: NAS rest-server" open "$(reach "http://$HOME_NET.20:8002/" ex client-backup)"

echo "---"
if [ "$fails" -eq 0 ]; then echo "layer-3 tests: all passed"; else echo "layer-3 tests: $fails failed"; fi
exit $((fails > 0))
