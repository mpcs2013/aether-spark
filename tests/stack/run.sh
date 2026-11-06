#!/usr/bin/env bash
# step-ca and Traefik end to end (docs/architecture/compose-base.md, tests C3, C3b, C9a, C9b, C11).
# Needs Docker, age, python3, curl and openssl; runs in WSL2 and in CI (about 12 minutes).
# Everything is throw-away: its own Compose project, store, dev key and in-memory folder, and
# 3-minute certificates so that renewal and expiry happen within the run. Prints PASS/FAIL lines;
# never prints a secret. AS_STACK_KEEP=1 keeps the project after the run for a look. It works in its
# own temporary folder with absolute paths: on WSL2's /mnt/c the working folder can briefly vanish.
set -uo pipefail
repo="$(cd "$(dirname "$0")/../.." && pwd -P)" || exit 1

export COMPOSE_PROJECT_NAME=as-stack-test
export AETHERSPARK_UID="${AETHERSPARK_UID:-$(id -u)}" AETHERSPARK_GID="${AETHERSPARK_GID:-$(id -g)}"
export AETHERSPARK_PLACE=localhost
export AETHERSPARK_CA_LEAF_LIFETIME=3m AETHERSPARK_TRAEFIK_CERT_HOURS=1
work="$(mktemp -d)"
cd "$work" || exit 1
export AETHERSPARK_SECRETS_STORE="$work/store" AETHERSPARK_SECRETS_KEY="$work/key/dev.key"
export AETHERSPARK_SECRETS_DIR="${XDG_RUNTIME_DIR:-/dev/shm}/$COMPOSE_PROJECT_NAME/secrets"
overlay=(-f "$repo/tests/stack/compose.test.yaml")
name=whoami.aether.localhost
pg=postgres-auth.svc.aether.localhost
fails=0

dc() { docker compose -f "$repo/deploy/compose/stack/compose.yaml" "${overlay[@]}" "$@"; }
ca() { python3 -I "$repo/deploy/ca/ca.py" "$@"; }
# Runs Python code with the CA tool imported as `ca`; sys.argv[2:] are the extra arguments
ca_py() {
    local code=$1
    shift
    python3 -I -c "import sys; sys.path.insert(0, sys.argv[1] + '/deploy/ca'); import ca; $code" "$repo" "$@"
}
step_image() { ca_py 'print(ca.image())'; }
up() { # `just up` with the test route; its output goes to up.log
    bash "$repo/deploy/compose/stack/up.sh" "${overlay[@]}" >"$work/up.log" 2>&1 \
        || { tail -15 "$work/up.log"; return 1; }
}

pass() { printf 'PASS  %s\n' "$1"; }
fail() {
    printf 'FAIL  %s\n' "$1"
    if [ -n "${2:-}" ]; then printf '%s\n' "$2" | sed 's/^/      /'; fi
    fails=$((fails + 1))
}
check() { # description, command...: PASS when the command succeeds
    local d=$1 out
    shift
    if out=$("$@" 2>&1); then pass "$d"; else fail "$d" "$(printf '%s\n' "$out" | tail -20)"; fi
}
refuse() { # description, command...: PASS when the command fails
    local d=$1 out
    shift
    if out=$("$@" 2>&1); then fail "$d (it succeeded)" "$(printf '%s\n' "$out" | tail -3)"; else pass "$d"; fi
}
cleanup() {
    cd / || return
    if [ "${AS_STACK_KEEP:-0}" = 1 ]; then echo "kept: project $COMPOSE_PROJECT_NAME, $work"; return; fi
    docker rm -f "$COMPOSE_PROJECT_NAME-foreign" >/dev/null 2>&1
    dc --profile '*' down -v >/dev/null 2>&1
    python3 -I "$repo/deploy/secret-store/secret_store.py" clear >/dev/null
    rmdir "$(dirname "$AETHERSPARK_SECRETS_DIR")" 2>/dev/null
    rm -rf "$work"
}
trap cleanup EXIT

# A client on pki with the CA's certificates: the bootstrap step's container, its script unused
on_pki() { dc run --rm --no-deps -T --entrypoint sh cert-bootstrap -c "$1"; }
pg_serial() {
    on_pki 'step certificate inspect /tls/postgres-auth/tls.crt --format json' \
        | python3 -I -c 'import json, sys; print(json.load(sys.stdin)["serial_number"])'
}
served_serial() {
    echo | openssl s_client -connect 127.0.0.1:443 -servername "$name" 2>/dev/null \
        | openssl x509 -noout -serial
}
host_curl() { curl -sS --max-time 5 --resolve "$name:443:127.0.0.1" --cacert "$work/root.crt" "$@"; }
renewer_healthy() { # on failure: Docker's last health results, the certificate's dates, the log
    local id
    id="$(dc ps -q postgres-auth-renewer)"
    [ "$(docker inspect -f '{{.State.Health.Status}}' "$id")" = healthy ] && return 0
    docker inspect -f '{{range .State.Health.Log}}{{.Start}} exit {{.ExitCode}}{{"\n"}}{{end}}' "$id" | tail -4
    on_pki 'step certificate inspect /tls/postgres-auth/tls.crt --short' 2>&1 | tail -3
    date -u +'now %Y-%m-%dT%H:%M:%SZ'
    docker logs --tail 8 "$id" 2>&1
    return 1
}
pg_valid() {
    on_pki "step certificate verify /tls/postgres-auth/tls.crt --roots /ca-certs/root_ca.crt --host $pg"
}

if [ -n "$(docker ps -q --filter label=com.docker.compose.project=aetherspark)" ]; then
    echo "FAIL  the stack (project aetherspark) is running: it holds port 443 and the as- bridges;"
    echo "      run just down first"
    exit 1
fi

echo "--- setup: just up with the test route"
if ! up; then
    fail "just up reaches healthy" "$(dc logs --tail 15 2>&1 | tail -40)"
    exit 1
fi
pass "just up reaches healthy (CA initialised, postgres-auth bootstrapped, Traefik ordered)"
ca root >"$work/root.crt"
if [ -s "$work/root.crt" ]; then pass "ca root prints the root certificate"; else fail "ca root prints the root certificate"; exit 1; fi

echo "--- the CA's volume: no password, no root key, the intermediate key encrypted"
ca_volume_clean() {
    docker run --rm --network none --read-only --cap-drop ALL --user "$AETHERSPARK_UID:$AETHERSPARK_GID" \
        -v "${COMPOSE_PROJECT_NAME}_step-ca:/ca:ro" \
        -v "$AETHERSPARK_SECRETS_DIR/step-ca-password:/s/a:ro" \
        -v "$AETHERSPARK_SECRETS_DIR/step-ca-provisioner-postgres-auth:/s/b:ro" \
        --entrypoint sh "$(step_image)" -c '
        cd /ca
        files="$(find . -type f ! -path "./db/*" | sort | tr "\n" " ")"
        want="./certs/intermediate_ca.crt ./certs/root_ca.crt ./config/ca.json ./secrets/intermediate_ca_key "
        [ "$files" = "$want" ] || { echo "files: $files"; exit 1; }
        grep -q ENCRYPTED secrets/intermediate_ca_key || { echo "the intermediate key is not encrypted"; exit 1; }
        keys="$(find . -type f ! -path "./db/*" ! -name intermediate_ca_key -exec grep -l "PRIVATE KEY" {} +)"
        [ -z "$keys" ] || { echo "private keys in: $keys"; exit 1; }
        for s in /s/a /s/b; do
            if grep -rqF -f "$s" .; then echo "a password value is in the volume"; exit 1; fi
        done'
}
check "only the CA's certificates, its configuration and the encrypted intermediate key" ca_volume_clean

echo "--- C3: every listener presents a certificate from AetherSpark's CA with its own name"
check "step-ca presents step-ca.svc.aether.localhost, chained to the root" on_pki \
    'step certificate inspect "$CA_URL" --roots /ca-certs/root_ca.crt --format json | grep -q "\"step-ca.svc.aether.localhost\""'
traefik_ok() { [ "$(host_curl "https://$name/")" = OK ]; }
check "Traefik serves $name with a certificate from the CA (curl checks name and chain)" traefik_ok
issuer_ok() {
    echo | openssl s_client -connect 127.0.0.1:443 -servername "$name" 2>/dev/null \
        | openssl x509 -noout -issuer | grep -q 'AetherSpark Intermediate CA (localhost)'
}
check "Traefik's certificate is issued by the intermediate" issuer_ok
check "postgres-auth's certificate names $pg and verifies against the root" pg_valid
published() {
    [ "$(docker ps --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME" --format '{{.Ports}}' \
        | grep -o '[0-9.:]*->[0-9]*/tcp' | sort -u)" = '127.0.0.1:443->443/tcp' ]
}
check "only Traefik's 443 is published, on 127.0.0.1 (C4)" published

echo "--- C3b: clients refuse a foreign CA and a wrong name"
foreign_root='cd /tmp && step certificate create Foreign f.crt f.key --profile root-ca --no-password --insecure >/dev/null 2>&1'
refuse "the renewers' client (step) refuses step-ca under a foreign root" on_pki \
    "$foreign_root && step ca health --ca-url \"\$CA_URL\" --root f.crt"
ca_ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' "$(dc ps -q step-ca)" | awk '{print $1}')"
refuse "the renewers' client (step) refuses step-ca by address (not its name)" \
    on_pki "step ca health --ca-url https://$ca_ip:9000 --root /ca-certs/root_ca.crt"
refuse "curl refuses Traefik under the system roots" \
    curl -sS --max-time 5 --resolve "$name:443:127.0.0.1" "https://$name/"
refuse "curl refuses Traefik by address" curl -sS --max-time 5 --cacert "$work/root.crt" https://127.0.0.1/
refuse "an unknown name gets no TLS handshake (strict SNI)" curl -sS --max-time 5 \
    --resolve "nope.aether.localhost:443:127.0.0.1" --cacert "$work/root.crt" https://nope.aether.localhost/
foreign_traefik() { # Traefik's ACME client trusts the CA's root only
    on_pki "$foreign_root && cat f.crt" >"$work/foreign.crt" || return 1
    chmod 0644 "$work/foreign.crt"
    dc run -d --rm --no-deps --name "$COMPOSE_PROJECT_NAME-foreign" \
        -v "$work/foreign.crt:/foreign/root.crt:ro" \
        -e TRAEFIK_CERTIFICATESRESOLVERS_STEPCA_ACME_CACERTIFICATES=/foreign/root.crt \
        traefik >/dev/null || return 1
    local found=1
    for _ in $(seq 1 20); do
        if docker logs "$COMPOSE_PROJECT_NAME-foreign" 2>&1 | grep -q -i 'signed by unknown authority'; then
            found=0
            break
        fi
        sleep 2
    done
    docker rm -f "$COMPOSE_PROJECT_NAME-foreign" >/dev/null 2>&1
    return "$found"
}
check "Traefik's ACME client refuses step-ca under a foreign root" foreign_traefik

echo "--- C11: name constraints, per client (a test CA made with the real templates)"
c11="$work/c11"
mkdir -p "$c11" && chmod 0777 "$c11"
ca_py 'open(sys.argv[2] + "/root.tpl", "w").write(ca.ca_template("localhost", 1, True))
open(sys.argv[2] + "/int.tpl", "w").write(ca.ca_template("localhost", 0, False))' "$c11"
docker run --rm --network none -v "$c11:/c11" --entrypoint sh "$(step_image)" -c '
    set -e; cd /c11
    step certificate create "C11 root" root.crt root.key --template root.tpl --no-password --insecure
    step certificate create "C11 intermediate" int.crt int.key --template int.tpl \
        --ca root.crt --ca-key root.key --no-password --insecure
    for n in evil.example ok.aether.localhost; do
        step certificate create $n $n.crt $n.key --profile leaf --ca int.crt --ca-key int.key \
            --no-password --insecure --not-after 1h
        cat $n.crt int.crt > $n.chain
    done
    chmod 0644 ./*' >/dev/null 2>&1
# Without both chains every "refuses" below would pass on nothing
if [ -s "$c11/ok.aether.localhost.chain" ] && [ -s "$c11/evil.example.chain" ]; then
    pass "the C11 test CA is made with the real templates"
else
    fail "the C11 test CA is made with the real templates"
    exit 1
fi
c11_go() {
    docker run --rm --network none -v "$c11:/c11:ro" --entrypoint step "$(step_image)" \
        certificate verify "/c11/$1.chain" --roots /c11/root.crt
}
check "Go (step CLI; Traefik's library) accepts ok.aether.localhost" c11_go ok.aether.localhost
refuse "Go (step CLI; Traefik's library) refuses evil.example from the constrained intermediate" c11_go evil.example
c11_openssl() { openssl verify -CAfile "$c11/root.crt" -untrusted "$c11/int.crt" "$c11/$1.crt"; }
check "OpenSSL accepts ok.aether.localhost" c11_openssl ok.aether.localhost
refuse "OpenSSL refuses evil.example (outside the permitted subtree)" c11_openssl evil.example
c11_curl() { # serve the name's chain, then connect with curl trusting only the test root
    openssl s_server -quiet -accept 127.0.0.1:18443 -cert "$c11/$1.crt" -key "$c11/$1.key" \
        -cert_chain "$c11/int.crt" -www >/dev/null 2>&1 &
    local pid=$! rc
    sleep 1
    curl -sS --max-time 5 --resolve "$1:18443:127.0.0.1" --cacert "$c11/root.crt" "https://$1:18443/" >/dev/null
    rc=$?
    kill "$pid" 2>/dev/null
    wait "$pid" 2>/dev/null
    return "$rc"
}
check "curl accepts a server for ok.aether.localhost" c11_curl ok.aether.localhost
refuse "curl refuses a server for evil.example" c11_curl evil.example

echo "--- C9a: normal renewal (3-minute certificates)"
pg_first="$(pg_serial)"
tr_first="$(served_serial)"
errors=0
for _ in $(seq 1 30); do # 150 s: postgres-auth's renewer renews before 120 s, Traefik every minute
    host_curl "https://$name/" >/dev/null 2>&1 || errors=$((errors + 1))
    sleep 5
done
check "no failed connection to Traefik while its certificate was renewed" [ "$errors" -eq 0 ]
pg_renewed() { [ "$pg_first" != "$(pg_serial)" ]; }
check "postgres-auth's renewer renewed its certificate (new serial)" pg_renewed
tr_renewed() { [ "$tr_first" != "$(served_serial)" ]; }
check "Traefik renewed its certificate (new serial)" tr_renewed
check "the renewer is healthy" renewer_healthy
# The overnight case: stopped past the renewal point (75 % of the lifetime), not yet expired. A
# fresh certificate first, so that the stop's length is known.
dc stop postgres-auth-renewer >/dev/null 2>&1
on_pki 'rm -f /tls/postgres-auth/tls.crt' >/dev/null 2>&1
dc run --rm cert-bootstrap >/dev/null 2>&1
dc stop >/dev/null 2>&1
sleep 135
overdue() { on_pki 'step certificate needs-renewal /tls/postgres-auth/tls.crt --expires-in 25%'; }
check "after the stop the certificate is past its renewal point" overdue
check "... and not expired" pg_valid
pg_first="$(pg_serial)"
check "just up after the stop reaches healthy" up
check "the renewer renewed before it reported healthy" pg_renewed

echo "--- C9b: recovery after expiry"
dc stop >/dev/null 2>&1
sleep 190
refuse "after the stop the old certificate no longer verifies (expired)" pg_valid
rebootstrapped() { up && grep -q 'cert-bootstrap: postgres-auth issued' "$work/up.log"; }
check "just up re-bootstraps the expired certificate and reaches healthy" rebootstrapped
check "the new certificate verifies" pg_valid
# A short-lived intermediate, as if the 60-day warning had been missed
AETHERSPARK_CA_INTERMEDIATE_LIFETIME=1m ca renew-intermediate >/dev/null
sleep 70
refuse "ca status fails once the intermediate has expired" ca status
on_pki 'rm -f /tls/postgres-auth/tls.crt' >/dev/null 2>&1
refuse "with the intermediate expired, issuance fails visibly" dc run --rm cert-bootstrap
check "ca renew-intermediate (the runbook) signs a new one from the sealed root" ca renew-intermediate
check "just up then issues and reaches healthy" up
year() { ca status | grep -q 'valid for 36[45] more days'; }
check "ca status reports the new intermediate (about a year)" year

echo "stack-test: $fails failed"
[ "$fails" -eq 0 ]
