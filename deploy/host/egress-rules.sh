#!/usr/bin/env bash
# Layer 3 of the egress control (docs/architecture/egress.md, "Layer 3 rules"): DOCKER-USER for
# forwarded traffic, host INPUT from Docker bridges, a source-address check on Docker bridges, IPv6
# from Docker bridges. On the Spark also host mode (docs/architecture/spark-host.md): INPUT and
# OUTPUT default-deny for the host itself, with DROP policies.
#
# Installed as /usr/local/sbin/aetherspark-egress-rules and run by aetherspark-egress-rules.service
# before the network and before docker.service. Needs Docker Engine with the iptables backend
# (iptables-nft); Docker keeps DOCKER-USER as it is across restarts and puts its jump first in FORWARD.
#
#   baseline  host mode only: DROP policies for INPUT, OUTPUT and FORWARD (IPv4 and IPv6); reads no
#             settings, so the host is closed even when the settings are missing or broken
#   apply     the baseline, then replace the rules (atomically per address family)
#   check     exit 1 unless every rule is exactly as rendered here and Docker's settings match; in
#             host mode also the built-in chains' policies and contents
#   remove    take the rules out again (tests only: the break-it test, CI cleanup, the lab)
#   lint      load the rules rendered from the example settings, with and without a backup flow,
#             in both modes, in a scratch network namespace (lint L3, from the repository);
#             changes nothing on the host
#
# Host mode is on when /etc/aetherspark/host-mode exists (the Spark's host role creates it).
#
# Settings, from the environment or else /etc/aetherspark/egress-rules.env (values in ops.local/):
#   AS_RESOLVERS     upstream resolvers the proxies may query on port 53 (IPv4, space separated);
#                    also Docker's "dns" in /etc/docker/daemon.json
#   AS_HOME_SUBNETS  sources allowed to open TCP 443 to the `ingress` network (space separated)
#   AS_NAS_ADDR      backup target address for the `backup` network (empty: no backup flow)
#   AS_NAS_PORT      backup target port
# Host mode only:
#   AS_LAN_IF        the host's LAN interface; nothing else carries host traffic
#   AS_ROUTER        the router: DHCP, DNS (resolver user only) and NTP (time user only)
#   AS_ADMIN_ADDR    the admin desktop: the only source of SSH
#   AS_UPS_PORT      UPS status port on AS_NAS_ADDR (empty: no UPS flow)
#   AS_OPS_PROXY     the ops proxy's fixed address:port (default the egress overlay's)
#   AS_COLLECTOR     the stack collector's fixed address:port (empty: no host telemetry flow)
#   AS_COLLECTOR_IF  the bridge of the collector's network (needed with AS_COLLECTOR)
#   AS_RESOLVER_USER, AS_TIME_USER, AS_UPS_USER, AS_APT_USER, AS_COLLECTOR_USER
#                    the local users those flows are limited to
set -euo pipefail

SETTINGS=/etc/aetherspark/egress-rules.env
# Host mode: the marker file, created by the Spark's host role. The role's drop-ins for the units
# also set AS_REQUIRE_HOST_MODE=1, so a deleted marker closes the host instead of opening it.
HOST_MODE_FILE=/etc/aetherspark/host-mode
HOST=0
[ -e "$HOST_MODE_FILE" ] && HOST=1
REQUIRE_HOST=${AS_REQUIRE_HOST_MODE:-0}

# Docker bridges: the default bridge, unnamed networks, and the stack's named networks. Flows are
# granted to exact names only; the wildcards only ever drop.
BRIDGES=(docker0 br-+ as-+)
OUT_BRIDGES=(as-out-runtime as-out-ops)
# Not public: the proxies' way out never reaches these (RFC 6890 special-purpose ranges)
NOT_PUBLIC=0.0.0.0/8,10.0.0.0/8,100.64.0.0/10,127.0.0.0/8,169.254.0.0/16,172.16.0.0/12,192.0.0.0/24,192.0.2.0/24,192.168.0.0/16,198.18.0.0/15,198.51.100.0/24,203.0.113.0/24,224.0.0.0/4,240.0.0.0/4
# Log budget per source and destination pair: one noisy flow cannot hide another container's
# attempt (a single shared limit would). Argument: the hash table's name, one per rule.
limit() { echo "-m hashlimit --hashlimit-upto 10/minute --hashlimit-burst 5 --hashlimit-mode srcip,dstip --hashlimit-name $1"; }

die() { echo "aetherspark-egress-rules: $*" >&2; exit 1; }

rules_v4() {
    local i o s r b
    echo '*filter'
    echo ':DOCKER-USER - [0:0]'
    echo ':AETHERSPARK-INPUT - [0:0]'
    echo ':AETHERSPARK-DROP - [0:0]'
    echo "-A AETHERSPARK-DROP $(limit as-drop) -j LOG --log-prefix aetherspark-drop:"
    echo '-A AETHERSPARK-DROP -j DROP'

    # Forwarded traffic. RETURN hands a packet back to Docker's own chains, which still apply
    # their isolation (internal networks, one network from another); the last rule drops the rest.
    # Replies match by connection state; the source check in the raw table below keeps a container
    # from sending as another one and riding its connection.
    echo '-A DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN'
    # Traefik's published port from another network through a host address: Docker 29 allows
    # that without the userland proxy, so it is dropped here before the bridge pairs
    echo '-A DOCKER-USER -i as-ingress -o as-ingress -j RETURN'
    for i in "${BRIDGES[@]}"; do
        echo "-A DOCKER-USER -i $i -o as-ingress -m conntrack --ctstate NEW -j AETHERSPARK-DROP"
    done
    for i in "${BRIDGES[@]}"; do for o in "${BRIDGES[@]}"; do
        echo "-A DOCKER-USER -i $i -o $o -j RETURN"
    done; done
    for s in $AS_HOME_SUBNETS; do
        echo "-A DOCKER-USER -s $s -o as-ingress -p tcp -m conntrack --ctstate NEW --ctorigdstport 443 -j RETURN"
    done
    for b in "${OUT_BRIDGES[@]}"; do
        for r in $AS_RESOLVERS; do
            echo "-A DOCKER-USER -i $b -d $r -p udp --dport 53 -j RETURN"
            echo "-A DOCKER-USER -i $b -d $r -p tcp --dport 53 -j RETURN"
        done
        echo "-A DOCKER-USER -i $b -d $NOT_PUBLIC -j AETHERSPARK-DROP"
        echo "-A DOCKER-USER -i $b -p tcp --dport 443 -j RETURN"
    done
    if [ -n "$AS_NAS_ADDR" ]; then
        # shellcheck disable=SC2153 # AS_NAS_PORT comes from the settings, checked in settings()
        echo "-A DOCKER-USER -i as-backup -d $AS_NAS_ADDR -p tcp --dport $AS_NAS_PORT -j RETURN"
    fi
    # Everything else: other ports and addresses, other bridges, and forwarding between two
    # non-Docker interfaces (the Spark is multi-homed)
    echo '-A DOCKER-USER -j AETHERSPARK-DROP'

    if [ "$HOST" -eq 0 ]; then
        # Host services are not reachable from containers; replies to the host's own connections are
        echo '-A AETHERSPARK-INPUT -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN'
        for i in "${BRIDGES[@]}"; do echo "-A AETHERSPARK-INPUT -i $i -j AETHERSPARK-DROP"; done
    else
        host_rules_v4
    fi
    echo 'COMMIT'

    # Source check before connection tracking: a packet from a bridge must carry a source address
    # that is routed back through that bridge (containers have NET_RAW by default)
    echo '*raw'
    echo ':AETHERSPARK-SPOOF - [0:0]'
    for b in "${BRIDGES[@]}"; do
        echo "-A AETHERSPARK-SPOOF -i $b -m rpfilter --invert $(limit as-spoof) -j LOG --log-prefix aetherspark-spoof:"
        echo "-A AETHERSPARK-SPOOF -i $b -m rpfilter --invert -j DROP"
    done
    echo 'COMMIT'
}

# Host mode (spark-host.md, flows H1-H9): the policies are DROP, so these chains ACCEPT what is
# allowed and send the rest to a logged drop. Containers still reach no host service.
host_rules_v4() {
    local i
    echo ':AETHERSPARK-OUTPUT - [0:0]'
    echo ':AETHERSPARK-HOST-DROP - [0:0]'
    echo "-A AETHERSPARK-HOST-DROP $(limit as-hostdrop) -j LOG --log-prefix aetherspark-host-drop:"
    echo '-A AETHERSPARK-HOST-DROP -j DROP'

    # In: replies, loopback, SSH from the admin desktop, the router's DHCP answers (a broadcast
    # exchange is not tracked as a connection)
    echo '-A AETHERSPARK-INPUT -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT'
    echo '-A AETHERSPARK-INPUT -m conntrack --ctstate INVALID -j DROP'
    echo '-A AETHERSPARK-INPUT -i lo -j ACCEPT'
    for i in "${BRIDGES[@]}"; do echo "-A AETHERSPARK-INPUT -i $i -j AETHERSPARK-DROP"; done
    echo "-A AETHERSPARK-INPUT -i $AS_LAN_IF -s $AS_ADMIN_ADDR -p tcp --dport 22 -m conntrack --ctstate NEW -j ACCEPT"
    echo "-A AETHERSPARK-INPUT -i $AS_LAN_IF -s $AS_ROUTER -p udp --sport 67 --dport 68 -j ACCEPT"
    echo '-A AETHERSPARK-INPUT -j AETHERSPARK-HOST-DROP'

    # Out: replies, loopback; DHCP to the router and to broadcast (renewals are plain UDP); DNS
    # and NTP to the router, each for its service's user only; the UPS client to the NAS; root and
    # apt to the ops proxy only; the host collector to the stack's collector only
    echo '-A AETHERSPARK-OUTPUT -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT'
    echo '-A AETHERSPARK-OUTPUT -m conntrack --ctstate INVALID -j DROP'
    echo '-A AETHERSPARK-OUTPUT -o lo -j ACCEPT'
    echo "-A AETHERSPARK-OUTPUT -o $AS_LAN_IF -d $AS_ROUTER -p udp --sport 68 --dport 67 -j ACCEPT"
    echo "-A AETHERSPARK-OUTPUT -o $AS_LAN_IF -d 255.255.255.255 -p udp --sport 68 --dport 67 -j ACCEPT"
    echo "-A AETHERSPARK-OUTPUT -o $AS_LAN_IF -d $AS_ROUTER -p udp --dport 53 -m owner --uid-owner $AS_RESOLVER_USER -j ACCEPT"
    echo "-A AETHERSPARK-OUTPUT -o $AS_LAN_IF -d $AS_ROUTER -p tcp --dport 53 -m owner --uid-owner $AS_RESOLVER_USER -j ACCEPT"
    echo "-A AETHERSPARK-OUTPUT -o $AS_LAN_IF -d $AS_ROUTER -p udp --dport 123 -m owner --uid-owner $AS_TIME_USER -j ACCEPT"
    if [ -n "$AS_UPS_PORT" ]; then
        echo "-A AETHERSPARK-OUTPUT -o $AS_LAN_IF -d $AS_NAS_ADDR -p tcp --dport $AS_UPS_PORT -m owner --uid-owner $AS_UPS_USER -j ACCEPT"
    fi
    for i in root "$AS_APT_USER"; do
        echo "-A AETHERSPARK-OUTPUT -o as-eg-ops -d ${AS_OPS_PROXY%:*} -p tcp --dport ${AS_OPS_PROXY##*:} -m owner --uid-owner $i -j ACCEPT"
    done
    if [ -n "$AS_COLLECTOR" ]; then
        echo "-A AETHERSPARK-OUTPUT -o $AS_COLLECTOR_IF -d ${AS_COLLECTOR%:*} -p tcp --dport ${AS_COLLECTOR##*:} -m owner --uid-owner $AS_COLLECTOR_USER -j ACCEPT"
    fi
    echo '-A AETHERSPARK-OUTPUT -j AETHERSPARK-HOST-DROP'
}

# IPv6 is off on every network; nothing is forwarded and Docker bridges reach no host service.
# Forwarding uses DOCKER-USER here too: Docker re-inserts its own jump first in FORWARD on every
# start, so a separate chain would end up behind it. In host mode the host itself sends and
# accepts IPv6 on loopback only.
rules_v6() {
    local b
    echo '*filter'
    echo ':DOCKER-USER - [0:0]'
    echo ':AETHERSPARK-INPUT - [0:0]'
    echo ':AETHERSPARK-DROP - [0:0]'
    echo "-A AETHERSPARK-DROP $(limit as-drop6) -j LOG --log-prefix aetherspark-drop:"
    echo '-A AETHERSPARK-DROP -j DROP'
    echo '-A DOCKER-USER -j AETHERSPARK-DROP'
    if [ "$HOST" -eq 1 ]; then echo '-A AETHERSPARK-INPUT -i lo -j ACCEPT'; fi
    for b in "${BRIDGES[@]}"; do echo "-A AETHERSPARK-INPUT -i $b -j AETHERSPARK-DROP"; done
    if [ "$HOST" -eq 1 ]; then
        echo ':AETHERSPARK-OUTPUT - [0:0]'
        echo ':AETHERSPARK-HOST-DROP - [0:0]'
        echo "-A AETHERSPARK-HOST-DROP $(limit as-hostdrop6) -j LOG --log-prefix aetherspark-host-drop:"
        echo '-A AETHERSPARK-HOST-DROP -j DROP'
        echo '-A AETHERSPARK-INPUT -j AETHERSPARK-HOST-DROP'
        echo '-A AETHERSPARK-OUTPUT -o lo -j ACCEPT'
        echo '-A AETHERSPARK-OUTPUT -j AETHERSPARK-HOST-DROP'
    fi
    echo 'COMMIT'
}

# Jumps from built-in chains to ours: command, table, chain, target
JUMPS=(
    "iptables filter FORWARD DOCKER-USER"
    "iptables filter INPUT AETHERSPARK-INPUT"
    "iptables raw PREROUTING AETHERSPARK-SPOOF"
    "ip6tables filter FORWARD DOCKER-USER"
    "ip6tables filter INPUT AETHERSPARK-INPUT"
)
# Our chains, compared rule by rule in `check`: command, table, chain
CHAINS=(
    "iptables filter DOCKER-USER" "iptables filter AETHERSPARK-INPUT" "iptables filter AETHERSPARK-DROP"
    "iptables raw AETHERSPARK-SPOOF"
    "ip6tables filter DOCKER-USER" "ip6tables filter AETHERSPARK-INPUT" "ip6tables filter AETHERSPARK-DROP"
)
HOST_JUMPS=("iptables filter OUTPUT AETHERSPARK-OUTPUT" "ip6tables filter OUTPUT AETHERSPARK-OUTPUT")
HOST_CHAINS=(
    "iptables filter AETHERSPARK-OUTPUT" "iptables filter AETHERSPARK-HOST-DROP"
    "ip6tables filter AETHERSPARK-OUTPUT" "ip6tables filter AETHERSPARK-HOST-DROP"
)
if [ "$HOST" -eq 1 ]; then JUMPS+=("${HOST_JUMPS[@]}"); CHAINS+=("${HOST_CHAINS[@]}"); fi

# The first rule of a built-in chain jumps to ours; moved back to the top if something got ahead
jump_first() { # command, table, chain, target
    [ "$("$1" -w -t "$2" -S "$3" | sed -n 2p)" = "-A $3 -j $4" ] && return
    while "$1" -w -t "$2" -D "$3" -j "$4" 2>/dev/null; do :; done
    "$1" -w -t "$2" -I "$3" 1 -j "$4"
}

# An installed host's settings, unless the caller passes its own. Read only here, after the
# baseline: a broken file stops the script with the host already closed.
settings() {
    if [ -z "${AS_RESOLVERS:-}" ] && [ -r "$SETTINGS" ]; then
        set -a
        # shellcheck source=/dev/null
        . "$SETTINGS"
        set +a
    fi
    [ "$REQUIRE_HOST" != 1 ] || [ "$HOST" -eq 1 ] || die "host mode is required but $HOST_MODE_FILE is missing"
    : "${AS_RESOLVERS:?}" "${AS_HOME_SUBNETS:?}"
    AS_NAS_ADDR=${AS_NAS_ADDR:-}
    [ -z "$AS_NAS_ADDR" ] || : "${AS_NAS_PORT:?AS_NAS_PORT is needed with AS_NAS_ADDR}"
    [ "$HOST" -eq 1 ] || return 0
    : "${AS_LAN_IF:?host mode needs AS_LAN_IF}" "${AS_ROUTER:?host mode needs AS_ROUTER}" \
        "${AS_ADMIN_ADDR:?host mode needs AS_ADMIN_ADDR}"
    AS_UPS_PORT=${AS_UPS_PORT:-} AS_COLLECTOR=${AS_COLLECTOR:-}
    AS_OPS_PROXY=${AS_OPS_PROXY:-172.30.20.2:3128}
    AS_RESOLVER_USER=${AS_RESOLVER_USER:-systemd-resolve} AS_TIME_USER=${AS_TIME_USER:-systemd-timesync}
    AS_UPS_USER=${AS_UPS_USER:-nut} AS_APT_USER=${AS_APT_USER:-_apt}
    [ -z "$AS_UPS_PORT" ] || [ -n "$AS_NAS_ADDR" ] || die "AS_UPS_PORT needs AS_NAS_ADDR"
    if [ -n "$AS_COLLECTOR" ]; then
        : "${AS_COLLECTOR_USER:?AS_COLLECTOR_USER is needed with AS_COLLECTOR}"
        : "${AS_COLLECTOR_IF:?AS_COLLECTOR_IF, the bridge of the collector network, is needed with AS_COLLECTOR}"
    fi
    # Single addresses and plain names only: a range here would widen a rule, and `check` compares
    # against the same settings, so it would not notice
    local v
    for v in "$AS_ROUTER" "$AS_ADMIN_ADDR" ${AS_NAS_ADDR:+"$AS_NAS_ADDR"}; do
        [[ $v =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || die "not a single IPv4 address: $v"
    done
    for v in "$AS_OPS_PROXY" ${AS_COLLECTOR:+"$AS_COLLECTOR"}; do
        [[ $v =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}:[0-9]{1,5}$ ]] || die "not an IPv4 address:port: $v"
    done
    for v in "$AS_LAN_IF" ${AS_COLLECTOR_IF:+"$AS_COLLECTOR_IF"}; do
        [[ $v =~ ^[A-Za-z0-9_.-]{1,15}$ ]] || die "not an interface name: $v"
    done
}

# Host mode, or host mode required by the unit: DROP policies before any rule and before the
# settings are read, IPv6 too. FORWARD stays DROP for good; Docker keeps it so as well.
baseline() {
    [ "$HOST" -eq 1 ] || [ "$REQUIRE_HOST" = 1 ] || return 0
    local ipt c
    for ipt in iptables ip6tables; do
        for c in INPUT OUTPUT FORWARD; do "$ipt" -w -P "$c" DROP; done
    done
}

# Docker's nftables firewall backend (experimental in 28/29) has no DOCKER-USER: these rules would
# load but not be consulted. Checked before Docker runs from its configuration, after it from
# `docker info` (the field exists from Engine 28.1; earlier versions print nothing). `docker info`
# only while docker.service runs or starts: at boot this unit runs before Docker, and a call
# through Docker's socket would start Docker, which waits for this unit.
backend_ok() {
    local configured running state
    configured=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("firewall-backend", "iptables"))' \
        /etc/docker/daemon.json 2>/dev/null || echo iptables)
    [ "$configured" = iptables ] || { echo "daemon.json selects the $configured firewall backend" >&2; return 1; }
    configured=$(systemctl cat docker.service 2>/dev/null | grep -oE -- '--firewall-backend(=| +)[A-Za-z]+' | grep -oE '[A-Za-z]+$' || true)
    if [ -n "$configured" ] && [ "$configured" != iptables ]; then
        echo "docker.service sets --firewall-backend $configured" >&2; return 1
    fi
    state=$(systemctl show -p ActiveState --value docker.service 2>/dev/null) || return 0
    case "$state" in active | activating | reloading) ;; *) return 0 ;; esac
    running=$(docker info --format '{{with .FirewallBackend}}{{.Driver}}{{end}}' 2>/dev/null) || return 0
    case "$running" in "" | iptables) ;; *) echo "Docker runs the $running firewall backend" >&2; return 1 ;; esac
}

# Other firewall managers put their own rules in the same chains or switch Docker to firewalld
others_ok() {
    if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -x 'Status: active' >/dev/null; then
        echo "ufw is active" >&2; return 1
    fi
    if systemctl is-active --quiet firewalld.service 2>/dev/null; then
        echo "firewalld is active" >&2; return 1
    fi
}

# Creates the namespace and loads the rendered rules into it; the caller deletes it
scratch_load() {
    ip netns add "$1" && { rules_v4 | ip netns exec "$1" iptables-restore -w; } \
        && { rules_v6 | ip netns exec "$1" ip6tables-restore -w; }
}

apply() {
    baseline
    settings
    iptables -V | grep nf_tables >/dev/null || die "iptables is not the nf_tables variant ($(iptables -V))"
    backend_ok || die "Docker must use the iptables firewall backend"
    others_ok || die "another firewall manager is active"
    # --noflush keeps Docker's chains; the chains declared above are replaced as a whole
    rules_v4 | iptables-restore -w --noflush
    rules_v6 | ip6tables-restore -w --noflush
    # Before Docker's first start FORWARD has no jump yet; Docker puts it first on every start
    local j
    for j in "${JUMPS[@]}"; do
        # shellcheck disable=SC2086 # split on purpose
        jump_first $j
    done
    check
}

check() {
    settings
    local bad=0 j c ipt table chain ns
    for j in "${JUMPS[@]}"; do
        read -r ipt table chain c <<<"$j"
        [ "$("$ipt" -w -t "$table" -S "$chain" | sed -n 2p)" = "-A $chain -j $c" ] \
            || { echo "$ipt $table $chain: the jump to $c is not first" >&2; bad=1; }
    done

    # Host mode: DROP policies, and nothing in INPUT or OUTPUT but the jump to our chain. Deleted
    # tables take the policies with them, so this is also where a flushed ruleset shows.
    if [ "$HOST" -eq 1 ]; then
        for ipt in iptables ip6tables; do
            for chain in INPUT OUTPUT; do
                [ "$("$ipt" -w -S "$chain" 2>/dev/null)" = "$(printf -- '-P %s DROP\n-A %s -j AETHERSPARK-%s' "$chain" "$chain" "$chain")" ] \
                    || { echo "$ipt $chain: not the DROP policy with only the jump to AETHERSPARK-$chain" >&2; bad=1; }
            done
            # FORWARD: DROP, and only the jumps to DOCKER-USER (ours, first) and Docker's own chain
            [ "$("$ipt" -w -S FORWARD 2>/dev/null | sed -n 1p)" = "-P FORWARD DROP" ] \
                || { echo "$ipt FORWARD: the policy is not DROP" >&2; bad=1; }
            [ -z "$("$ipt" -w -S FORWARD 2>/dev/null | sed 1d \
                    | grep -vx -e '-A FORWARD -j DOCKER-USER' -e '-A FORWARD -j DOCKER-FORWARD' || true)" ] \
                || { echo "$ipt FORWARD: holds rules other than the jumps to DOCKER-USER and DOCKER-FORWARD" >&2; bad=1; }
        done
    fi

    backend_ok || bad=1
    others_ok || bad=1

    # Every rule as rendered: load the rules into a scratch network namespace, where iptables
    # prints them in the same normalised form, and compare chain by chain. Only the last line of
    # DOCKER-USER may be a bare `-j RETURN`, the one Docker before 28.2.2 appends (ours never ends
    # a chain that way); a RETURN anywhere else counts as a difference.
    ns=aetherspark-check-$$
    if scratch_load "$ns"; then
        for c in "${CHAINS[@]}"; do
            read -r ipt table chain <<<"$c"
            diff <(ip netns exec "$ns" "$ipt" -w -t "$table" -S "$chain") \
                 <("$ipt" -w -t "$table" -S "$chain" 2>/dev/null | sed '${/^-A DOCKER-USER -j RETURN$/d}') >/dev/null \
                || { echo "$ipt $table $chain: differs from the rendered rules" >&2; bad=1; }
        done
    else
        echo "could not render the rules in a scratch namespace" >&2; bad=1
    fi
    ip netns del "$ns" 2>/dev/null || true

    # Docker sends lookups for upstreams taken from the host's resolv.conf from the host's own
    # namespace, past these rules; upstreams set in daemon.json leave from the container's
    # namespace, so DOCKER-USER decides who may query them. With the userland proxy, published
    # ports would also answer on the host's IPv6 addresses, past the ingress rule.
    local want
    read -ra want <<<"$AS_RESOLVERS"
    [ "$(python3 - /etc/docker/daemon.json <<'EOF' 2>/dev/null
import json, sys
cfg = json.load(open(sys.argv[1]))
print(" ".join(cfg.get("dns", [])), cfg.get("userland-proxy", True) is False)
EOF
)" = "${want[*]} True" ] \
        || { echo "/etc/docker/daemon.json: needs \"dns\": [${want[*]}] and \"userland-proxy\": false" >&2; bad=1; }
    [ "$bad" -eq 0 ] || die "rules not in place"
    echo "aetherspark-egress-rules: in place"
}

# FORWARD keeps its DROP policy (Docker's own default); a second run, or one after the tables were
# deleted, finds some chains already gone
remove() {
    local ipt c
    for ipt in iptables ip6tables; do
        if [ "$HOST" -eq 1 ]; then
            for c in INPUT OUTPUT; do "$ipt" -w -P "$c" ACCEPT; done
            while "$ipt" -w -D OUTPUT -j AETHERSPARK-OUTPUT 2>/dev/null; do :; done
            "$ipt" -w -F AETHERSPARK-OUTPUT 2>/dev/null || true
            "$ipt" -w -X AETHERSPARK-OUTPUT 2>/dev/null || true
        fi
        while "$ipt" -w -D INPUT -j AETHERSPARK-INPUT 2>/dev/null; do :; done
        printf '*filter\n:DOCKER-USER - [0:0]\n-A DOCKER-USER -j RETURN\n:AETHERSPARK-INPUT - [0:0]\n:AETHERSPARK-DROP - [0:0]\nCOMMIT\n' \
            | "$ipt-restore" -w --noflush
        "$ipt" -w -X AETHERSPARK-INPUT 2>/dev/null || true
        "$ipt" -w -X AETHERSPARK-DROP 2>/dev/null || true
        if [ "$HOST" -eq 1 ]; then
            "$ipt" -w -F AETHERSPARK-HOST-DROP 2>/dev/null || true
            "$ipt" -w -X AETHERSPARK-HOST-DROP 2>/dev/null || true
        fi
    done
    while iptables -w -t raw -D PREROUTING -j AETHERSPARK-SPOOF 2>/dev/null; do :; done
    iptables -w -t raw -F AETHERSPARK-SPOOF 2>/dev/null || true
    iptables -w -t raw -X AETHERSPARK-SPOOF 2>/dev/null || true
    echo "aetherspark-egress-rules: removed"
}

# Owner matches need their users to exist, which a CI machine's may not: the lint proves that the
# rules load, so every owner match names root here
lint() {
    local mode nas nas_addr ns=aetherspark-lint-$$ bad=0
    # The example alone, never a host's own settings or defaults left in the environment
    unset AS_OPS_PROXY AS_COLLECTOR_IF AS_RESOLVER_USER AS_TIME_USER AS_UPS_USER AS_APT_USER AS_COLLECTOR_USER
    set -a
    # shellcheck source=/dev/null
    . "$(dirname "$0")/egress-rules.env.example"
    set +a
    AS_RESOLVER_USER=root AS_TIME_USER=root AS_UPS_USER=root AS_APT_USER=root AS_COLLECTOR_USER=root
    AS_COLLECTOR=${AS_COLLECTOR:-172.30.40.2:4317} AS_COLLECTOR_IF=${AS_COLLECTOR_IF:-as-obs-edge}
    nas_addr=$AS_NAS_ADDR
    for mode in 0 1; do
        HOST=$mode
        for nas in "$nas_addr" ""; do
            # The UPS flow needs the NAS address, so it goes with the backup flow
            AS_NAS_ADDR=$nas
            AS_UPS_PORT=${nas:+3493}
            settings
            if scratch_load "$ns"; then
                echo "L3 rules: load (host mode: $mode, backup flow: ${AS_NAS_ADDR:-none})"
            else
                echo "L3 rules: do not load (host mode: $mode, backup flow: ${AS_NAS_ADDR:-none})" >&2; bad=1
            fi
            ip netns del "$ns" 2>/dev/null || true
        done
    done
    [ "$bad" -eq 0 ] || die "lint failed"
}

case "${1:-}" in
    baseline) baseline ;;
    apply) apply ;;
    check) check ;;
    remove) remove ;;
    lint) lint ;;
    *) die "usage: $0 baseline|apply|check|remove|lint" ;;
esac
