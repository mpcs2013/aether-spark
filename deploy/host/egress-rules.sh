#!/usr/bin/env bash
# Layer 3 of the egress control (docs/architecture/egress.md, "Layer 3 rules"): DOCKER-USER for
# forwarded traffic, host INPUT from Docker bridges, a source-address check on Docker bridges, IPv6
# from Docker bridges. Host OUTPUT on the Spark follows with the host role (WP6.2).
#
# Installed as /usr/local/sbin/aetherspark-egress-rules and run by aetherspark-egress-rules.service
# before docker.service. Needs Docker Engine with the iptables backend (iptables-nft); Docker keeps
# DOCKER-USER as it is across restarts and puts its jump first in FORWARD.
#
#   apply   replace the rules (atomically per address family)
#   check   exit 1 unless every rule is exactly as rendered here and Docker's settings match
#   remove  take the rules out again (tests only: the break-it test and CI cleanup)
#   lint    load the rules rendered from the example settings, with and without a backup flow, in
#           a scratch network namespace (lint L3, from the repository); changes nothing on the host
#
# Settings, from the environment or else /etc/aetherspark/egress-rules.env (values in ops.local/):
#   AS_RESOLVERS     upstream resolvers the proxies may query on port 53 (IPv4, space separated);
#                    also Docker's "dns" in /etc/docker/daemon.json
#   AS_HOME_SUBNETS  sources allowed to open TCP 443 to the `ingress` network (space separated)
#   AS_NAS_ADDR      backup target address for the `backup` network (empty: no backup flow)
#   AS_NAS_PORT      backup target port
set -euo pipefail

# An installed host's settings, unless the caller passes its own
SETTINGS=/etc/aetherspark/egress-rules.env
if [ -z "${AS_RESOLVERS:-}" ] && [ -r "$SETTINGS" ]; then
    set -a
    # shellcheck source=/dev/null
    . "$SETTINGS"
    set +a
fi

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
        echo "-A DOCKER-USER -i as-backup -d $AS_NAS_ADDR -p tcp --dport $AS_NAS_PORT -j RETURN"
    fi
    # Everything else: other ports and addresses, other bridges, and forwarding between two
    # non-Docker interfaces (the Spark is multi-homed)
    echo '-A DOCKER-USER -j AETHERSPARK-DROP'

    # Host services are not reachable from containers; replies to the host's own connections are
    echo '-A AETHERSPARK-INPUT -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN'
    for i in "${BRIDGES[@]}"; do echo "-A AETHERSPARK-INPUT -i $i -j AETHERSPARK-DROP"; done
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

# IPv6 is off on every network; nothing is forwarded and Docker bridges reach no host service.
# Forwarding uses DOCKER-USER here too: Docker re-inserts its own jump first in FORWARD on every
# start, so a separate chain would end up behind it.
rules_v6() {
    local b
    echo '*filter'
    echo ':DOCKER-USER - [0:0]'
    echo ':AETHERSPARK-INPUT - [0:0]'
    echo ':AETHERSPARK-DROP - [0:0]'
    echo "-A AETHERSPARK-DROP $(limit as-drop6) -j LOG --log-prefix aetherspark-drop:"
    echo '-A AETHERSPARK-DROP -j DROP'
    echo '-A DOCKER-USER -j AETHERSPARK-DROP'
    for b in "${BRIDGES[@]}"; do echo "-A AETHERSPARK-INPUT -i $b -j AETHERSPARK-DROP"; done
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

# The first rule of a built-in chain jumps to ours; moved back to the top if something got ahead
jump_first() { # command, table, chain, target
    [ "$("$1" -w -t "$2" -S "$3" | sed -n 2p)" = "-A $3 -j $4" ] && return
    while "$1" -w -t "$2" -D "$3" -j "$4" 2>/dev/null; do :; done
    "$1" -w -t "$2" -I "$3" 1 -j "$4"
}

settings() {
    : "${AS_RESOLVERS:?}" "${AS_HOME_SUBNETS:?}"
    AS_NAS_ADDR=${AS_NAS_ADDR:-}
    [ -z "$AS_NAS_ADDR" ] || : "${AS_NAS_PORT:?AS_NAS_PORT is needed with AS_NAS_ADDR}"
}

# Docker's nftables firewall backend (experimental in 28/29) has no DOCKER-USER: these rules would
# load but not be consulted. Checked before Docker runs from its configuration, after it from
# `docker info` (the field exists from Engine 28.1; earlier versions print nothing).
backend_ok() {
    local configured running
    configured=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("firewall-backend", "iptables"))' \
        /etc/docker/daemon.json 2>/dev/null || echo iptables)
    [ "$configured" = iptables ] || { echo "daemon.json selects the $configured firewall backend" >&2; return 1; }
    if systemctl cat docker.service 2>/dev/null | grep -- '--firewall-backend' >/dev/null; then
        echo "docker.service sets --firewall-backend" >&2; return 1
    fi
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

    backend_ok || bad=1
    others_ok || bad=1

    # Every rule as rendered: load the rules into a scratch network namespace, where iptables
    # prints them in the same normalised form, and compare chain by chain. The trailing `-j RETURN`
    # Docker before 28.2.2 appends to DOCKER-USER is ignored (ours never ends a chain that way).
    ns=aetherspark-check-$$
    if scratch_load "$ns"; then
        for c in "${CHAINS[@]}"; do
            read -r ipt table chain <<<"$c"
            diff <(ip netns exec "$ns" "$ipt" -w -t "$table" -S "$chain") \
                 <("$ipt" -w -t "$table" -S "$chain" 2>/dev/null | grep -vx -- "-A $chain -j RETURN") >/dev/null \
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

remove() {
    local ipt
    for ipt in iptables ip6tables; do
        while "$ipt" -w -D INPUT -j AETHERSPARK-INPUT 2>/dev/null; do :; done
        printf '*filter\n:DOCKER-USER - [0:0]\n-A DOCKER-USER -j RETURN\n:AETHERSPARK-INPUT - [0:0]\n:AETHERSPARK-DROP - [0:0]\nCOMMIT\n' \
            | "$ipt-restore" -w --noflush
        "$ipt" -w -X AETHERSPARK-INPUT
        "$ipt" -w -X AETHERSPARK-DROP
    done
    while iptables -w -t raw -D PREROUTING -j AETHERSPARK-SPOOF 2>/dev/null; do :; done
    iptables -w -t raw -F AETHERSPARK-SPOOF 2>/dev/null || true
    iptables -w -t raw -X AETHERSPARK-SPOOF 2>/dev/null || true
    echo "aetherspark-egress-rules: removed"
}

lint() {
    local nas ns=aetherspark-lint-$$ bad=0
    set -a
    # shellcheck source=/dev/null
    . "$(dirname "$0")/egress-rules.env.example"
    set +a
    for nas in "$AS_NAS_ADDR" ""; do
        AS_NAS_ADDR=$nas
        settings
        if scratch_load "$ns"; then
            echo "L3 rules: load (backup flow: ${AS_NAS_ADDR:-none})"
        else
            echo "L3 rules: do not load (backup flow: ${AS_NAS_ADDR:-none})" >&2; bad=1
        fi
        ip netns del "$ns" 2>/dev/null || true
    done
    [ "$bad" -eq 0 ] || die "lint failed"
}

case "${1:-}" in
    apply) apply ;;
    check) check ;;
    remove) remove ;;
    lint) lint ;;
    *) die "usage: $0 apply|check|remove|lint" ;;
esac
