#!/usr/bin/env bash
# Layer 3 of the egress control (docs/architecture/egress.md, "Layer 3 rules"): DOCKER-USER for
# forwarded traffic, host INPUT from Docker bridges, IPv6 from Docker bridges. Host OUTPUT on the
# Spark follows with the host role (WP6.2).
#
# Installed as /usr/local/sbin/aetherspark-egress-rules and run by aetherspark-egress-rules.service
# before docker.service. Needs Docker Engine with the iptables backend (iptables-nft); Docker keeps
# DOCKER-USER as it is across restarts and puts its jump first in FORWARD.
#
#   apply   replace the rules (atomically per address family)
#   check   exit 1 unless the rules are in place and Docker's DNS upstreams are set (see check)
#   remove  take the rules out again (tests only: the break-it test and CI cleanup)
#
# Settings, from the environment (on a host: /etc/aetherspark/egress-rules.env, values in ops.local/):
#   AS_RESOLVERS     upstream resolvers the proxies may query on port 53 (IPv4, space separated);
#                    also Docker's "dns" in /etc/docker/daemon.json
#   AS_HOME_SUBNETS  sources allowed to open TCP 443 to the `ingress` network (space separated)
#   AS_NAS_ADDR      backup target address for the `backup` network (empty: no backup flow)
#   AS_NAS_PORT      backup target port
set -euo pipefail

# Docker bridges: the default bridge, unnamed networks, and the stack's named networks
BRIDGES=(docker0 br-+ as-+)
# Not public: the proxies' way out never reaches these (RFC 6890 special-purpose ranges)
NOT_PUBLIC=0.0.0.0/8,10.0.0.0/8,100.64.0.0/10,127.0.0.0/8,169.254.0.0/16,172.16.0.0/12,192.0.0.0/24,192.0.2.0/24,192.168.0.0/16,198.18.0.0/15,198.51.100.0/24,203.0.113.0/24,224.0.0.0/4,240.0.0.0/4
LOG='-m limit --limit 20/minute --limit-burst 40 -j LOG --log-prefix aetherspark-drop:'

die() { echo "aetherspark-egress-rules: $*" >&2; exit 1; }

rules_v4() {
    echo '*filter'
    echo ':DOCKER-USER - [0:0]'
    echo ':AETHERSPARK-INPUT - [0:0]'
    echo ':AETHERSPARK-DROP - [0:0]'
    echo "-A AETHERSPARK-DROP $LOG"
    echo '-A AETHERSPARK-DROP -j DROP'

    # Forwarded traffic. RETURN hands a packet back to Docker's own chains, which still apply
    # their isolation (internal networks, one network from another); the last rule drops the rest.
    echo '-A DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN'
    local i o s r
    for i in "${BRIDGES[@]}"; do for o in "${BRIDGES[@]}"; do
        echo "-A DOCKER-USER -i $i -o $o -j RETURN"
    done; done
    for s in $AS_HOME_SUBNETS; do
        echo "-A DOCKER-USER -s $s -o as-ingress -p tcp -m conntrack --ctstate NEW --ctorigdstport 443 -j RETURN"
    done
    for r in $AS_RESOLVERS; do
        echo "-A DOCKER-USER -i as-out-+ -d $r -p udp --dport 53 -j RETURN"
        echo "-A DOCKER-USER -i as-out-+ -d $r -p tcp --dport 53 -j RETURN"
    done
    echo "-A DOCKER-USER -i as-out-+ -d $NOT_PUBLIC -j AETHERSPARK-DROP"
    echo '-A DOCKER-USER -i as-out-+ -p tcp --dport 443 -j RETURN'
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
}

# IPv6 is off on every network; nothing is forwarded and Docker bridges reach no host service.
# Forwarding uses DOCKER-USER here too: Docker re-inserts its own jump first in FORWARD on every
# start, so a separate chain would end up behind it.
rules_v6() {
    echo '*filter'
    echo ':DOCKER-USER - [0:0]'
    echo ':AETHERSPARK-INPUT - [0:0]'
    echo ':AETHERSPARK-DROP - [0:0]'
    echo "-A AETHERSPARK-DROP $LOG"
    echo '-A AETHERSPARK-DROP -j DROP'
    echo '-A DOCKER-USER -j AETHERSPARK-DROP'
    local b
    for b in "${BRIDGES[@]}"; do echo "-A AETHERSPARK-INPUT -i $b -j AETHERSPARK-DROP"; done
    echo 'COMMIT'
}

# The first rule of a built-in chain jumps to ours; moved back to the top if something got ahead
jump_first() { # command, chain, target
    [ "$("$1" -w -S "$2" | sed -n 2p)" = "-A $2 -j $3" ] && return
    while "$1" -w -D "$2" -j "$3" 2>/dev/null; do :; done
    "$1" -w -I "$2" 1 -j "$3"
}

apply() {
    : "${AS_RESOLVERS:?}" "${AS_HOME_SUBNETS:?}"
    AS_NAS_ADDR=${AS_NAS_ADDR:-}
    [ -z "$AS_NAS_ADDR" ] || : "${AS_NAS_PORT:?AS_NAS_PORT is needed with AS_NAS_ADDR}"
    iptables -V | grep -q nf_tables || die "iptables is not the nf_tables variant ($(iptables -V))"
    # --noflush keeps Docker's chains; the chains declared above are replaced as a whole
    rules_v4 | iptables-restore -w --noflush
    rules_v6 | ip6tables-restore -w --noflush
    # Before Docker's first start FORWARD has no jump yet; Docker puts it first on every start
    local ipt
    for ipt in iptables ip6tables; do
        jump_first "$ipt" FORWARD DOCKER-USER
        jump_first "$ipt" INPUT AETHERSPARK-INPUT
    done
    check
}

check() {
    local bad=0 ipt chain target
    for ipt in iptables ip6tables; do
        for chain in FORWARD:DOCKER-USER INPUT:AETHERSPARK-INPUT; do
            target=${chain#*:}; chain=${chain%:*}
            [ "$("$ipt" -w -S "$chain" | sed -n 2p)" = "-A $chain -j $target" ] \
                || { echo "$ipt $chain: the jump to $target is not first" >&2; bad=1; }
        done
        # The catch-all drop must come before anything Docker may append (Docker adds `-j RETURN`)
        "$ipt" -w -S DOCKER-USER | grep -v -- '^-N' | grep -v -- ' -j RETURN$' | tail -n 1 \
            | grep -qx -- '-A DOCKER-USER -j AETHERSPARK-DROP' \
            || { echo "$ipt DOCKER-USER: does not end in the catch-all drop" >&2; bad=1; }
    done
    # Docker sends lookups for upstreams taken from the host's resolv.conf from the host's own
    # namespace, past these rules. Upstreams set in daemon.json leave from the container's
    # namespace instead, so DOCKER-USER decides who may query them (install.sh sets them).
    local want
    read -ra want <<<"$AS_RESOLVERS"
    [ "$(python3 -c 'import json,sys; print(" ".join(json.load(open(sys.argv[1])).get("dns", [])))' \
        /etc/docker/daemon.json 2>/dev/null)" = "${want[*]}" ] \
        || { echo "/etc/docker/daemon.json: \"dns\" is not [${AS_RESOLVERS}]" >&2; bad=1; }
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
    echo "aetherspark-egress-rules: removed"
}

case "${1:-}" in
    apply) apply ;;
    check) : "${AS_RESOLVERS:?}"; check ;;
    remove) remove ;;
    *) die "usage: $0 apply|check|remove" ;;
esac
