#!/usr/bin/env bash
# The Spark host role (deploy/ansible/) in a container with systemd as PID 1 (docs/architecture/
# spark-host.md, "Tests"): apply, apply again (nothing may change), check mode (nothing may
# differ), then break each setting an assertion guards and expect that assertion to fail. Runs on
# the desktop (Docker Desktop) and in CI; needs Docker and internet access (image build, packages).
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1
# Git Bash on Windows: container paths stay as written
export MSYS_NO_PATHCONV=1

name=aetherspark-host-role-test
image=aetherspark/host-role-test:local
fails=0
check() { # id, description, expected, actual
    if [ "$3" = "$4" ]; then printf 'PASS  %-4s %s\n' "$1" "$2"
    else printf 'FAIL  %-4s %s (expected %s, got %s)\n' "$1" "$2" "$3" "$4"; fails=$((fails + 1)); fi
}

docker build -q -t "$image" tests/host-role >/dev/null || { echo "role test image build failed"; exit 1; }
docker rm -f "$name" >/dev/null 2>&1
if [ "${AS_LAB_KEEP:-}" = 1 ]; then trap 'echo "kept: docker rm -f $name when done"' EXIT
else trap 'docker rm -f "$name" >/dev/null 2>&1' EXIT; fi
docker run -d --privileged --cgroupns=host -v /sys/fs/cgroup:/sys/fs/cgroup:rw --tmpfs /run --tmpfs /run/lock \
    --name "$name" "$image" >/dev/null || exit 1
inside() { docker exec "$name" bash -c "$1"; }
for _ in $(seq 60); do
    case "$(inside 'systemctl is-system-running 2>/dev/null')" in running | degraded) break ;; esac
    sleep 1
done

# The files the role needs, owned by root as on the Spark
inside 'mkdir -p /repo/deploy /repo/src'
for f in pyproject.toml uv.lock .python-version; do docker cp "$f" "$name:/repo/$f" >/dev/null || exit 1; done
docker cp deploy/ansible "$name:/repo/deploy/ansible" >/dev/null || exit 1
docker cp deploy/host "$name:/repo/deploy/host" >/dev/null || exit 1
docker cp src/python "$name:/repo/src/python" >/dev/null || exit 1
docker cp tests/host-role/host.yml "$name:/root/host.yml" >/dev/null || exit 1
inside 'chown -R root:root /repo /root/host.yml && chmod -R go-w /repo && chmod 0600 /root/host.yml'

# ansible-lint (pinned in uv.lock) on the playbook and the role; it prints its findings
lint=$(inside 'cd /repo/deploy/ansible && uv run --frozen --only-group host ansible-lint --offline spark.yml 2>&1')
lint_rc=$?
check L "ansible-lint finds nothing" 0 "$lint_rc"
[ "$lint_rc" -eq 0 ] || printf '%s\n' "$lint" | tail -n 40

# The role as on the Spark (run.sh), unsigned: the copy has no tags. Prints the play recap's counts.
role() {
    docker exec -e AS_ROLE_UNSIGNED=1 "$name" bash /repo/deploy/ansible/run.sh \
        -e spark_host_vars_file=/root/host.yml "$@" >/tmp/aetherspark-role.log 2>&1
    local rc=$?
    grep -oE 'ok=[0-9]+ +changed=[0-9]+ +unreachable=[0-9]+ +failed=[0-9]+' /tmp/aetherspark-role.log | tail -n 1 \
        | sed -E 's/ +/ /g; s/^/rc='"$rc"' /'
}
counts() { sed -E 's/ok=[0-9]+ //; s/unreachable=[0-9]+ //'; }

first=$(role)
check R1 "first run applies the role" "rc=0 failed=0" "$(echo "$first" | counts | sed -E 's/ changed=[0-9]+//')"
[ "${first%% *}" = rc=0 ] || { echo "--- first run's log:"; tail -n 40 /tmp/aetherspark-role.log; }
check R2 "second run changes nothing" "rc=0 changed=0 failed=0" "$(role | counts)"
check R3 "check mode finds no difference" "rc=0 changed=0 failed=0" "$(role --check --diff | counts)"

# Each break: the assertions (check mode, tag assert) must fail, and name what drifted
breaks=(
    "motd-news.timer unmasked|systemctl unmask motd-news.timer|systemctl mask motd-news.timer|not masked: motd-news.timer"
    "graphical boot|systemctl set-default graphical.target|systemctl set-default multi-user.target|the default target is graphical.target"
    "an earlier sshd file allows agent forwarding|echo AllowAgentForwarding yes >/etc/ssh/sshd_config.d/05-drift.conf|rm /etc/ssh/sshd_config.d/05-drift.conf|not in effect: allowagentforwarding no"
    "password login switched on|sed -i 's/^PasswordAuthentication no/PasswordAuthentication yes/' /etc/ssh/sshd_config.d/10-aetherspark.conf|sed -i 's/^PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config.d/10-aetherspark.conf|not in effect: passwordauthentication no"
    "an extra authorised key|echo 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExtcmFrZXlvbmx5Zm9ydGhldGVzdG5vdHJlYWw drift' >>/etc/ssh/authorized_keys/spark-admin|sed -i '\$d' /etc/ssh/authorized_keys/spark-admin|the key file holds 3 keys"
    "keys read from the account's own folder|echo 'AuthorizedKeysFile .ssh/authorized_keys' >/etc/ssh/sshd_config.d/05-drift.conf|rm /etc/ssh/sshd_config.d/05-drift.conf|not in effect: authorizedkeysfile /etc/ssh/authorized_keys/%u"
    "a unit that is not installed loses its mask|systemctl unmask passim.service|systemctl mask passim.service|not masked"
    "a member in the docker group|groupadd docker && usermod -aG docker spark-admin|groupdel docker|the docker group has members"
    "IPv6 switched on|sysctl -qw net.ipv6.conf.all.disable_ipv6=0|sysctl -qw net.ipv6.conf.all.disable_ipv6=1 net.ipv6.conf.default.disable_ipv6=1|disable_ipv6 (all, default): 0"
    "the age key readable by all|chmod 0644 /etc/aetherspark/age/host.key|chmod 0400 /etc/aetherspark/age/host.key|not root-only 0400"
    "node-exporter on every address|sed -i 's/127.0.0.1:9100/:9100/' /etc/default/prometheus-node-exporter && systemctl restart prometheus-node-exporter|sed -i 's/=:9100/=127.0.0.1:9100/' /etc/default/prometheus-node-exporter && systemctl restart prometheus-node-exporter|listens beyond loopback"
)
for b in "${breaks[@]}"; do
    IFS='|' read -r what break restore expect <<<"$b"
    inside "$break"
    sleep 1
    out=$(role --check --tags assert)
    if [ "${out%% *}" != rc=0 ] && grep -F -- "$expect" /tmp/aetherspark-role.log >/dev/null; then r=found; else r=missed; fi
    check A "assertions find: $what" found "$r"
    inside "$restore"
done
sleep 1
check A "all breaks undone: the assertions pass" "rc=0 changed=0 failed=0" "$(role --check --tags assert | counts)"

# A file the role writes, changed: check mode reports it (no assertion needed)
inside 'echo "DNSStubListener=no" >>/etc/systemd/resolved.conf.d/90-aetherspark.conf'
drift=$(role --check --diff | counts)
case "$drift" in *" changed=0 "*) r=no ;; *" changed="*) r=yes ;; *) r=no ;; esac
check D "check mode finds a changed resolver file" yes "$r"
inside "sed -i '\$d' /etc/systemd/resolved.conf.d/90-aetherspark.conf"
check D "the file restored: check mode finds no difference" "rc=0 changed=0 failed=0" "$(role --check | counts)"

# run.sh's gates (without AS_ROLE_UNSIGNED): a copy of the checkout as a git repository, a
# throwaway signing key as the only allowed signer; --version stands in for a run of the role
# shellcheck disable=SC2016 # expands inside the container
inside 'set -e
    rm -rf /trust && cp -a /repo /trust && rm -rf /trust/.venv && cd /trust
    git init -q && git config user.email test@example.invalid && git config user.name test
    git config gpg.format ssh && ssh-keygen -q -t ed25519 -N "" -f /root/tagkey && ssh-keygen -q -t ed25519 -N "" -f /root/otherkey
    git config user.signingkey /root/tagkey.pub && git add -A && git commit -qm test && git tag -s -m test v0.0.1-test
    echo "test@example.invalid namespaces=\"git\" $(cat /root/tagkey.pub)" >/etc/aetherspark/allowed_signers'
gate() { # what to change first (run in /trust); prints ok when run.sh lets the run through
    if inside "cd /trust && { $1; } && deploy/ansible/run.sh --version 2>&1" | grep '^ansible-playbook' >/dev/null; then echo ok; else echo refused; fi
}
check T "a release tag signed with the allowed key" ok "$(gate 'true')"
check T "a commit without a tag" refused "$(gate 'git commit -q --allow-empty -m next')"
check T "a tag without a signature" refused "$(gate 'git tag v0.0.2-test')"
check T "a tag signed with another key" refused \
    "$(gate 'git -c user.signingkey=/root/otherkey.pub tag -s -m other v0.0.3-test HEAD~0 && git tag -d v0.0.2-test >/dev/null')"
inside 'cd /trust && git tag -d v0.0.3-test >/dev/null && git reset -q --hard HEAD~1'
check T "back at the signed tag" ok "$(gate 'true')"
check T "a checkout with local changes" refused "$(gate 'echo "# changed" >>deploy/ansible/spark.yml')"
inside 'cd /trust && git checkout -q -- deploy/ansible/spark.yml'
check T "a file another account owns" refused "$(gate 'chown nobody deploy/ansible/spark.yml')"
inside 'chown root /trust/deploy/ansible/spark.yml'
check T "a file others may write" refused "$(gate 'chmod o+w deploy/ansible/spark.yml')"
inside 'chmod o-w /trust/deploy/ansible/spark.yml'

echo "not in this container (the Spark's own tests): radios, held packages, Ubuntu Pro news,"
echo "the LAN connection and resolv.conf, Docker's proxy, the UPS client, the rules (the host lab)"
if [ "$fails" -eq 0 ]; then echo "host role: all passed"; else echo "host role: $fails failed"; fi
[ "$fails" -eq 0 ]
