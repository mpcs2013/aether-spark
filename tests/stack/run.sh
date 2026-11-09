#!/usr/bin/env bash
# step-ca, Traefik and postgres-auth end to end (docs/architecture/compose-base.md, tests C3, C3b,
# C5 for postgres-auth, C9a, C9b, C11).
# Needs Docker, age, python3, curl and openssl; runs in WSL2 and in CI (about 13 minutes).
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
# postgres-auth re-reads its certificate files every 20 s instead of every hour, Keycloak every 35 s
# (its minimum is above 30 s)
export AETHERSPARK_PG_RELOAD_SECONDS=20 AETHERSPARK_KC_RELOAD_PERIOD=35s
work="$(mktemp -d)"
cd "$work" || exit 1
export AETHERSPARK_SECRETS_STORE="$work/store" AETHERSPARK_SECRETS_KEY="$work/key/dev.key"
export AETHERSPARK_SECRETS_DIR="${XDG_RUNTIME_DIR:-/dev/shm}/$COMPOSE_PROJECT_NAME/secrets"
overlay=(-f "$repo/tests/stack/compose.test.yaml")
name=whoami.aether.localhost
auth=auth.aether.localhost
admin=auth-admin.aether.localhost
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

# A database client on db-auth: psql in postgres-auth's own image and service definition (its
# secrets, its certificate volume), with $work/pg at /test. Arguments: user, password source
# (keycloak, superuser or wrong), connection settings, SQL. The password goes through a password
# file in the container's in-memory /tmp, never through an argument.
mkdir -p "$work/pg"
cat >"$work/pg/client.sh" <<'EOF'
set -euo pipefail
user=$1 source=$2 conninfo=$3 sql=$4
{
    printf '*:*:*:%s:' "$user"
    case $source in
        keycloak) cat /run/secrets/keycloak-db-password ;;
        superuser) cat /run/secrets/postgres-auth-superuser-password ;;
        *) printf 'not-the-password' ;;
    esac
    printf '\n'
} >/tmp/pgpass
chmod 0600 /tmp/pgpass
PGPASSFILE=/tmp/pgpass psql "user=$user connect_timeout=5 $conninfo" -X -q -t -A -v ON_ERROR_STOP=1 -c "$sql"
EOF
on_db() { dc run --rm --no-deps -T --entrypoint bash -v "$work/pg:/test:ro" postgres-auth "$@"; }
pgc() { on_db /test/client.sh "$@"; }
pg_tls="host=$pg dbname=keycloak sslmode=verify-full sslrootcert=/test/root.crt"
pg_is() { # expected output, then pgc's arguments
    local want=$1 got
    shift
    got="$(pgc "$@")" || return 1
    [ "$got" = "$want" ] || { echo "got: $got"; return 1; }
}
# The certificate a service serves is the one in its volume (after the renewer's last renewal).
# Postgres' and Keycloak's images have no openssl command, so Python connects (for Postgres with its
# SSLRequest first), in the Python image the egress tests pin, as the stack user. With a CA file it
# also verifies the chain and the name. Arguments: host, port, postgres (yes/no), CA file or "-".
cat >"$work/pg/tls.py" <<'EOF'
import hashlib, socket, ssl, struct, sys
host, port, postgres, cafile = sys.argv[1], int(sys.argv[2]), sys.argv[3] == "yes", sys.argv[4]
with open("/tls/tls.crt", encoding="ascii") as f:
    pem = f.read().split("-----END CERTIFICATE-----")[0] + "-----END CERTIFICATE-----\n"
in_file = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()
raw = socket.create_connection((host, port), timeout=5)
if postgres:
    raw.sendall(struct.pack("!ii", 8, 80877103))  # SSLRequest
    if raw.recv(1) != b"S":
        sys.exit("no TLS offered")
if cafile == "-":
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # only the fingerprint is compared
else:
    context = ssl.create_default_context(cafile=cafile)  # chain to this root and the host's name
with context.wrap_socket(raw, server_hostname=host) as tls:
    served = hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest()
if served != in_file:
    sys.exit(f"file {in_file[:16]}, served {served[:16]}")
EOF
py_image="$(sed -n 's/^ *image: *\(python:[^ ]*\) *$/\1/p' "$repo/tests/egress/compose.layer3.yaml" | head -1)"
tls_py() { # network, certificate volume, tls.py's arguments
    local net=$1 vol=$2
    shift 2
    docker run --rm --network "${COMPOSE_PROJECT_NAME}_$net" --read-only --cap-drop ALL \
        --user "$AETHERSPARK_UID:$AETHERSPARK_GID" \
        -v "${COMPOSE_PROJECT_NAME}_$vol:/tls:ro" -v "$work/pg:/test:ro" \
        "$py_image" python3 -I /test/tls.py "$@"
}
pg_serves_current() { tls_py db-auth postgres-auth-tls "$pg" 5432 yes -; }
pg_reloaded() { # within two reload periods
    for _ in $(seq 1 9); do pg_serves_current >/dev/null 2>&1 && return 0; sleep 5; done
    pg_serves_current
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
pass "just up reaches healthy (CA initialised, postgres-auth bootstrapped and started, Traefik ordered)"
ca root >"$work/root.crt"
[ -n "$py_image" ] || { fail "the egress tests' Python image is found (tests/egress/compose.layer3.yaml)"; exit 1; }
cp "$work/root.crt" "$work/pg/root.crt"
chmod 0644 "$work/pg/"*
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
traefik_ok() { [ "$(host_curl "https://$name/" 2>/dev/null)" = OK ]; }
# Traefik is healthy before its ACME order completes; give the order up to 90 s (amd64 CI once lost
# this race, 2026-11-07)
traefik_ready() {
    for _ in $(seq 1 45); do traefik_ok && return 0; sleep 2; done
    host_curl "https://$name/"   # the last error, for the report
    return 1
}
check "Traefik serves $name with a certificate from the CA (curl checks name and chain)" traefik_ready
# After a restart Traefik orders each name's certificate anew, one after the other: wait for all of
# them, not only the test page's (amd64 CI once met the admin name still without one, 2026-11-08)
all_names_ready() {
    local h
    for h in "$name" "$auth" "$admin"; do
        for _ in $(seq 1 45); do
            curl -sS -o /dev/null --max-time 5 --resolve "$h:443:127.0.0.1" --cacert "$work/root.crt" \
                "https://$h/" 2>/dev/null && continue 2
            sleep 2
        done
        echo "$h has no certificate yet"
        return 1
    done
}
check "Traefik serves all its names ($name, $auth, $admin)" all_names_ready
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
    on_pki "$foreign_root && cat f.crt" >"$work/foreign.crt" 2>/dev/null || return 1
    chmod 0644 "$work/foreign.crt"
    # A second Traefik cannot run beside the real one: it takes the same fixed address on auth-front
    dc stop traefik >/dev/null 2>&1
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
dc up -d --no-deps --wait traefik >/dev/null 2>&1
check "the stack's Traefik is back, with all its names" all_names_ready

echo "--- postgres-auth: TLS only, Keycloak's role only (C3, C3b), no secret in its metadata (C5)"
on_pki "$foreign_root && cat f.crt" >"$work/pg/foreign.crt" 2>/dev/null && chmod 0644 "$work/pg/foreign.crt"
# Without a foreign root the "foreign root" refusal below would pass on nothing
grep -q 'BEGIN CERTIFICATE' "$work/pg/foreign.crt" || { fail "a foreign root for the database client"; exit 1; }
check "keycloak signs in over TLS checked with the CA's root and the name (verify-full)" \
    pg_is keycloak keycloak keycloak "$pg_tls" "select current_user"
check "the connection is encrypted, TLS 1.2 or newer" pg_is t keycloak keycloak "$pg_tls" \
    "select ssl and version in ('TLSv1.2', 'TLSv1.3') from pg_stat_ssl where pid = pg_backend_pid()"
check "keycloak owns its database (creates a table)" pgc keycloak keycloak "$pg_tls" \
    "create table stack_test_probe (id int)"
refuse "a plain connection is refused (hostssl only)" \
    pgc keycloak keycloak "host=$pg dbname=keycloak sslmode=disable" "select 1"
refuse "a wrong password is refused" pgc keycloak wrong "$pg_tls" "select 1"
refuse "the superuser cannot sign in over the network, even with its password" \
    pgc postgres superuser "host=$pg dbname=postgres sslmode=verify-full sslrootcert=/test/root.crt" "select 1"
refuse "keycloak cannot open another database" \
    pgc keycloak keycloak "host=$pg dbname=postgres sslmode=verify-full sslrootcert=/test/root.crt" "select 1"
refuse "libpq (verify-full) refuses postgres-auth under a foreign root" \
    pgc keycloak keycloak "host=$pg dbname=keycloak sslmode=verify-full sslrootcert=/test/foreign.crt" "select 1"
pg_ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' "$(dc ps -q postgres-auth)" | awk '{print $1}')"
refuse "libpq (verify-full) refuses postgres-auth by address (not its name)" \
    pgc keycloak keycloak "host=$pg_ip dbname=keycloak sslmode=verify-full sslrootcert=/test/root.crt" "select 1"
hba_matches() { # pg_hba.conf's keycloak line admits exactly the db-auth network's subnet
    local net hba
    net="$(docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}' "${COMPOSE_PROJECT_NAME}_db-auth")"
    hba="$(awk '$1 == "hostssl" && $2 == "keycloak" {print $4}' "$repo/deploy/compose/stack/postgres-auth/pg_hba.conf")"
    [ -n "$net" ] && [ "$net" = "$hba" ] || { echo "db-auth $net, pg_hba.conf $hba"; return 1; }
}
check "pg_hba.conf admits the db-auth network's subnet, nothing wider" hba_matches
pg_secrets_hidden() { # in no process environment of the container, not in inspect, not in its log
    local id s
    id="$(dc ps -q postgres-auth)"
    docker exec "$id" bash -c '
        for s in /run/secrets/*; do
            for e in /proc/[0-9]*/environ; do
                if tr "\0" "\n" <"$e" 2>/dev/null | grep -qF -f "$s"; then echo "$s in $e"; exit 1; fi
            done
        done' || return 1
    for s in postgres-auth-superuser-password keycloak-db-password; do
        if docker inspect "$id" | grep -qF -f "$AETHERSPARK_SECRETS_DIR/$s"; then echo "$s in inspect"; return 1; fi
        if docker logs "$id" 2>&1 | grep -qF -f "$AETHERSPARK_SECRETS_DIR/$s"; then echo "$s in the log"; return 1; fi
    done
}
check "no secret value in postgres-auth's process environments, inspect output or log (C5)" pg_secrets_hidden
# Within a reload period: with 3-minute certificates the renewer may have just replaced the files
check "postgres-auth serves the certificate in its volume" pg_reloaded

echo "--- Keycloak: behind Traefik only, admin refused on the sign-in name (C3, C3b, C5, C10)"
kc=keycloak.svc.aether.localhost
via() { # name, path, curl options...: prints the status code; the body goes to $work/kc.body
    local h=$1 p=$2
    shift 2
    curl -sS --max-time 10 --path-as-is --resolve "$h:443:127.0.0.1" --cacert "$work/root.crt" \
        -o "$work/kc.body" -w '%{http_code}' "$@" "https://$h$p"
}
answers() { # code, name, path, curl options...
    local want=$1 got
    shift
    got="$(via "$@")" || return 1
    [ "$got" = "$want" ] || { echo "status $got"; head -c 200 "$work/kc.body"; return 1; }
}
traefik_refuses() { # name, path: Traefik answers itself, so Keycloak never sees the request
    local got
    got="$(via "$@")" || return 1
    case $got in
        404) grep -qx '404 page not found' "$work/kc.body" || { echo "404, not Traefik's"; return 1; } ;;
        400) [ ! -s "$work/kc.body" ] || { echo "400 with a body, not Traefik's"; return 1; } ;;
        *) echo "status $got"; return 1 ;;
    esac
}
issuer_ok() {
    answers 200 "$auth" /realms/aetherspark/.well-known/openid-configuration || return 1
    python3 -I -c 'import json, sys; d = json.load(open(sys.argv[1])); sys.exit(d["issuer"] != sys.argv[2])' \
        "$work/kc.body" "https://$auth/realms/aetherspark"
}
check "discovery through Traefik names the issuer https://$auth/realms/aetherspark" issuer_ok
check "the realm's keys (JWKS) through Traefik" answers 200 "$auth" /realms/aetherspark/protocol/openid-connect/certs
check "the sign-in page through Traefik" answers 200 "$auth" \
    "/realms/aetherspark/protocol/openid-connect/auth?client_id=account-console&response_type=code&scope=openid&code_challenge=$(printf 'x%.0s' $(seq 1 43))&code_challenge_method=S256&redirect_uri=https://$auth/realms/aetherspark/account/" -L
for p in /admin/ /admin/master/console/ /admin/realms/aetherspark /realms/master/ \
    /realms/master/protocol/openid-connect/token /realms/aetherspark/../master/ '//admin/' \
    '/realms/aetherspark/%2e%2e/%2e%2e/admin/' '/realms/aetherspark/..%2fmaster/' '/%61dmin/' \
    /health/ready /metrics /; do
    check "Traefik refuses $p on $auth" traefik_refuses "$auth" "$p"
done
semicolon() { # passes Traefik's path rule, so Keycloak's own check must refuse it
    answers 400 "$auth" '/realms/aetherspark/..;/..;/admin/' && grep -q invalidCharacter "$work/kc.body"
}
check "a ..;/ path is refused by Keycloak's invalid-character check, never an admin page" semicolon
check "Traefik refuses the sign-in realm on $admin" traefik_refuses "$admin" /realms/aetherspark/
check "the admin console on $admin from the allowed source" answers 200 "$admin" /admin/master/console/
kc_tls() { tls_py auth-front keycloak-tls "$kc" 8443 no "$@"; }
kc_presents() { # within two reload periods: with 3-minute certificates a renewal may have just happened
    for _ in $(seq 1 16); do kc_tls /test/root.crt >/dev/null 2>&1 && return 0; sleep 5; done
    kc_tls /test/root.crt
}
check "Keycloak presents $kc, chained to the root (Traefik's view)" kc_presents
kc_foreign() { # refused for the right reason: the chain, not a renewal race
    local out
    if out="$(kc_tls /test/foreign.crt 2>&1)"; then echo "accepted"; return 1; fi
    printf '%s' "$out" | grep -q CERTIFICATE_VERIFY_FAILED || { printf '%s\n' "$out" | tail -2; return 1; }
}
check "a client trusting a foreign root refuses Keycloak (certificate verify failed)" kc_foreign
kc_listens() { # nothing listens but 8443 on its auth-front address, and loopback (health on 9000,
    # Java's own helpers, Docker's resolver); so no cluster port. The image has no awk: bash reads the
    # kernel's socket tables (state 0A = listening), Python judges them here
    docker exec "$(dc ps -q keycloak)" bash -c '
        cat /proc/net/tcp /proc/net/tcp6 2>/dev/null | while read -r _ local _ state _; do
            if [ "$state" = 0A ]; then echo "$local"; fi
        done' | python3 -I "$work/pg/listeners.py"
}
cat >"$work/pg/listeners.py" <<'EOF'
import sys

want = {"8443 on auth-front", "9000 on loopback"}
seen = set()
for line in sys.stdin.read().split():
    address, port = line.split(":")
    ipv4 = address[-8:]  # an IPv4 or IPv4-mapped address, in the kernel's byte order
    if address[:-8].strip("0") not in ("", "FFFF"):
        sys.exit(f"an IPv6 listener: {line}")
    if ipv4 == "033D1EAC" and port == "20FB":  # 172.30.61.3:8443
        seen.add("8443 on auth-front")
    elif ipv4.endswith("7F"):  # 127.x.x.x
        if port == "2328":
            seen.add("9000 on loopback")
    else:
        sys.exit(f"listening outside loopback: {line} (the any-address is 00000000)")
if seen != want:
    sys.exit(f"missing: {sorted(want - seen)}")
EOF
check "Keycloak listens only on 8443 (its auth-front address) and on loopback: no cluster port" kc_listens
kc_from_db_auth() { # postgres-auth shares db-auth with Keycloak, but Keycloak does not listen there
    local ip
    ip="$(docker inspect -f '{{(index .NetworkSettings.Networks "'"${COMPOSE_PROJECT_NAME}"'_db-auth").IPAddress}}' "$(dc ps -q keycloak)")"
    docker exec "$(dc ps -q postgres-auth)" timeout 5 bash -c "exec 3<>/dev/tcp/$ip/8443"
}
refuse "postgres-auth (db-auth) cannot open Keycloak's port" kc_from_db_auth
kc_secrets_hidden() {
    local id s
    id="$(dc ps -q keycloak)"
    docker exec "$id" bash -c '
        for v in $(sed -n "s/^[a-z-]*=//p" /run/secrets/keycloak.conf); do
            for e in /proc/[0-9]*/environ; do
                if tr "\0" "\n" <"$e" 2>/dev/null | grep -qF -- "$v"; then echo "a value in $e"; exit 1; fi
            done
        done' || return 1
    for s in keycloak-db-password keycloak-bootstrap-admin-password; do
        if docker inspect "$id" | grep -qF -f "$AETHERSPARK_SECRETS_DIR/$s"; then echo "$s in inspect"; return 1; fi
        if docker logs "$id" 2>&1 | grep -qF -f "$AETHERSPARK_SECRETS_DIR/$s"; then echo "$s in the log"; return 1; fi
    done
}
check "no secret value in Keycloak's process environments, inspect output or log (C5)" kc_secrets_hidden
kc_db_foreign_root() { # Keycloak with a foreign root for its database refuses to start
    dc stop keycloak >/dev/null 2>&1
    local out rc
    out="$(timeout 240 docker compose -f "$repo/deploy/compose/stack/compose.yaml" "${overlay[@]}" run --rm --no-deps \
        -v "$work/pg/foreign.crt:/test/foreign.crt:ro" \
        -e "KC_DB_URL=jdbc:postgresql://$pg:5432/keycloak?sslmode=verify-full&sslrootcert=/test/foreign.crt" \
        keycloak 2>&1)"
    rc=$?
    dc up -d --wait keycloak >/dev/null 2>&1
    [ "$rc" -ne 0 ] && [ "$rc" -ne 124 ] && printf '%s' "$out" | grep -qiE 'PKIX|certificat|SSL' \
        || { echo "exit $rc"; printf '%s\n' "$out" | grep -iE 'error|exception' | tail -3; return 1; }
}
check "Keycloak's database client (JDBC, verify-full) refuses postgres-auth under a foreign root" kc_db_foreign_root
check "... and Keycloak is healthy again with the real root" answers 200 "$auth" /realms/aetherspark/.well-known/openid-configuration
admin_source() { # Traefik with another allowed source: the desktop is refused, a forged header too
    AETHERSPARK_ADMIN_SOURCE=192.0.2.1/32 dc up -d --no-deps --force-recreate traefik >/dev/null 2>&1 || return 1
    local code=""
    for _ in $(seq 1 45); do
        code="$(via "$admin" /admin/master/console/ 2>/dev/null)"
        [ "$code" = 403 ] && break
        sleep 2
    done
    [ "$code" = 403 ] || { echo "status $code"; return 1; }
    code="$(via "$admin" /admin/master/console/ -H 'X-Forwarded-For: 192.0.2.1' -H 'X-Real-Ip: 192.0.2.1')"
    [ "$code" = 403 ] || { echo "forged header: status $code"; return 1; }
}
check "the admin route refuses a source outside its allowlist, also with a forged X-Forwarded-For" admin_source
dc up -d --no-deps --force-recreate --wait traefik >/dev/null 2>&1
check "Traefik is back with the stack's allowlist, with all its names" all_names_ready

echo "--- Keycloak's administrators: password and security key, no password-only sign-in (C10, PR 5b)"
( umask 077; python3 -I -c 'import secrets, sys; sys.stdout.write(secrets.token_urlsafe(24))' >"$work/admin-pw" )
kc_admin() { python3 -I "$repo/deploy/keycloak/admin.py" setup "$@"; }
check "setup hardens the master realm and creates an administrator" \
    kc_admin stack-test-admin --password-file "$work/admin-pw"
refuse "setup refuses a second run (it can no longer sign in with a password)" \
    kc_admin other-admin --password-file "$work/admin-pw"
direct_grant_refused() { # even with the first-start administrator's real password
    local code
    code="$(via "$admin" /realms/master/protocol/openid-connect/token \
        --data-urlencode grant_type=password --data-urlencode client_id=admin-cli \
        --data-urlencode username=bootstrap-admin \
        --data-urlencode "password@$AETHERSPARK_SECRETS_DIR/keycloak-bootstrap-admin-password")"
    # OAuth answers unauthorized_client with 400 (RFC 6749, 5.2)
    [ "$code" = 400 ] && grep -q '"unauthorized_client"' "$work/kc.body" \
        || { echo "status $code: $(head -c 120 "$work/kc.body")"; return 1; }
}
check "a password-only sign-in to the master realm is refused (unauthorized_client)" direct_grant_refused
first_login() { # user, password file, what must follow the password: new-password or key
    local user=$1 pw=$2 want=$3 jar="$work/login.jar" page="$work/login.html" form verifier challenge
    rm -f "$jar"
    lc() { curl -sS -L --max-time 15 --resolve "$admin:443:127.0.0.1" --cacert "$work/root.crt" \
        -b "$jar" -c "$jar" -o "$page" "$@"; }
    form_action() { sed -n 's/.*<form[^>]*action="\([^"]*\)".*/\1/p' "$page" | head -1 | sed 's/&amp;/\&/g'; }
    verifier="$(python3 -I -c 'import secrets; print(secrets.token_urlsafe(48))')"
    challenge="$(python3 -I -c 'import base64, hashlib, sys
print(base64.urlsafe_b64encode(hashlib.sha256(sys.argv[1].encode()).digest()).rstrip(b"=").decode())' "$verifier")"
    lc "https://$admin/realms/master/protocol/openid-connect/auth?client_id=security-admin-console&response_type=code&scope=openid&code_challenge=$challenge&code_challenge_method=S256&redirect_uri=https%3A%2F%2F$admin%2Fadmin%2Fmaster%2Fconsole%2F" || return 1
    form="$(form_action)"
    case $form in
        "https://$admin/realms/master/"*) ;;
        *) echo "the login form posts to ${form%%\?*}, not the admin name"; return 1 ;;
    esac
    lc "$form" --data-urlencode "username=$user" --data-urlencode "password@$pw" --data-urlencode credentialId= || return 1
    if [ "$want" = new-password ]; then
        grep -q 'password-new' "$page" || { echo "no new-password page"; return 1; }
        local fresh
        fresh="$(python3 -I -c 'import secrets; print(secrets.token_urlsafe(24))')"
        lc "$(form_action)" --data-urlencode "password-new=$fresh" --data-urlencode "password-confirm=$fresh" || return 1
    fi
    grep -q 'id="kc-page-title">[[:space:]]*Passkey Registration' "$page" && grep -q registerWebAuthn "$page" \
        || { echo "no security-key registration: $(grep -o 'id="kc-page-title">[^<]*' "$page")"; return 1; }
}
check "the first sign-in posts to the admin name, asks a new password, then a security key" \
    first_login stack-test-admin "$work/admin-pw" new-password
kc_db() { # what the master realm holds, read from Keycloak's database (not through Keycloak)
    pg_is "$1" keycloak keycloak "$pg_tls" "$2"
}
master="(select id from realm where name = 'master')"
check "database: brute-force detection on" kc_db true \
    "select value from realm_attribute where realm_id = $master and name = 'bruteForceProtected'"
check "database: the realm's address is the admin name" kc_db "https://$admin" \
    "select value from realm_attribute where realm_id = $master and name = 'frontendUrl'"
check "database: the master realm signs in with the flow browser-security-key" kc_db browser-security-key \
    "select f.alias from realm r join authentication_flow f on f.id = r.browser_flow where r.name = 'master'"
# Requirement codes: 0 required, 2 alternative
check "database: that flow is the cookie, or the form: password, then the security key, both required" kc_db \
    "auth-cookie:2 subflow:2 auth-username-password-form:0 webauthn-authenticator:0" \
    "select string_agg(coalesce(e.authenticator, 'subflow') || ':' || e.requirement, ' ' order by f.alias, e.priority)
       from authentication_execution e join authentication_flow f on f.id = e.flow_id
      where f.realm_id = $master and f.alias in ('browser-security-key', 'browser-security-key forms')"
check "database: no master client allows a password-only sign-in" kc_db 0 \
    "select count(*) from client where realm_id = $master and direct_access_grants_enabled"
check "database: login and admin events recorded" kc_db t \
    "select events_enabled and admin_events_enabled from realm where name = 'master'"
# Break-glass, as the runbook does it, with a throw-away password handed over by variable name
breakglass() {
    local pw
    pw="$(python3 -I -c 'import secrets; print(secrets.token_urlsafe(24))')"
    ( umask 077; printf '%s' "$pw" >"$work/breakglass-pw" )
    BREAK_GLASS_PW="$pw" AETHERSPARK_BREAK_GLASS_PASSWORD_ENV=BREAK_GLASS_PW \
        bash "$repo/deploy/keycloak/break-glass.sh" breakglass-test "${overlay[@]}" >"$work/breakglass.log" 2>&1 \
        || { tail -5 "$work/breakglass.log"; return 1; }
}
check "break-glass creates a temporary administrator with Keycloak stopped, and Keycloak returns" breakglass
check "... whose first sign-in also demands a security key" first_login breakglass-test "$work/breakglass-pw" key

echo "--- The aetherspark realm as code; its users: password and a second factor (C10, PR 6a)"
check "apply after setup changes nothing and reports the realm as the repository says" \
    python3 -I "$repo/deploy/keycloak/admin.py" apply
# The config service account, as `apply` uses it: what it may and may not reach
cat >"$work/config_probe.py" <<'EOF'
import sys
sys.path.insert(0, sys.argv[1] + "/deploy/keycloak")
import admin as kc
a = kc.admin_route()
if not kc.sign_in_config(a):
    sys.exit("the config account cannot sign in")
master = {p: a.call("GET", f"/admin/realms/master/{p}")[0] for p in ("users", "clients", "roles")}
changed = a.call("PUT", "/admin/realms/master", {"bruteForceProtected": False})[0]
if set(master.values()) != {403} or changed != 403:
    sys.exit(f"master reachable: {master}, update {changed}")
if a.call("GET", "/admin/realms/aetherspark/users")[0] != 200:
    sys.exit("the aetherspark realm's users are out of reach")
# A user created plainly (as in the console) still has to set up an app code
created = a.call("POST", "/admin/realms/aetherspark/users", {"username": "plain-user", "enabled": True})
actions = kc.find_user(a, "aetherspark", "plain-user")[0].get("requiredActions")
if created[0] != 201 or actions != ["CONFIGURE_TOTP"]:
    sys.exit(f"a plain user: HTTP {created[0]}, required actions {actions}")
EOF
check "the config account administers the aetherspark realm only (master: 403 on users, clients, roles, settings)" \
    python3 -I "$work/config_probe.py" "$repo"
mkdir -p "$work/user" "$work/keyuser" && chmod 700 "$work/user" "$work/keyuser"
for u in user keyuser; do
    ( umask 077; python3 -I -c 'import secrets, sys; sys.stdout.write(secrets.token_urlsafe(24))' >"$work/$u/password" )
done
check "user add creates a user with a temporary password and an app code to set up" \
    python3 -I "$repo/deploy/keycloak/admin.py" user add stack-test-user --password-file "$work/user/password"
oidc() { python3 -I "$repo/tests/stack/oidc_flow.py" "$@" "$work/root.crt"; }
check "first sign-in through Traefik: PKCE, app code set up, new password, tokens, refresh, userinfo, logout" \
    oidc "$work/user" stack-test-user
check "a later sign-in asks for the app code (a fresh browser remembers nothing)" oidc "$work/user" stack-test-user
key_user_first() { # the client cannot touch a key: it must stop at the registration page
    local out
    out="$(oidc "$work/keyuser" stack-test-key-user 2>&1)"
    printf '%s' "$out" | grep -q 'unexpected page' && grep -q registerWebAuthn "$work/keyuser/unexpected.html" \
        || { printf '%s\n' "$out" | tail -2; return 1; }
}
check "user add --key: the first sign-in demands a security key instead" \
    python3 -I "$repo/deploy/keycloak/admin.py" user add stack-test-key-user --key --password-file "$work/keyuser/password"
check "... and stops at the security-key registration (no way around it)" key_user_first
aether="(select id from realm where name = 'aetherspark')"
check "database: the aetherspark realm signs in with the flow browser-2fa" kc_db browser-2fa \
    "select f.alias from realm r join authentication_flow f on f.id = r.browser_flow where r.name = 'aetherspark'"
check "database: users may not delete their own second factor; no profile questions" kc_db "f f" \
    "select string_agg(case when enabled then 't' else 'f' end, ' ' order by alias) from required_action_provider
      where realm_id = $aether and alias in ('delete_credential', 'VERIFY_PROFILE')"
check "database: every new user must set up an app code (default action)" kc_db t \
    "select default_action from required_action_provider where realm_id = $aether and alias = 'CONFIGURE_TOTP'"

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
kc_serial() {
    on_pki 'step certificate inspect /tls/keycloak/tls.crt --format json' \
        | python3 -I -c 'import json, sys; print(json.load(sys.stdin)["serial_number"])'
}
kc_first="$(kc_serial)"
errors=0 pg_errors=0 kc_errors=0
for _ in $(seq 1 30); do # 150 s: the renewers renew before 120 s, Traefik every minute
    host_curl "https://$name/" >/dev/null 2>&1 || errors=$((errors + 1))
    pgc keycloak keycloak "$pg_tls" "select 1" >/dev/null 2>&1 || pg_errors=$((pg_errors + 1))
    [ "$(via "$auth" /realms/aetherspark/.well-known/openid-configuration 2>/dev/null)" = 200 ] \
        || kc_errors=$((kc_errors + 1))
    sleep 3
done
check "no failed connection to Traefik while its certificate was renewed" [ "$errors" -eq 0 ]
check "no failed connection to postgres-auth while its certificate was renewed" [ "$pg_errors" -eq 0 ]
check "no failed request to Keycloak through Traefik while its certificate was renewed" [ "$kc_errors" -eq 0 ]
pg_renewed() { [ "$pg_first" != "$(pg_serial)" ]; }
check "postgres-auth's renewer renewed its certificate (new serial)" pg_renewed
check "postgres-auth serves the renewed certificate after its reload" pg_reloaded
kc_renewed() { [ "$kc_first" != "$(kc_serial)" ]; }
check "Keycloak's renewer renewed its certificate (new serial)" kc_renewed
kc_reloaded() { # within two reload periods
    for _ in $(seq 1 16); do kc_tls - >/dev/null 2>&1 && return 0; sleep 5; done
    kc_tls -
}
check "Keycloak serves the renewed certificate after its reload" kc_reloaded
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
overdue() { on_pki 'step certificate needs-renewal /tls/postgres-auth/tls.crt --expires-in 75%'; }
check "after the stop the certificate is past its renewal point" overdue
check "... and not expired" pg_valid
pg_first="$(pg_serial)"
check "just up after the stop reaches healthy" up
check "the renewer renewed before it reported healthy" pg_renewed
check "keycloak's data survived the restart (the probe table is there)" pg_is 1 keycloak keycloak \
    "$pg_tls" "select count(*) from pg_tables where tablename = 'stack_test_probe'"

echo "--- C9b: recovery after expiry"
dc stop >/dev/null 2>&1
sleep 190
refuse "after the stop the old certificate no longer verifies (expired)" pg_valid
# A rotated database password applies at the next start (postgres-auth sets it every start)
python3 -I "$repo/deploy/secret-store/secret_store.py" rotate keycloak-db-password >/dev/null
rebootstrapped() { up && grep -q 'cert-bootstrap: postgres-auth issued' "$work/up.log"; }
check "just up re-bootstraps the expired certificate and reaches healthy" rebootstrapped
check "the new certificate verifies" pg_valid
check "keycloak signs in with its rotated password" pg_is keycloak keycloak keycloak "$pg_tls" "select current_user"
check "... and Keycloak itself, through its regenerated configuration file" answers 200 "$auth" \
    /realms/aetherspark/.well-known/openid-configuration
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
