#!/usr/bin/env bash
# Installs the layer-3 egress rules on a Linux host running Docker Engine (the Spark's host role,
# WP6.2, and the CI egress job): the rule script, its systemd unit, the docker.service drop-in and
# Docker's DNS upstreams, then applies the rules. Usage, as root: deploy/host/install.sh <settings>
# (see egress-rules.env.example; the real values are in ops.local/).
set -euo pipefail
cd "$(dirname "$0")" || exit 1

env_file=${1:?usage: $0 <settings file>}
[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }
systemctl cat docker.service >/dev/null 2>&1 || { echo "no docker.service: Docker Engine with systemd needed" >&2; exit 1; }
# shellcheck source=/dev/null
. "$env_file"
: "${AS_RESOLVERS:?}"

install -D -m 0755 egress-rules.sh /usr/local/sbin/aetherspark-egress-rules
install -D -m 0644 "$env_file" /etc/aetherspark/egress-rules.env
install -D -m 0644 aetherspark-egress-rules.service /etc/systemd/system/aetherspark-egress-rules.service
install -D -m 0644 docker-check-egress-rules.conf /etc/systemd/system/docker.service.d/aetherspark-egress-rules.conf

# Docker's DNS upstreams in daemon.json, so containers' lookups leave from their own namespace and
# pass DOCKER-USER (see `check` in egress-rules.sh); other daemon.json settings are kept
dns_changed=$(python3 - "$AS_RESOLVERS" <<'EOF'
import json, os, sys
path = "/etc/docker/daemon.json"
cfg = json.load(open(path)) if os.path.exists(path) and os.path.getsize(path) else {}
dns = sys.argv[1].split()
if cfg.get("dns") == dns:
    print("no")
else:
    cfg["dns"] = dns
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    print("yes")
EOF
)

systemctl daemon-reload
systemctl enable aetherspark-egress-rules.service
# Applies now, with Docker running; from the next boot it runs before Docker
systemctl restart aetherspark-egress-rules.service
# Docker reads daemon.json at start only; its start re-applies the rules (drop-in)
[ "$dns_changed" = no ] || systemctl restart docker.service
/usr/local/sbin/aetherspark-egress-rules check
