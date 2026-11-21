#!/usr/bin/env bash
# step-ca, Traefik and postgres-auth end to end (docs/architecture/compose-base.md, tests C3, C3b,
# C5 for postgres-auth, C6, C9a, C9b, C11), and the data stores, the data Postgres and ClickHouse
# (docs/architecture/data-plane.md, D8 and D10 for them). C6's host-rule checks need
# tests/stack/layer3.sh (CI).
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
# ClickHouse reloads its configuration, and so its certificate files, every 20 s too
export AETHERSPARK_CH_RELOAD_SECONDS=20
# forward-auth and the test page restart on a new certificate (cert-watch.sh): checked every 20 s
export AETHERSPARK_CERT_WATCH_SECONDS=20
work="$(mktemp -d)"
cd "$work" || exit 1
export AETHERSPARK_SECRETS_STORE="$work/store" AETHERSPARK_SECRETS_KEY="$work/key/dev.key"
export AETHERSPARK_PACKAGE_FEED="$work/packages"
export AETHERSPARK_SECRETS_DIR="${XDG_RUNTIME_DIR:-/dev/shm}/$COMPOSE_PROJECT_NAME/secrets"
# The ingester's raw folder: a throw-away one, never the desktop's E: or the Spark's /srv/raw
export AETHERSPARK_RAW_DIR="$work/raw"
mkdir -p "$AETHERSPARK_RAW_DIR"
# The pipeline's taxonomy store: a throw-away one with a made-up taxonomy (tests/stack/pipeline_jobs.py)
export AETHERSPARK_TAXONOMY_DIR="$work/taxonomies"
mkdir -p "$AETHERSPARK_TAXONOMY_DIR"
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
up() { # `just up` with the test route; its output goes to up.log. On failure: its last lines, and
    # the last lines of every container that stopped (CI once lost postgres-auth here, 2026-11-09)
    bash "$repo/deploy/compose/stack/up.sh" "${overlay[@]}" >"$work/up.log" 2>&1 && return 0
    tail -8 "$work/up.log"
    local id
    for id in $(docker ps -aq --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME" --filter status=exited); do
        echo "--- $(docker inspect -f '{{.Name}} exit {{.State.ExitCode}}' "$id")"
        docker logs --tail 12 "$id" 2>&1
    done
    return 1
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
enter() { # name, value: through the test's file descriptor, never an argument of the tool
    AETHERSPARK_SECRETS_ENTER_FD=3 python3 -I "$repo/deploy/secret-store/secret_store.py" enter "$1" 3< <(printf '%s\n' "$2") >/dev/null
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

# C6 with the egress host rules in place (AETHERSPARK_LAYER3=1, set by tests/stack/layer3.sh in
# CI): the kernel log from this run on, read with sudo. The log is a ring buffer, so the baseline is
# the last line's timestamp, never a line count (tests/egress/layer3.sh).
layer3="${AETHERSPARK_LAYER3:-}"
klog() { sudo -n dmesg 2>/dev/null; }
klog_mark() { klog | tail -n 1 | awk -F'[][]' '{gsub(/ /, "", $2); print $2}'; }
if [ -n "$layer3" ]; then
    klog_start="$(klog_mark)"
    [ -n "$klog_start" ] || { echo "FAIL  the kernel log is readable (sudo dmesg)"; exit 1; }
fi

echo "--- setup: just up with the test route"
if ! up; then
    fail "just up reaches healthy" "$(dc logs --tail 15 2>&1 | tail -40)"
    exit 1
fi
pass "just up reaches healthy (CA initialised, postgres-auth bootstrapped and started, Traefik ordered)"
ingester_left_out() { # no container, a message, and no folder where the secret file belongs
    grep -F 'the ingester is not started until the SEC contact is entered' "$work/up.log" >/dev/null \
        || { echo "no message in up.log"; return 1; }
    [ -z "$(dc ps -aq ingester)" ] || { echo "an ingester container exists"; return 1; }
    [ ! -e "$AETHERSPARK_SECRETS_DIR/sec-user-agent-contact" ] || { echo "something at the secret's path"; return 1; }
}
check "just up leaves the ingester out until the SEC contact is entered, and says so" ingester_left_out
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
traefik_ok() { [ "$(host_curl "https://$name/stack-test/" 2>/dev/null)" = OK ]; }
# Traefik is healthy before its ACME order completes; give the order up to 90 s (amd64 CI once lost
# this race, 2026-11-07)
traefik_ready() {
    for _ in $(seq 1 45); do traefik_ok && return 0; sleep 2; done
    host_curl "https://$name/stack-test/"   # the last error, for the report
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

echo "--- postgres-data: TLS only, its two runtime roles only, rows not tables (C3, C3b, C5, D8, D10)"
# A client on ingest: psql in postgres-data's own image and service definition, as on_db for
# postgres-auth. The password sources: ingester, pipeline, owner, superuser, or a wrong one
pgd=postgres-data.svc.aether.localhost
cat >"$work/pg/client-data.sh" <<'EOF'
set -euo pipefail
user=$1 source=$2 conninfo=$3 sql=$4
{
    printf '*:*:*:%s:' "$user"
    case $source in
        ingester|pipeline|owner|superuser) cat "/run/secrets/postgres-data-$source-password" ;;
        *) printf 'not-the-password' ;;
    esac
    printf '\n'
} >/tmp/pgpass
chmod 0600 /tmp/pgpass
PGPASSFILE=/tmp/pgpass psql "user=$user connect_timeout=5 $conninfo" -X -q -t -A -v ON_ERROR_STOP=1 -c "$sql"
EOF
on_data() { dc run --rm --no-deps -T --entrypoint bash -v "$work/pg:/test:ro" postgres-data "$@"; }
pgdc() { on_data /test/client-data.sh "$@"; }
pgd_tls="host=$pgd dbname=research sslmode=verify-full sslrootcert=/test/root.crt"
pgd_is() { # expected output, then pgdc's arguments
    local want=$1 got
    shift
    got="$(pgdc "$@")" || return 1
    [ "$got" = "$want" ] || { echo "got: $got"; return 1; }
}
# The migration owner, on the server's own socket inside its container (as migrate.sh signs in)
owner_sql() {
    docker exec -i "$(dc ps -q postgres-data)" bash -c '
        umask 077
        { printf "*:*:*:data_owner:"; cat /run/secrets/postgres-data-owner-password; printf "\n"; } >/tmp/owner.pgpass
        PGPASSFILE=/tmp/owner.pgpass psql -h /var/run/postgresql -U data_owner -d research -X -q -t -A -v ON_ERROR_STOP=1 -c "$1"
        code=$?; rm -f /tmp/owner.pgpass; exit $code' _ "$1"
}
owner_is() { # expected output, SQL
    local got
    got="$(owner_sql "$2")" || return 1
    [ "$got" = "$1" ] || { echo "got: $got"; return 1; }
}
check "ingester signs in over TLS checked with the CA's root and the name (verify-full)" \
    pgd_is ingester ingester ingester "$pgd_tls" "select current_user"
check "pipeline signs in over TLS checked with the CA's root and the name (verify-full)" \
    pgd_is pipeline pipeline pipeline "$pgd_tls" "select current_user"
check "the connection is encrypted, TLS 1.2 or newer" pgd_is t ingester ingester "$pgd_tls" \
    "select ssl and version in ('TLSv1.2', 'TLSv1.3') from pg_stat_ssl where pid = pg_backend_pid()"
refuse "a plain connection is refused (hostssl only)" \
    pgdc ingester ingester "host=$pgd dbname=research sslmode=disable" "select 1"
refuse "a wrong password is refused" pgdc ingester wrong "$pgd_tls" "select 1"
refuse "the superuser cannot sign in over the network, even with its password" \
    pgdc postgres superuser "$pgd_tls" "select 1"
refuse "the migration owner cannot sign in over the network, even with its password" \
    pgdc data_owner owner "$pgd_tls" "select 1"
refuse "the ingester cannot open another database" \
    pgdc ingester ingester "host=$pgd dbname=postgres sslmode=verify-full sslrootcert=/test/root.crt" "select 1"
refuse "libpq (verify-full) refuses postgres-data under a foreign root" \
    pgdc ingester ingester "host=$pgd dbname=research sslmode=verify-full sslrootcert=/test/foreign.crt" "select 1"
pgd_ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' "$(dc ps -q postgres-data)" | awk '{print $1}')"
refuse "libpq (verify-full) refuses postgres-data by address (not its name)" \
    pgdc ingester ingester "host=$pgd_ip dbname=research sslmode=verify-full sslrootcert=/test/root.crt" "select 1"
hba_data_matches() { # every hostssl line of postgres-data's pg_hba.conf admits exactly the ingest subnet
    local net lines
    net="$(docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}' "${COMPOSE_PROJECT_NAME}_ingest")"
    lines="$(awk '$1 == "hostssl" {print $4}' "$repo/deploy/compose/stack/postgres-data/pg_hba.conf" | sort -u)"
    [ -n "$net" ] && [ "$lines" = "$net" ] || { echo "ingest $net, pg_hba.conf: $lines"; return 1; }
}
check "pg_hba.conf admits the ingest network's subnet, nothing wider" hba_data_matches
check "the migrations are applied and recorded with their SHA-256" \
    owner_sql "select count(*) = 1 from meta.migrations where name = '0001_schemas.sql' and length(sha256) = 64"
# D8 with a table of the owner's in each schema: the runtime roles get rows, never tables
check "the migration owner creates a probe table in ref and in pipeline" \
    owner_sql "create table if not exists ref.stack_test_probe (id int); create table if not exists pipeline.stack_test_probe (id int)"
check "the ingester writes and reads rows in ref (default privileges)" \
    pgdc ingester ingester "$pgd_tls" "insert into ref.stack_test_probe values (1); select count(*) from ref.stack_test_probe"
check "the pipeline inserts and reads rows in pipeline" \
    pgdc pipeline pipeline "$pgd_tls" "insert into pipeline.stack_test_probe values (1); select count(*) from pipeline.stack_test_probe"
refuse "the pipeline cannot update rows in pipeline (insert only)" \
    pgdc pipeline pipeline "$pgd_tls" "update pipeline.stack_test_probe set id = 2"
refuse "the ingester cannot drop the owner's table" pgdc ingester ingester "$pgd_tls" "drop table ref.stack_test_probe"
refuse "the pipeline cannot drop the owner's table" pgdc pipeline pipeline "$pgd_tls" "drop table pipeline.stack_test_probe"
refuse "the ingester cannot create a table in its own schema" pgdc ingester ingester "$pgd_tls" "create table ref.stack_test_new (id int)"
refuse "the pipeline cannot create a table in its own schema" pgdc pipeline pipeline "$pgd_tls" "create table pipeline.stack_test_new (id int)"
refuse "the ingester cannot create a table in public" pgdc ingester ingester "$pgd_tls" "create table public.stack_test_new (id int)"
refuse "the ingester cannot read the pipeline's schema" pgdc ingester ingester "$pgd_tls" "select count(*) from pipeline.stack_test_probe"
refuse "the pipeline cannot read the ingester's schema" pgdc pipeline pipeline "$pgd_tls" "select count(*) from ref.stack_test_probe"
check "neither runtime role holds another role (no pg_* role)" pgd_is 0 ingester ingester "$pgd_tls" \
    "select count(*) from pg_auth_members m join pg_roles u on u.oid = m.member where u.rolname in ('ingester', 'pipeline', 'data_owner')"
check "the owner's probe tables are removed again" \
    owner_sql "drop table ref.stack_test_probe; drop table pipeline.stack_test_probe"
pgd_secrets_hidden() { # in no process environment of the container, not in inspect, not in its log
    local id s
    id="$(dc ps -q postgres-data)"
    docker exec "$id" bash -c '
        for s in /run/secrets/*; do
            for e in /proc/[0-9]*/environ; do
                if tr "\0" "\n" <"$e" 2>/dev/null | grep -qF -f "$s"; then echo "$s in $e"; exit 1; fi
            done
        done' || return 1
    for s in postgres-data-superuser-password postgres-data-owner-password postgres-data-ingester-password postgres-data-pipeline-password; do
        if docker inspect "$id" | grep -qF -f "$AETHERSPARK_SECRETS_DIR/$s"; then echo "$s in inspect"; return 1; fi
        if docker logs "$id" 2>&1 | grep -qF -f "$AETHERSPARK_SECRETS_DIR/$s"; then echo "$s in the log"; return 1; fi
    done
}
check "no secret value in postgres-data's process environments, inspect output or log (C5)" pgd_secrets_hidden
pgd_reloaded() { # within two reload periods
    for _ in $(seq 1 9); do tls_py ingest postgres-data-tls "$pgd" 5432 yes - >/dev/null 2>&1 && return 0; sleep 5; done
    tls_py ingest postgres-data-tls "$pgd" 5432 yes -
}
check "postgres-data serves the certificate in its volume" pgd_reloaded

echo "--- clickhouse: TLS ports only, no default user, the pipeline's rows only, no table functions (C3, C3b, C5, D8, D10)"
# A client on ingest: clickhouse-client in ClickHouse's own image and service definition, as on_data
# for postgres-data. It checks the chain against the CA's root, but not the name (clickhouse-client
# does not), so the name and address checks use tls.py. The password sources: pipeline, owner, or a
# wrong one; the password goes through a client file in the container's in-memory /tmp.
ch=clickhouse.svc.aether.localhost
cat >"$work/pg/client-ch.sh" <<'EOF'
set -euo pipefail
user=$1 source=$2 sql=$3
{
    printf '<clickhouse><user>%s</user><password>' "$user"
    case $source in
        pipeline|owner) cat "/run/secrets/clickhouse-$source-password" ;;
        *) printf 'not-the-password' ;;
    esac
    printf '</password><openSSL><client><loadDefaultCAFile>false</loadDefaultCAFile>'
    printf '<caConfig>/test/root.crt</caConfig><verificationMode>strict</verificationMode></client></openSSL></clickhouse>'
} >/tmp/client.xml
clickhouse-client --config-file /tmp/client.xml --host clickhouse.svc.aether.localhost --port 9440 --secure -q "$sql"
EOF
chc() { dc run --rm --no-deps -T --entrypoint bash -v "$work/pg:/test:ro" clickhouse /test/client-ch.sh "$@"; }
chc_is() { # expected output, then chc's arguments
    local want=$1 got
    shift
    got="$(chc "$@")" || return 1
    [ "$got" = "$want" ] || { echo "got: $got"; return 1; }
}
# The migration owner, from the server's own loopback (as run.sh and migrate.sh sign in)
owner_ch() { docker exec "$(dc ps -q clickhouse)" clickhouse-client --config-file /tmp/clickhouse/owner-client.xml -q "$1"; }
check "pipeline signs in over TLS (native protocol), checked with the CA's root" \
    chc_is pipeline pipeline pipeline "SELECT currentUser()"
check "the native TLS port serves the certificate in its volume, chain and name checked" \
    tls_py ingest clickhouse-tls "$ch" 9440 no /test/root.crt
check "the HTTPS port serves the certificate in its volume, chain and name checked" \
    tls_py ingest clickhouse-tls "$ch" 8443 no /test/root.crt
refuse "a client refuses clickhouse under a foreign root" tls_py ingest clickhouse-tls "$ch" 9440 no /test/foreign.crt
ch_ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' "$(dc ps -q clickhouse)" | awk '{print $1}')"
refuse "a client refuses clickhouse by address (not its name)" tls_py ingest clickhouse-tls "$ch_ip" 9440 no /test/root.crt
ch_listens() { # 8443 and 9440 only (Docker's resolver on 127.0.0.11 is not the server's)
    local got
    got="$(docker exec "$(dc ps -q clickhouse)" sh -c 'cat /proc/net/tcp /proc/net/tcp6 2>/dev/null' |
        awk '$4 == "0A" && $2 !~ /^0B00007F:/ {split($2, a, ":"); print a[2]}' | sort -u | tr '\n' ' ')"
    [ "$got" = "20FB 24E0 " ] || { echo "listening (hex ports): $got"; return 1; }
}
check "nothing listens but 8443 (HTTPS) and 9440 (native TLS)" ch_listens
ch_plain_closed() { # the upstream image's plaintext, MySQL, PostgreSQL, interserver and gRPC ports
    dc run --rm --no-deps -T --entrypoint bash clickhouse -c '
        for p in 8123 9000 9004 9005 9009 9100 9363; do
            if (echo >"/dev/tcp/clickhouse.svc.aether.localhost/$p") 2>/dev/null; then echo "$p open"; exit 1; fi
        done'
}
check "the plaintext, MySQL, PostgreSQL, interserver and gRPC ports are closed" ch_plain_closed
refuse "a wrong password is refused" chc pipeline wrong "SELECT 1"
refuse "there is no default user" chc default wrong "SELECT 1"
refuse "the migration owner cannot sign in over the network, even with its password" chc data_owner owner "SELECT 1"
users_match() { # users.xml admits the pipeline from exactly the ingest network's subnet
    local net ips
    net="$(docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}' "${COMPOSE_PROJECT_NAME}_ingest")"
    ips="$(sed -n '/<pipeline>/,/<\/pipeline>/s/.*<ip>\(.*\)<\/ip>.*/\1/p' "$repo/deploy/compose/stack/clickhouse/users.xml")"
    [ -n "$net" ] && [ "$ips" = "$net" ] || { echo "ingest $net, users.xml: $ips"; return 1; }
}
check "users.xml admits the pipeline from the ingest network's subnet, nothing wider" users_match
check "the migrations are applied and recorded with their SHA-256" \
    owner_ch "SELECT throwIf(count() != 1) FROM meta.migrations WHERE name = '0001_facts.sql' AND length(sha256) = 64"
# D8: the pipeline gets rows of facts, never tables, other databases or the outside world
check "the pipeline inserts and reads a fact" chc_is 1 pipeline pipeline \
    "INSERT INTO research.facts (cik, value, period_start, period_end, known_at, filed_date, fiscal_year, fact_hash) VALUES (4294967295, 1.5, '2024-01-01', '2024-12-31', now64(3), today(), 2024, repeat('0', 64)); SELECT count() FROM research.facts WHERE cik = 4294967295"
refuse "the pipeline cannot drop the facts table" chc pipeline pipeline "DROP TABLE research.facts"
refuse "the pipeline cannot create a table" chc pipeline pipeline "CREATE TABLE research.stack_test_new (a Int8) ENGINE = Memory"
refuse "the pipeline cannot delete facts (append-only)" chc pipeline pipeline "ALTER TABLE research.facts DELETE WHERE cik = 4294967295"
refuse "the pipeline cannot truncate the facts table" chc pipeline pipeline "TRUNCATE TABLE research.facts"
refuse "the pipeline cannot read the migrations' record" chc pipeline pipeline "SELECT count() FROM meta.migrations"
refuse "the pipeline cannot list the users" chc pipeline pipeline "SELECT name FROM system.users"
refuse "url() is refused" chc pipeline pipeline "SELECT * FROM url('https://www.sec.gov/', CSV, 'a String')"
refuse "file() is refused" chc pipeline pipeline "SELECT * FROM file('a.csv', CSV, 'a String')"
refuse "remote() is refused" chc pipeline pipeline "SELECT * FROM remote('postgres-data.svc.aether.localhost', system.one)"
refuse "s3() is refused" chc pipeline pipeline "SELECT * FROM s3('https://www.sec.gov/bucket/key.csv', CSV, 'a String')"
refuse "executable() is refused" chc pipeline pipeline "SELECT * FROM executable('x.sh', TSV, 'a String')"
refuse "postgresql() is refused" chc pipeline pipeline "SELECT * FROM postgresql('postgres-data.svc.aether.localhost:5432', 'research', 't', 'u', 'p')"
ch_secrets_hidden() { # in no process environment of the container, not in inspect, not in its log
    local id s
    id="$(dc ps -q clickhouse)"
    docker exec "$id" bash -c '
        for s in /run/secrets/*; do
            for e in /proc/[0-9]*/environ; do
                if tr "\0" "\n" <"$e" 2>/dev/null | grep -qF -f "$s"; then echo "$s in $e"; exit 1; fi
            done
        done' || return 1
    for s in clickhouse-owner-password clickhouse-pipeline-password; do
        if docker inspect "$id" | grep -qF -f "$AETHERSPARK_SECRETS_DIR/$s"; then echo "$s in inspect"; return 1; fi
        if docker logs "$id" 2>&1 | grep -qF -f "$AETHERSPARK_SECRETS_DIR/$s"; then echo "$s in the log"; return 1; fi
    done
}
check "no secret value in clickhouse's process environments, inspect output or log (C5)" ch_secrets_hidden
ch_reloaded() { # within two reload periods
    for _ in $(seq 1 9); do tls_py ingest clickhouse-tls "$ch" 9440 no - >/dev/null 2>&1 && return 0; sleep 5; done
    tls_py ingest clickhouse-tls "$ch" 9440 no -
}
check "clickhouse serves the certificate in its volume after its reloads" ch_reloaded

echo "--- the runtime egress proxy: part of the stack, the SEC hosts only (egress.md; data-plane.md section 7)"
# A client on egress-runtime, as the ingester will be: through the proxy to an allowed host, and
# refused elsewhere. The egress tests (tests/egress/run.sh, E1-E10) cover the proxy's rules in depth;
# here only that the stack's own proxy is there and works. One request to the SEC per run.
rt_proxy=http://172.30.10.2:3128
rt_curl_image="$(sed -n 's/^ *image: *\(curlimages\/curl:[^ ]*\) *$/\1/p' "$repo/tests/egress/compose.test.yaml" | head -1)"
on_runtime() { # curl's arguments, from a throw-away client on egress-runtime
    docker run --rm --network "${COMPOSE_PROJECT_NAME}_egress-runtime" --read-only --cap-drop ALL \
        --user "$AETHERSPARK_UID:$AETHERSPARK_GID" "$rt_curl_image" "$@"
}
check "the runtime proxy runs in the stack's own project" test -n "$(dc ps -q egress-runtime)"
# The tunnel's status (200) is the proxy's answer; the SEC's own status is not checked, since the SEC
# refuses (403) a request without a declared contact in its User-Agent, and that contact is the
# ingester's entered secret (PR 3b). Any SEC answer over the tunnel means the client checked the TLS.
sec_through_proxy() {
    local codes
    codes="$(on_runtime -s -o /dev/null -m 15 -w '%{http_connect} %{http_code}' --proxy "$rt_proxy" https://www.sec.gov/)"
    case "$codes" in
        "200 000" | "200 ") echo "tunnel open, no answer over TLS: $codes"; return 1 ;;
        "200 "*) return 0 ;;
        *) echo "tunnel and answer: $codes"; return 1 ;;
    esac
}
check "a client on egress-runtime reaches www.sec.gov through the proxy (TLS checked by the client)" sec_through_proxy
refuse "a host not on the runtime list is refused" on_runtime -s -o /dev/null -m 15 --proxy "$rt_proxy" https://www.example.com/
refuse "without the proxy, egress-runtime has no way out" on_runtime -s -o /dev/null -m 10 https://www.sec.gov/

echo "--- the ingester: no listener, its two networks, rows only, its schema checked at start (data-plane.md; C5, D8, D10)"
# just up left it out (setup); a dummy contact as in CI (decision 8), then it starts
ingester_up() {
    enter sec-user-agent-contact stack-test@example.org \
        && python3 -I "$repo/deploy/secret-store/secret_store.py" unseal >/dev/null \
        && dc up -d --wait ingester
}
check "with the SEC contact entered, the ingester starts and is healthy (its database heartbeat)" ingester_up
ing() { docker exec "$(dc ps -q ingester)" "$@"; }
ingester_walked() { # the walk at start went through Wolverine's durable queue to its handler
    # 0 companies: the overlay mounts the test's empty universe (tests/stack/universe), never the
    # real dev universe, which this ingester's dummy contact must not walk
    for _ in $(seq 1 30); do
        dc logs ingester 2>&1 | grep -F 'universe walk (start): 0 companies' >/dev/null && return 0
        sleep 2
    done
    dc logs --tail 20 ingester 2>&1
    return 1
}
check "the walk at start runs (Wolverine's durable local queue, the test's empty universe, never the real one)" ingester_walked
ingester_networks() {
    local got
    got="$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$(dc ps -q ingester)")"
    [ "$got" = "${COMPOSE_PROJECT_NAME}_egress-runtime ${COMPOSE_PROJECT_NAME}_ingest " ] || { echo "networks: $got"; return 1; }
}
check "the ingester is on ingest and egress-runtime only" ingester_networks
ingester_no_listener() { # no listening TCP socket (state 0A) and no listening Unix socket (flag 00010000);
    # 127.0.0.11 (0B00007F) is Docker's own resolver, which listens in every container's namespace
    local found
    found="$(ing cat /proc/net/tcp /proc/net/tcp6 | awk '$4 == "0A" && $2 !~ /^0B00007F:/')$(ing cat /proc/net/unix | awk 'NR > 1 && $4 == "00010000"')"
    [ -z "$found" ] || { echo "$found"; return 1; }
}
check "the ingester listens on no socket, TCP or Unix (no diagnostics socket either)" ingester_no_listener
check "the ingester writes its raw folder" ing sh -c 'touch /raw/.stack-test && rm /raw/.stack-test'
refuse "the ingester cannot write its universe file (read-only)" ing touch /universe/universe.csv
refuse "the ingester cannot write its program files (read-only root)" ing touch /app/stack-test
ingester_secrets_hidden() { # as postgres-data's C5: no process environment, inspect output or log holds one
    local id s
    id="$(dc ps -q ingester)"
    docker exec "$id" sh -c '
        for s in /run/secrets/*; do
            for e in /proc/[0-9]*/environ; do
                if tr "\0" "\n" <"$e" 2>/dev/null | grep -qF -f "$s"; then echo "$s in $e"; exit 1; fi
            done
        done' || return 1
    for s in postgres-data-ingester-password sec-user-agent-contact; do
        if docker inspect "$id" | grep -qF -f "$AETHERSPARK_SECRETS_DIR/$s"; then echo "$s in inspect"; return 1; fi
        if docker logs "$id" 2>&1 | grep -qF -f "$AETHERSPARK_SECRETS_DIR/$s"; then echo "$s in the log"; return 1; fi
    done
}
check "no secret value in the ingester's process environments, inspect output or log (C5)" ingester_secrets_hidden
check "the ingester's migrations are applied and recorded (EF Core's, Wolverine's, the rights)" owner_is t \
    "select (select count(*) from meta.migrations where name in ('0002_ingester_tables.sql', '0003_ingester_wolverine.sql', '0004_ingester_rights.sql')) = 3 and (select count(*) from meta.ef_migrations) = 1"
check "the ingester writes and removes a company in ref (rows)" pgdc ingester ingester "$pgd_tls" \
    "insert into ref.issuers values (9999999999, 'stack test', now(), now()); delete from ref.issuers where cik = 9999999999"
refuse "the database refuses a malformed accession number (its check constraint)" pgdc ingester ingester "$pgd_tls" \
    "insert into ref.issuers values (9999999999, 'stack test', now(), now()); insert into ref.filings values ('../etc', 9999999999, '10-K', now(), current_date, null, now())"
refuse "the ingester cannot create a table in ingest_ops" pgdc ingester ingester "$pgd_tls" "create table ingest_ops.stack_test_new (id int)"
check "the ingester reads meta.ef_migrations (its start check)" pgdc ingester ingester "$pgd_tls" "select count(*) from meta.ef_migrations"
refuse "the ingester cannot read meta.migrations (the owner's)" pgdc ingester ingester "$pgd_tls" "select count(*) from meta.migrations"
check "the pipeline reads the jobs" pgdc pipeline pipeline "$pgd_tls" "select count(*) from ingest_ops.job_runs"
check "the pipeline may update a job's status columns" pgdc pipeline pipeline "$pgd_tls" \
    "update ingest_ops.job_runs set status = 'done', reason = null, attempts = 1, started_at = now(), finished_at = now() where false"
refuse "the pipeline cannot change a job's contract" pgdc pipeline pipeline "$pgd_tls" \
    "update ingest_ops.job_runs set contract = '{}' where false"
refuse "the pipeline cannot add a job" pgdc pipeline pipeline "$pgd_tls" \
    "insert into ingest_ops.job_runs (accession_no, contract, created_at) values ('0000000000-00-000000', '{}', now())"
refuse "the pipeline cannot read Wolverine's tables" pgdc pipeline pipeline "$pgd_tls" \
    "select count(*) from ingest_ops.wolverine_incoming_envelopes"
# The start check: with a migration missing, or Wolverine's tables not as its version expects, the
# ingester stops before any work and names the gap; once the database is right again, it starts
ingester_exits() { # text, since: the ingester, started, stops with an error and the text in its log
    local id state=
    dc up -d ingester >/dev/null 2>&1
    id="$(dc ps -aq ingester)"
    for _ in $(seq 1 30); do
        state="$(docker inspect -f '{{.State.Status}} {{.State.ExitCode}}' "$id")"
        case $state in exited*) break ;; esac
        sleep 2
    done
    case $state in "exited 0" | "" | running* | created* | restarting*) echo "state: $state"; return 1 ;; esac
    docker logs --since "$2" "$id" 2>&1 | grep -F "$1" >/dev/null || { docker logs --tail 15 "$id" 2>&1; return 1; }
}
ingester_guard_ef() {
    local since
    dc stop ingester >/dev/null 2>&1
    owner_sql "create table meta.stack_test_ef as select * from meta.ef_migrations; delete from meta.ef_migrations" >/dev/null \
        || return 1
    since="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    ingester_exits "lacks the ingester's migrations" "$since"
}
ingester_ef_back() {
    owner_sql "insert into meta.ef_migrations select * from meta.stack_test_ef; drop table meta.stack_test_ef" >/dev/null \
        && dc up -d --wait ingester
}
check "the ingester refuses to start while an EF migration is missing" ingester_guard_ef
check "... and starts once it is back" ingester_ef_back
ingester_guard_wolverine() {
    local since
    dc stop ingester >/dev/null 2>&1
    owner_sql "drop index ingest_ops.idx_wolverine_incoming_envelopes_keep_until" >/dev/null || return 1
    since="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    ingester_exits "idx_wolverine_incoming_envelopes_keep_until" "$since"
}
ingester_index_back() { # as 0003_ingester_wolverine.sql makes it
    owner_sql "CREATE INDEX idx_wolverine_incoming_envelopes_keep_until ON ingest_ops.wolverine_incoming_envelopes USING btree (keep_until) WHERE (status = 'Handled')" >/dev/null \
        && dc up -d --wait ingester
}
check "the ingester refuses to start while Wolverine's tables differ from its version's (an index dropped)" ingester_guard_wolverine
check "... and starts once the index is back" ingester_index_back

echo "--- the ingester against a stub SEC: stored, hashed, handed off; nothing beyond its URLs (D3, D4, D5)"
# The stub SEC (stub_sec.py, the standard library in the pinned Python image) serves one made-up
# company on ingest. A run of the ingester's own image and service, with its SEC hosts pointed at the
# stub and no proxy, walks a one-company universe in the raw folder. The stack's ingester stops
# meanwhile: both would share Wolverine's store. No request reaches the real SEC.
sec_stub_name="${COMPOSE_PROJECT_NAME}-sec-stub"
ingester_stub_name="${COMPOSE_PROJECT_NAME}-ingester-stub"
mkdir -p "$work/stub"
printf 'cik,name,why\n1234567,Stub Co,the stack test'"'"'s stand-in company\n' >"$work/raw/stub-universe.csv"
sec_stub_check() { python3 -I "$repo/tests/stack/sec_stub_check.py" "$@"; }
sec_stub_lines() { if [ -f "$work/stub/requests.jsonl" ]; then wc -l <"$work/stub/requests.jsonl"; else echo 0; fi; }
sec_stub_up() {
    dc stop ingester >/dev/null 2>&1
    docker run -d --name "$sec_stub_name" --network "${COMPOSE_PROJECT_NAME}_ingest" --network-alias sec-stub \
        --read-only --cap-drop ALL --user "$AETHERSPARK_UID:$AETHERSPARK_GID" \
        -v "$repo/tests/stack/stub_sec.py:/stub/stub_sec.py:ro" -v "$work/stub:/log" \
        "$py_image" python3 -I /stub/stub_sec.py >/dev/null || return 1
    for _ in $(seq 1 30); do
        docker logs "$sec_stub_name" 2>&1 | grep -F 'sec-stub listening' >/dev/null && return 0
        sleep 1
    done
    docker logs "$sec_stub_name" 2>&1 | tail -5
    return 1
}
ingester_stub_run() { # summary line expected: the run walks, then stops
    local line=$1
    dc run -d --name "$ingester_stub_name" \
        -e Ingester__SecArchives=http://sec-stub:8080/ -e Ingester__SecData=http://sec-stub:8080/ \
        -e Ingester__Proxy= -e Ingester__UniverseFile=/raw/stub-universe.csv ingester >/dev/null || return 1
    for _ in $(seq 1 90); do
        if docker logs "$ingester_stub_name" 2>&1 | grep -F "$line" >/dev/null; then
            # A graceful stop, so Wolverine marks the company's message handled before the end
            docker stop -t 30 "$ingester_stub_name" >/dev/null
            docker logs "$ingester_stub_name" >"$work/stub/ingester.log" 2>&1
            docker rm -f "$ingester_stub_name" >/dev/null
            return 0
        fi
        sleep 2
    done
    docker logs --tail 30 "$ingester_stub_name" 2>&1
    docker rm -f "$ingester_stub_name" >/dev/null
    return 1
}
check "the stub SEC serves one company on ingest" sec_stub_up
stub_first="$(sec_stub_lines)"
check "the ingester walks the stub's company: 2 filings handed off, 3 refused" \
    ingester_stub_run "company 1234567: 2 filings handed off, 0 already stored, 3 refused"
check "it asked only for the URLs it builds, never a link, schema location, redirect or path found in a filing; one User-Agent, the contact; under 5 a second; the 429 and the 503 retried once each" \
    sec_stub_check requests "$work/stub/requests.jsonl" "$stub_first" "AetherSpark stack-test@example.org"
stub_refused() { # accession, reason
    grep -F "filing $1 refused" "$work/stub/ingester.log" | grep -F "$2" >/dev/null \
        || { grep -F 'refused' "$work/stub/ingester.log"; return 1; }
}
check "a traversing file name refuses its filing" stub_refused 0001234567-25-000003 "plain file name"
check "a header whose acceptance time differs from the index refuses its filing (D3)" \
    stub_refused 0001234567-25-000004 "acceptance times differ: header 2025-11-03T21:30:00Z, index 2025-11-03T21:00:00Z"
check "a redirect is not followed and refuses its filing" stub_refused 0001234567-25-000005 "HTTP 302"
refuse "the contact is not in the run's log" grep -F stack-test@example.org "$work/stub/ingester.log"
stub_stored() {
    pgdc pipeline pipeline "$pgd_tls" \
        "select contract::text from ingest_ops.job_runs where accession_no like '0001234567-%' order by accession_no" \
        >"$work/stub/contracts.jsonl" \
        && pgdc ingester ingester "$pgd_tls" \
            "select accession_no || ' ' || name || ' ' || kind || ' ' || sha256 || ' ' || size from ref.filing_files where accession_no like '0001234567-%'" \
            >"$work/stub/files.txt" \
        && sec_stub_check stored "$work/raw" "$work/stub/contracts.jsonl" "$work/stub/files.txt"
}
check "each job's contract names its stored files with their SHA-256, as ref.filing_files does (the pipeline reads it)" stub_stored
check "known_at keeps the true acceptance time of a filing accepted after 17:30 ET (D3)" pgd_is \
    "2025-07-31T22:03:34Z 2025-08-01" ingester ingester "$pgd_tls" \
    "select to_char(known_at at time zone 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') || ' ' || filed_date from ref.filings where accession_no = '0001234567-25-000002'"
check "the company and its ticker are recorded" pgd_is "Stub Co STUB NYSE" ingester ingester "$pgd_tls" \
    "select i.name || ' ' || t.symbol || ' ' || t.exchange from ref.issuers i join ref.tickers t using (cik) where cik = 1234567 and t.valid_to is null"
stub_second="$(sec_stub_lines)"
check "a second walk hands off nothing new" \
    ingester_stub_run "company 1234567: 0 filings handed off, 2 already stored, 3 refused"
check "... and asks for no stored filing" sec_stub_check again "$work/stub/requests.jsonl" "$stub_second"
check "... and the jobs stay two" pgd_is 2 pipeline pipeline "$pgd_tls" \
    "select count(*) from ingest_ops.job_runs where accession_no like '0001234567-%'"
check "no message is left in Wolverine's store for the stack's ingester" owner_is 0 \
    "select count(*) from ingest_ops.wolverine_incoming_envelopes where status <> 'Handled'"
stub_down() {
    docker rm -f "$sec_stub_name" >/dev/null 2>&1
    owner_sql "delete from ingest_ops.job_runs where accession_no like '0001234567-%';
        delete from ref.filing_files where accession_no like '0001234567-%';
        delete from ref.filings where cik = 1234567; delete from ref.tickers where cik = 1234567;
        delete from ref.issuers where cik = 1234567" >/dev/null \
        && rm -rf "$work/raw/1234567" "$work/raw/stub-universe.csv" \
        && dc up -d --wait ingester
}
check "the stub's rows and files are removed and the stack's ingester runs again" stub_down

echo "--- the pipeline worker: the jobs' files checked, pre-parsed and read by Arelle offline in a limited child (PR 4a, 4b1; D5, D6)"
# Not part of `just up` until PR 4b2 (profile `pipeline`): started here on a made-up company's jobs
# (tests/stack/pipeline_jobs.py), one case per filing, with a made-up taxonomy in the store, then
# stopped and its rows and files removed
pipeline_jobs() { python3 -I "$repo/tests/stack/pipeline_jobs.py" "$@"; }
pip_() { docker exec "$(dc --profile pipeline ps -q pipeline)" "$@"; }
pipeline_up() {
    pipeline_jobs prepare "$work/raw" "$work/taxonomies" >"$work/pipeline-jobs.sql" \
        && owner_sql "$(cat "$work/pipeline-jobs.sql")" >/dev/null \
        && dc --profile pipeline build -q pipeline >/dev/null \
        && dc --profile pipeline up -d --wait pipeline
}
check "the pipeline worker starts on its profile and is healthy (its database heartbeat)" pipeline_up
pipeline_settled() { # every test job has ended (done or failed); Arelle takes seconds per job
    local open
    for _ in $(seq 1 90); do
        open="$(owner_sql "select count(*) from ingest_ops.job_runs where accession_no like '0007654321-%' and status in ('queued', 'running')")" || return 1
        [ "$open" = 0 ] && return 0
        sleep 2
    done
    echo "jobs still open: $open"
    dc --profile pipeline logs --tail 20 pipeline 2>&1
    return 1
}
check "the worker ends every job, one at a time" pipeline_settled
pipeline_results() {
    owner_sql "select accession_no || '|' || status || '|' || attempts || '|' || coalesce(reason, '') from ingest_ops.job_runs where accession_no like '0007654321-%' order by accession_no" \
        >"$work/pipeline-results.txt" && pipeline_jobs check "$work/pipeline-results.txt"
}
check "each job ends as its case says: done with 2 facts; a changed file, a traversing name, a link to the worker's own secret, an entity bomb, a remote DTD, a missing file fail with their reasons; a stuck job is queued again once, then fails; a taxonomy file the store lacks, an import out of the job folder, an import from another host fail (D5, D6)" pipeline_results
pipeline_alive() {
    [ "$(docker inspect -f '{{.State.Health.Status}} {{.RestartCount}}' "$(dc --profile pipeline ps -q pipeline)")" = "healthy 0" ]
}
check "... and the worker is still running and healthy after the hostile files" pipeline_alive
check "no job folder is left in its memory" sh -c '[ -z "$(docker exec "$1" ls -A /jobs)" ]' _ "$(dc --profile pipeline ps -q pipeline)"
pipeline_networks() {
    local got
    got="$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$(dc --profile pipeline ps -q pipeline)")"
    [ "$got" = "${COMPOSE_PROJECT_NAME}_ingest " ] || { echo "networks: $got"; return 1; }
}
check "the worker is on ingest only (no proxy network, no route out)" pipeline_networks
pipeline_no_listener() { # as the ingester's
    local found
    found="$(pip_ cat /proc/net/tcp /proc/net/tcp6 | awk '$4 == "0A" && $2 !~ /^0B00007F:/')$(pip_ cat /proc/net/unix | awk 'NR > 1 && $4 == "00010000"')"
    [ -z "$found" ] || { echo "$found"; return 1; }
}
check "the worker listens on no socket, TCP or Unix" pipeline_no_listener
refuse "the worker cannot write the raw folder (read-only)" pip_ touch /raw/.stack-test
refuse "the worker cannot write the taxonomy store (read-only)" pip_ touch /taxonomies/.stack-test
refuse "the worker cannot write its program files (read-only root)" pip_ touch /usr/local/lib/python3.12/site-packages/stack-test
pipeline_secrets_hidden() { # as the ingester's C5
    local id
    id="$(dc --profile pipeline ps -q pipeline)"
    docker exec "$id" sh -c '
        for e in /proc/[0-9]*/environ; do
            if tr "\0" "\n" <"$e" 2>/dev/null | grep -qF -f /run/secrets/postgres-data-pipeline-password; then echo "in $e"; exit 1; fi
        done' || return 1
    if docker inspect "$id" | grep -qF -f "$AETHERSPARK_SECRETS_DIR/postgres-data-pipeline-password"; then echo "in inspect"; return 1; fi
    if docker logs "$id" 2>&1 | grep -qF -f "$AETHERSPARK_SECRETS_DIR/postgres-data-pipeline-password"; then echo "in the log"; return 1; fi
}
check "no secret value in the worker's process environments, inspect output or log (C5)" pipeline_secrets_hidden
pipeline_down() {
    dc --profile pipeline rm -sf pipeline >/dev/null 2>&1
    owner_sql "delete from ingest_ops.job_runs where accession_no like '0007654321-%';
        delete from ref.filings where cik = 7654321; delete from ref.issuers where cik = 7654321" >/dev/null \
        && rm -rf "$work/raw/7654321" "$work/taxonomies/https" "$work/pipeline-jobs.sql" "$work/pipeline-results.txt"
}
check "the worker is removed with its test jobs and files" pipeline_down

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
# Before setup and apply, so only Keycloak's import has seen the realm file: it filled in the place
check "the import created forward-auth with the place filled in (its callback is accepted)" answers 200 "$auth" \
    "/realms/aetherspark/protocol/openid-connect/auth?client_id=forward-auth&response_type=code&scope=openid&code_challenge=$(printf 'x%.0s' $(seq 1 43))&code_challenge_method=S256&redirect_uri=https://whoami.aether.localhost/oauth2/callback"
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
    # -T and no input: run from a terminal, compose would read it, and under `timeout` (not in the
    # terminal's foreground) the system stops it until the timeout (the desktop run of 2026-11-17)
    out="$(timeout 240 docker compose -f "$repo/deploy/compose/stack/compose.yaml" "${overlay[@]}" run --rm --no-deps -T \
        -v "$work/pg/foreign.crt:/test/foreign.crt:ro" \
        -e "KC_DB_URL=jdbc:postgresql://$pg:5432/keycloak?sslmode=verify-full&sslrootcert=/test/foreign.crt" \
        keycloak 2>&1 </dev/null)"
    rc=$?
    dc up -d --wait --force-recreate keycloak >/dev/null 2>&1
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
# Email and names are optional (AetherSpark sends no email)
profile = a.need("GET", "/admin/realms/aetherspark/users/profile")
required = [x["name"] for x in profile["attributes"] if x["name"] in ("email", "firstName", "lastName") and "required" in x]
if required:
    sys.exit(f"still required in the user profile: {required}")
EOF
check "the config account administers the aetherspark realm only (master: 403 on users, clients, roles, settings); email and names optional" \
    python3 -I "$work/config_probe.py" "$repo"
check "database: both realms accept only external security keys (cross-platform), never Windows Hello" kc_db \
    "cross-platform cross-platform" \
    "select string_agg(a.value, ' ' order by r.name) from realm_attribute a join realm r on r.id = a.realm_id
      where a.name = 'webAuthnPolicyAuthenticatorAttachment' and r.name in ('aetherspark', 'master')"
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

echo "--- Gate G3: the test page behind forward-auth (C8), the back channel (C10), PR 6b"
page_flow() {
    python3 -I "$repo/tests/stack/oidc_flow.py" "$work/user" stack-test-user "$work/root.crt" --page "https://$name/"
}
check "the page: sign-in through forward-auth (PKCE, app code), the user shown, a forged header ignored, sign-out" page_flow
fa_secrets_hidden() {
    local id s
    id="$(dc ps -q forward-auth)"
    docker exec "$id" sh -c '
        for s in /run/secrets/*; do
            for e in /proc/[0-9]*/environ; do
                if tr "\0" "\n" <"$e" 2>/dev/null | grep -qF -f "$s"; then echo "$s in $e"; exit 1; fi
            done
        done' || return 1
    for s in forward-auth-client-secret forward-auth-cookie-secret; do
        if docker inspect "$id" | grep -qF -f "$AETHERSPARK_SECRETS_DIR/$s"; then echo "$s in inspect"; return 1; fi
        if docker logs "$id" 2>&1 | grep -qF -f "$AETHERSPARK_SECRETS_DIR/$s"; then echo "$s in the log"; return 1; fi
    done
    if docker logs "$id" 2>&1 | grep -q 'code='; then echo "an authorization code in the log"; return 1; fi
}
check "no secret in forward-auth's process environments, inspect output or log; no authorization code in its log (C5)" fa_secrets_hidden
# The back channel, as a service on auth-edge sees it; a test client with a service account first
cat >"$work/service_client.py" <<'EOF'
import secrets, sys
sys.path.insert(0, sys.argv[1] + "/deploy/keycloak")
import admin as kc
a = kc.admin_route()
kc.sign_in_config(a)
secret = secrets.token_urlsafe(32)
# Its tokens name it as their audience: Keycloak 26 lets a client introspect only tokens issued for
# it ("not in the token audience" otherwise, measured 2026-11-09), as an API checks its own tokens
status, _ = a.call("POST", "/admin/realms/aetherspark/clients", {
    "clientId": "stack-test-service", "enabled": True, "publicClient": False,
    "serviceAccountsEnabled": True, "standardFlowEnabled": False, "directAccessGrantsEnabled": False,
    "fullScopeAllowed": False, "secret": secret,
    "protocolMappers": [{"name": "audience", "protocol": "openid-connect",
                         "protocolMapper": "oidc-audience-mapper",
                         "config": {"included.client.audience": "stack-test-service",
                                    "access.token.claim": "true", "id.token.claim": "false",
                                    "introspection.token.claim": "true"}}]})
if status != 201:
    sys.exit(f"test client: HTTP {status}")
with open(sys.argv[2], "w") as f:
    f.write(secret)
EOF
( umask 077; python3 -I "$work/service_client.py" "$repo" "$work/pg/service-secret" ) \
    && chmod 0644 "$work/pg/service-secret"
cat >"$work/pg/backchannel.py" <<'EOF'
import json, socket, ssl, sys, urllib.parse
from http.client import HTTPSConnection
NAME = "auth-backchannel.aether.localhost"
mode, address = sys.argv[1], sys.argv[2]  # address: the name (auth-edge) or Traefik's edge address
ctx = ssl.create_default_context(cafile="/test/root.crt")
class C(HTTPSConnection):
    def connect(self):
        self.sock = ctx.wrap_socket(socket.create_connection((address, 9443), timeout=10), server_hostname=NAME)
def call(path, form=None):
    c = C(NAME, 9443, context=ctx)
    body = urllib.parse.urlencode(form).encode() if form else None
    headers = {"Host": f"{NAME}:9443"}
    if form:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    c.request("POST" if form else "GET", path, body=body, headers=headers)
    r = c.getresponse()
    return r.status, r.read().decode()
base = "/realms/aetherspark/protocol/openid-connect"
if mode == "refused":
    status, _ = call("/realms/aetherspark/.well-known/openid-configuration")
    sys.exit(0 if status == 403 else f"from outside auth-edge: HTTP {status}, not 403")
status, body = call("/realms/aetherspark/.well-known/openid-configuration")
if status != 200 or json.loads(body)["issuer"] != "https://auth.aether.localhost/realms/aetherspark":
    sys.exit(f"discovery: HTTP {status}")
if call(f"{base}/certs")[0] != 200:
    sys.exit("keys (JWKS)")
for path in ("/admin/", "/admin/master/console/", "/realms/master/.well-known/openid-configuration",
             "/realms/aetherspark/account/", "/realms/aetherspark/login-actions/authenticate", "/"):
    if call(path)[0] != 404:
        sys.exit(f"{path} is not refused")
secret = open("/test/service-secret").read().strip()
status, body = call(f"{base}/token", {"grant_type": "client_credentials", "client_id": "stack-test-service", "client_secret": secret})
if status != 200:
    sys.exit(f"client credentials: HTTP {status}")
token = json.loads(body)["access_token"]
status, body = call(f"{base}/token/introspect", {"token": token, "client_id": "stack-test-service", "client_secret": secret})
if status != 200 or not json.loads(body).get("active"):
    sys.exit(f"introspection: HTTP {status}")
print("discovery, keys, client credentials and introspection; admin, master, account and / refused")
EOF
chmod 0644 "$work/pg/backchannel.py"
on_net() { # network, then the command in the egress tests' Python image, with $work/pg at /test
    local net=$1
    shift
    docker run --rm --network "${COMPOSE_PROJECT_NAME}_$net" --read-only --cap-drop ALL \
        --user "$AETHERSPARK_UID:$AETHERSPARK_GID" -v "$work/pg:/test:ro" "$py_image" "$@"
}
check "the back channel (auth-edge): discovery with the public issuer, keys, client credentials, introspection; admin, master, account refused" \
    on_net auth-edge python3 -I /test/backchannel.py works auth-backchannel.aether.localhost
check "the back channel refuses a caller on edge (another network than auth-edge)" \
    on_net edge python3 -I /test/backchannel.py refused 172.30.63.2

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
fingerprint() { on_pki "step certificate fingerprint /tls/$1/tls.crt" 2>/dev/null; }
fa_first="$(fingerprint forward-auth)"
wa_first="$(fingerprint whoami)"
errors=0 pg_errors=0 kc_errors=0
for _ in $(seq 1 30); do # 150 s: the renewers renew before 120 s, Traefik every minute
    [ "$(host_curl "https://$name/stack-test/" 2>/dev/null)" = OK ] || errors=$((errors + 1))
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
watched() { # service, port: renewed, and served after cert-watch's restart (two periods at most)
    local service=$1 port=$2 first=$3
    [ -n "$first" ] && [ "$first" != "$(fingerprint "$service")" ] || { echo "not renewed"; return 1; }
    for _ in $(seq 1 12); do
        tls_py edge "$service-tls" "$service.svc.aether.localhost" "$port" no /test/root.crt >/dev/null 2>&1 && return 0
        sleep 5
    done
    tls_py edge "$service-tls" "$service.svc.aether.localhost" "$port" no /test/root.crt
}
check "forward-auth was renewed and serves the new certificate (cert-watch restarted it)" watched forward-auth 4443 "$fa_first"
check "the test page was renewed and serves the new certificate (cert-watch restarted it)" watched whoami 8443 "$wa_first"
tr_renewed() { [ "$tr_first" != "$(served_serial)" ]; }
check "Traefik renewed its certificate (new serial)" tr_renewed
check "the renewer is healthy" renewer_healthy
# The overnight case: stopped past the renewal point (75 % of the lifetime), not yet expired. A
# fresh certificate first, so that the stop's length is known.
dc stop postgres-auth-renewer >/dev/null 2>&1
on_pki 'rm -f /tls/postgres-auth/tls.crt' >/dev/null 2>&1
dc run --rm cert-bootstrap >/dev/null 2>&1
dc stop >/dev/null 2>&1
overdue() { on_pki 'step certificate needs-renewal /tls/postgres-auth/tls.crt --expires-in 75%'; }
# Until the renewal point has passed, then at once: a fixed wait missed the 45-second window between
# the renewal point and expiry when the stop took long (desktop, 2026-11-10)
for _ in $(seq 1 40); do overdue >/dev/null 2>&1 && break; sleep 5; done
check "after the stop the certificate is past its renewal point" overdue
check "... and not expired" pg_valid
pg_first="$(pg_serial)"
check "just up after the stop reaches healthy" up
check "the certificate was renewed before the stack reported healthy (just up's bootstrap, past the renewal point)" pg_renewed
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

echo "--- WP3.4: the backup job (backup-job.md, tests B1-B5, B7)"
# The target's stand-in: with the host rules, tests/stack/layer3.sh runs it outside Docker's bridges
# at the test NAS address and gives its login in a file; otherwise a container on `backup`, with a
# login made here. An "owner" key creates the repository, as the home infrastructure did on the NAS.
bk() { bash "$repo/deploy/backup/backup.sh" "$@" "${overlay[@]}"; }
# The job's containers mount the staging folder, which must exist (Docker may not create it); the
# test's own restic calls make it as backup.sh does, and backup.sh's next run removes it
bk_staging="$(dirname "$AETHERSPARK_SECRETS_DIR")/backup"
export AETHERSPARK_BACKUP_DIR="$bk_staging"
t_stage() { mkdir -p "$bk_staging/sealed" "$bk_staging/feed" "$AETHERSPARK_PACKAGE_FEED" && chmod 0700 "$bk_staging"; }
# The local folder feed (WP3.5) with one package in it; the snapshot must carry it
feed_pkg=ostium.stack-test.0.0.1.nupkg
mkdir -p "$AETHERSPARK_PACKAGE_FEED"
printf 'a stand-in package\n' >"$AETHERSPARK_PACKAGE_FEED/$feed_pkg"
restic_t() { t_stage && dc --profile backup run --rm --no-deps -T backup "$@"; }
export AETHERSPARK_BACKUP_MARKER="$work/backup-last"
standin_dir="$work/standin"
mkdir -p "$standin_dir/data" "$standin_dir/login" "$work/owner"
if [ -n "${AETHERSPARK_BACKUP_STANDIN:-}" ]; then
    standin_address=$AETHERSPARK_BACKUP_STANDIN
    { read -r bk_name; read -r bk_password; } <"$AETHERSPARK_STANDIN_LOGIN_FILE"
else
    standin_address=backup-standin:8000
    bk_name=stack-test
    bk_password="$(python3 -I -c 'import secrets; print(secrets.token_urlsafe(24))')"
    dc --profile standin build backup-standin >/dev/null 2>&1
    printf '%s\n' "$bk_password" | docker run --rm -i --network none --entrypoint bcrypt-login aetherspark/rest-server:local "$bk_name" \
        >"$standin_dir/login/.htpasswd"
    chmod 0644 "$standin_dir/login/.htpasswd"
    export AETHERSPARK_STANDIN_DIR="$standin_dir"
    dc --profile standin up -d backup-standin >/dev/null 2>&1
fi
python3 -I -c 'import secrets; print(secrets.token_urlsafe(32))' >"$work/owner/pw"
chmod 0644 "$work/owner/pw"
# Never `grep -q` at the end of a pipeline here (pipefail: an early exit reads as "not found")
no_login_yet() { bk run --if-due 2>&1 | grep 'skipped: no backup login' >/dev/null; }
check "B: just up before the login is entered warns and skips the backup" no_login_yet
enter_login() {
    enter backup-login-name "$bk_name" && enter backup-login-password "$bk_password" \
        && enter backup-address "$standin_address" && enter backup-repository-path "$bk_name/stack" \
        && python3 -I "$repo/deploy/secret-store/secret_store.py" unseal >/dev/null
}
check "B: the login is entered (four entered secrets) and unsealed" enter_login
owner_restic() { # restic with the owner's key (a file here; the owner types it on the NAS's)
    t_stage && dc --profile backup run --rm --no-deps -T -e RESTIC_PASSWORD_FILE=/owner/pw -v "$work/owner:/owner:ro" backup "$@"
}
owner_setup() {
    owner_restic init >/dev/null && owner_restic key add --new-password-file /run/secrets/backup-key-password >/dev/null
}
check "B: the owner creates the repository and adds AetherSpark's key (as key-add does)" owner_setup
bk_out="$(bk run 2>&1)" && bk_rc=0 || bk_rc=$?
check "B: just backup makes a snapshot" [ "$bk_rc" -eq 0 ]
[ "$bk_rc" -eq 0 ] || printf '%s\n' "$bk_out" | tail -15 | sed 's/^/      /'
snap="$(restic_t snapshots --json --host aetherspark-desktop --tag aetherspark-stack --latest 1 2>/dev/null \
    | python3 -I -c 'import json, sys; s = json.load(sys.stdin); print(s[-1]["id"] if s else "")')"
b1() { # exactly the four paths; no TLS volume, no unsealed secret, no age key; the sealed files only
    restic_t ls --json "$snap" | python3 -I -c 'import json, sys
items = [json.loads(l) for l in sys.stdin if l.strip()]
paths = sorted(i["path"] for i in items if "node" in (i.get("struct_type"), i.get("message_type")) and "path" in i)
tops = {p.split("/")[2] for p in paths if p.startswith("/backup/") and p.count("/") >= 2}
bad = [p for p in paths if "-tls" in p or "/run/secrets" in p or p.endswith(".key")]
sealed = [p for p in paths if p.startswith("/backup/sealed/") and not p.endswith(".age")]
dump = "/backup/keycloak/keycloak.dump" in paths
ca = any(p.startswith("/backup/ca/certs/root_ca.crt") for p in paths)
feed = "/backup/feed/" + sys.argv[1] in paths
ok = tops == {"ca", "keycloak", "sealed", "feed"} and not bad and not sealed and dump and ca and feed
print(sorted(tops), bad[:3], sealed[:3], dump, ca, feed) if not ok else None
sys.exit(0 if ok else 1)' "$feed_pkg"
}
check "B1: the snapshot holds exactly ca, keycloak (the dump), sealed (*.age only) and feed (its package)" b1
b2() { # the job's settings name files only; no secret value in its rendered Compose or its output.
    # The login's name, the address and the path are not secret (the name is part of this project's)
    local config f
    config="$(dc --profile backup config --format json)"
    for f in "$AETHERSPARK_SECRETS_DIR"/*; do
        case $(basename "$f") in backup-login-name | backup-address | backup-repository-path) continue ;; esac
        if grep -F -f "$f" <<<"$config$bk_out" >/dev/null; then echo "a value of $(basename "$f") is visible"; return 1; fi
    done
}
check "B2: no secret value in the job's configuration or output" b2
b3_forget() { ! restic_t forget "$snap" >/dev/null 2>&1; }
check "B3: the job's login cannot forget a snapshot (append-only)" b3_forget
still_there() { restic_t snapshots --json "$snap" | grep "$snap" >/dev/null; }
check "B3: ... and the snapshot is still there" still_there
if [ -n "${AETHERSPARK_BACKUP_STANDIN:-}" ]; then
    check "B3: an HTTP DELETE of the snapshot is refused (403), from the host" bk prove-append-only
else
    printf 'SKIP  B3: the HTTP DELETE runs from the host, which reaches the stand-in only in CI (layer 3)\n'
fi
count_snaps() { restic_t snapshots --json --host aetherspark-desktop --tag aetherspark-stack | python3 -I -c 'import json, sys; print(len(json.load(sys.stdin)))'; }
n_before="$(count_snaps)"
bk run --if-due >/dev/null 2>&1
check "B4: a second run the same day (just up's) makes no snapshot" [ "$(count_snaps)" = "$n_before" ]
rm -f "$AETHERSPARK_BACKUP_MARKER"
# With the host rules, the stand-in is a process the wrapper controls (AETHERSPARK_STANDIN_CTL)
standin() { if [ -n "${AETHERSPARK_STANDIN_CTL:-}" ]; then "$AETHERSPARK_STANDIN_CTL" "$1"; else dc --profile standin "$1" backup-standin >/dev/null 2>&1; fi; }
standin stop
unreachable() { bk run --if-due 2>&1 | grep 'skipped: the backup target does not answer' >/dev/null; }
check "B4: with the target unreachable, just up's run warns and goes on" unreachable
standin start
check "B5: step-ca is healthy after the jobs" [ "$(docker inspect -f '{{.State.Health.Status}}' "$(dc ps -q step-ca)")" = healthy ]
check "B5: no staging folder is left" [ ! -e "$(dirname "$AETHERSPARK_SECRETS_DIR")/backup" ]
dc stop postgres-auth >/dev/null 2>&1
dump_fails() { ! bk run >/dev/null 2>&1 && [ ! -e "$(dirname "$AETHERSPARK_SECRETS_DIR")/backup" ]; }
check "B5: a failed job (the dump) leaves no staging folder" dump_fails
check "B5: ... and step-ca running" [ "$(docker inspect -f '{{.State.Health.Status}}' "$(dc ps -q step-ca)")" = healthy ]
# A new container, as up.sh starts one: the B tests unsealed again since postgres-auth was created,
# and Docker Desktop keeps a secret file as it was at creation (the desktop run of 2026-11-17)
check "B5: postgres-auth is back after the failed job" dc up -d --wait --force-recreate postgres-auth
if [ -n "$layer3" ]; then
    b7() { # the job's network reaches the target and nothing else; the drop is logged
        local mark out
        mark="$(klog_mark)"
        out="$(docker run --rm --network "${COMPOSE_PROJECT_NAME}_backup" --read-only --cap-drop ALL "$curl_image_b7" \
            sh -c "curl -s -m 5 -o /dev/null -w '%{http_code}' http://$standin_address/ || true; echo; curl -s -m 5 -k -o /dev/null https://1.1.1.1/ && echo out || echo none")"
        printf '%s\n' "$out" | head -1 | grep -qE '^(200|401|404|405)$' || { echo "the target did not answer: $out"; return 1; }
        printf '%s\n' "$out" | tail -1 | grep -qx none || { echo "reached the internet"; return 1; }
        drops_since_b7 "$mark" | grep 'IN=as-backup ' >/dev/null || { echo "no drop logged on as-backup"; return 1; }
    }
    curl_image_b7="$(sed -n 's/^ *image: *\(curlimages\/curl:[^ ]*\) *$/\1/p' "$repo/tests/egress/compose.test.yaml" | head -1)"
    drops_since_b7() { klog | awk -F'[][]' -v since="$1" '$2 + 0 > since + 0' | grep -E 'aetherspark-[a-z]+:IN=as-backup '; }
    check "B7: from the backup network, the target answers, the internet does not, and the drop is logged" b7
else
    printf 'SKIP  B7: the backup network reaches only the target with the host rules (CI and the Spark)\n'
fi

echo "--- C6: the egress rules hold for the stack's own services (egress.md, Tests)"
# Each probe runs inside a service's own network namespace (--network container:), so it has the
# service's routes and resolver whatever tools the service's image has. Busybox's nslookup and curl
# from the egress tests' client image. Every "fail" has a control that must work from the same
# place: a name of the stack the service may resolve.
curl_image="$(sed -n 's/^ *image: *\(curlimages\/curl:[^ ]*\) *$/\1/p' "$repo/tests/egress/compose.test.yaml" | head -1)"
cat >"$work/pg/egress.sh" <<'EOF'
# One line per probe, "<name> ok" or "<name> fail". $1: a stack name the service may resolve
# (control); $2: its network's gateway, for the host-service probe (layer 3), or nothing. Docker's
# resolver is asked by its address: a container joining another's namespace may get another
# resolv.conf (CI's Docker Engine did, 2026-11-10), so the default resolver proves nothing.
t() { n=$1; shift; if "$@" >/dev/null 2>&1; then echo "$n ok"; else echo "$n fail"; fi; }
echo "resolv.conf $(awk '/^nameserver/ {printf "%s ", $2}' /etc/resolv.conf)"
t control nslookup "$1" 127.0.0.11
t https-address curl -s -m 5 -k -o /dev/null https://1.1.1.1/
t https-name curl -s -m 5 -o /dev/null https://www.sec.gov/
t dns-docker nslookup www.sec.gov 127.0.0.11
t dns-public nslookup www.sec.gov 1.1.1.1
t ipv6 curl -s -m 5 -g -6 -k -o /dev/null "https://[2606:4700:4700::1111]/"
if [ -n "${2:-}" ]; then t host-service curl -s -m 5 -o /dev/null "http://$2:8099/"; fi
EOF
chmod 0644 "$work/pg/egress.sh"
probe() { # service, control name, gateway or "": the probe lines from the service's namespace
    local id
    id="$(dc ps -q "$1")"
    [ -n "$id" ] || { echo "no container"; return 1; }
    docker run --rm --network "container:$id" --read-only --cap-drop ALL \
        --user "$AETHERSPARK_UID:$AETHERSPARK_GID" -v "$work/pg:/test:ro" \
        --entrypoint sh "$curl_image" /test/egress.sh "$2" "$3"
}
c6() { # service, control name, gateway (layer 3) or "": the control works, everything else fails
    local service=$1 out p want got said
    out="$(probe "$@" 2>&1)"
    for p in control https-address https-name dns-docker dns-public ipv6 ${3:+host-service}; do
        want=fail said=fails
        [ "$p" = control ] && want=ok said=works
        got="$(printf '%s\n' "$out" | awk -v p="$p" '$1 == p {print $2}')"
        if [ "$got" = "$want" ]; then
            pass "$service: $p $said"
        else
            fail "$service: $p (expected $want, got ${got:-nothing})" "$out"
        fi
    done
}
# Kernel log lines since a mark, from the stack's own bridges. Never `grep -q` at the end of a
# pipeline: it exits at the first match and, with pipefail, a found line would read as not found.
stack_bridges='as-(ingress|auth-front|pki|db-auth|edge|auth-edge|ingest)'
drops_since() { klog | awk -F'[][]' -v since="$1" '$2 + 0 > since + 0' | grep -E "aetherspark-[a-z]+:IN=$stack_bridges "; }
gw() { [ -n "$layer3" ] && echo "$1"; } # the host-service probe needs layer 3's host listener
if [ -z "$curl_image" ]; then
    fail "the egress tests' client image is found (tests/egress/compose.test.yaml)"
else
    host_pid=
    if [ -n "$layer3" ]; then
        # E14, adapted: the whole run so far (sign-ins, renewals, restarts) sent nothing that the
        # rules dropped from the stack's networks. Before the probes below, which are dropped on purpose.
        no_drops() {
            local lines
            lines="$(drops_since "$klog_start")"
            [ -z "$lines" ] && return 0
            printf '%s\n' "$lines" | grep -oE 'aetherspark-[a-z]+:IN=[^ ]+ OUT=[^ ]*|DST=[^ ]+|PROTO=[^ ]+|DPT=[0-9]+' \
                | paste -sd ' ' | fold -w 200 | head -10
            return 1
        }
        check "E14: nothing from the stack's networks was dropped during the whole run" no_drops
        # E16: a host service on every address; containers must not reach it through a bridge
        python3 -m http.server 8099 --bind 0.0.0.0 >/dev/null 2>&1 &
        host_pid=$!
        sleep 1
        check "E16 control: the host service answers on the host" curl -s -m 5 -o /dev/null http://127.0.0.1:8099/
    fi
    probes_start="$( [ -n "$layer3" ] && klog_mark)"
    c6 step-ca traefik "$(gw 172.30.50.1)"
    c6 postgres-auth-renewer step-ca "$(gw 172.30.50.1)"
    c6 postgres-auth keycloak "$(gw 172.30.60.1)"
    c6 postgres-data "$pgd" "$(gw 172.30.70.1)"
    c6 clickhouse "$ch" "$(gw 172.30.70.1)"
    c6 ingester "$pgd" "$(gw 172.30.70.1)"
    c6 keycloak traefik "$(gw 172.30.61.1)"
    c6 forward-auth traefik "$(gw 172.30.63.1)"
    c6 whoami traefik "$(gw 172.30.63.1)"
    if [ -n "$layer3" ]; then
        # Traefik is on ingress, a network with a route out: only the host rules stop it (E9)
        c6 traefik keycloak 172.30.30.1
        traefik_dropped() { drops_since "$probes_start" | grep 'IN=as-ingress ' >/dev/null; }
        check "E9: Traefik's attempts were dropped and logged on ingress" traefik_dropped
        kill "$host_pid" 2>/dev/null
        wait "$host_pid" 2>/dev/null
    else
        printf 'SKIP  traefik: on ingress, a network with a route out; only the host rules (layer 3, CI and the Spark) stop it\n'
    fi
fi

echo "--- WP3.4: the restore drill (backup-job.md, test B6)"
# A clean state, as after losing the machine: no volumes, no sealed store, no in-memory secrets.
# Only the dev key stays, standing for its off-line copy. The owner enters the backup login again,
# restores the store with the repository's original key (the test's "owner" key, a file here), then
# everything else with AetherSpark's own. A user made before the backup must sign in after it.
root_fp() { ca status | sed -n 's/.*root fingerprint \([0-9a-f]*\).*/\1/p'; }
fp_before="$(root_fp)"
rm -f "$AETHERSPARK_BACKUP_MARKER"
check "B6: a fresh snapshot before the drill" bk run
refuse "B6: just backup restore refuses while the stack runs" bk restore
dc down -v >/dev/null 2>&1
python3 -I "$repo/deploy/secret-store/secret_store.py" clear >/dev/null
rm -rf "$AETHERSPARK_SECRETS_STORE" "$AETHERSPARK_PACKAGE_FEED"
[ -n "${AETHERSPARK_STANDIN_CTL:-}" ] || dc --profile standin up -d backup-standin >/dev/null 2>&1
check "B6: nothing left: no volume, no sealed store, no local feed" bash -c "! docker volume inspect '${COMPOSE_PROJECT_NAME}_step-ca' >/dev/null 2>&1 && [ ! -e '$AETHERSPARK_SECRETS_STORE' ] && [ ! -e '$AETHERSPARK_PACKAGE_FEED' ]"
enter_only() {
    enter backup-login-name "$bk_name" && enter backup-login-password "$bk_password" \
        && enter backup-address "$standin_address" && enter backup-repository-path "$bk_name/stack"
}
check "B6: the owner enters the backup login again" enter_only
store_back() { AETHERSPARK_BACKUP_OWNER_PASSWORD_FILE="$work/owner/pw" bk restore-secrets; }
check "B6: restore-secrets brings the sealed store back with the owner's key" store_back
check "B6: ... every sealed secret of the manifest is there" python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); import secret_store
m = secret_store.load_manifest(); s = secret_store.store_dir()
missing = [x.name for x in m if not x.derived and not (s / f"{x.name}.age").is_file()]
print(missing) if missing else None; sys.exit(1 if missing else 0)' "$repo/deploy/secret-store"
check "B6: ... and the local feed with its package" grep -qxF 'a stand-in package' "$AETHERSPARK_PACKAGE_FEED/$feed_pkg"
restore_all() { bk restore --expect-root "$fp_before"; }
rm -f "$AETHERSPARK_BACKUP_MARKER"
check "B6: just backup restore brings back the CA and Keycloak's database (root fingerprint as before)" restore_all
check "B6: ... and counts as today's snapshot, so the just up after it adds none" [ "$(cat "$AETHERSPARK_BACKUP_MARKER" 2>/dev/null)" = "$(date -u +%F)" ]
check "B6: just up after the restore reaches healthy" up
check "B6: the root CA is the same" [ "$(root_fp)" = "$fp_before" ]
check "B6: a user made before the backup signs in (password and app code)" oidc "$work/user" stack-test-user
check "B6: keycloak apply reports the realm as the repository says" python3 -I "$repo/deploy/keycloak/admin.py" apply
dc stop >/dev/null 2>&1
future() { # a snapshot with a forged date, as a compromised desktop could add
    t_stage && mkdir -p "$bk_staging/feed" \
        && owner_restic backup --time "2099-01-01 00:00:00" --host aetherspark-desktop --tag aetherspark-stack /backup/feed --json \
        | python3 -I -c 'import json, sys
ids = [m["snapshot_id"] for m in map(json.loads, filter(None, sys.stdin.read().splitlines())) if m.get("message_type") == "summary"]
print(ids[-1] if ids else "")'
}
future_id="$(future)"
check "B6: a snapshot dated 2099 is made (the forgery to refuse)" [ -n "$future_id" ]
refuse "B6: a snapshot dated in the future is refused by name" bk restore --snapshot "$future_id"
not_over_live() {
    local out
    out="$(bk restore 2>&1)" && { echo "the restore ran over live volumes:"; printf '%s\n' "$out" | tail -8; return 1; }
    grep 'is not empty' <<<"$out" >/dev/null || { printf '%s\n' "$out" | tail -8; return 1; }
}
check "B6: ... and by default the newest real one is chosen, but never over live volumes" not_over_live

echo "stack-test: $fails failed"
[ "$fails" -eq 0 ]
