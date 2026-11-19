#!/bin/sh
# One-shot at `just up` (compose-base.md, "Bootstrap and renewal"): for each service in $SERVICES,
# a certificate for <service>.svc.aether.$PLACE in /tls/<service>/ unless a usable one is there.
# Its JWK provisioner's password is read from /run/secrets/step-ca-provisioner-<service> by step
# itself; nothing here prints or holds it. Runs in the step-ca image (deploy/compose/stack/).
set -eu
umask 077
root=/ca-certs/root_ca.crt
# Less than 75 % of the lifetime has passed: the renewers' health threshold (compose.yaml). A
# certificate past it may expire before its renewer starts, and an expired one cannot be renewed
# (the renewal authenticates with it): the renewer would stay unhealthy (desktop, 2026-11-18)
fresh() { step certificate needs-renewal "$1" --expires-in 75% >/dev/null 2>&1; [ $? -eq 1 ]; }
for service in $SERVICES; do
    name="$service.svc.aether.$PLACE"
    dir="/tls/$service"
    # Usable means: chains to this CA's root, names the service, not past the renewal point
    if [ -f "$dir/tls.crt" ] && step certificate verify "$dir/tls.crt" --roots "$root" \
        --host "$name" >/dev/null 2>&1 && fresh "$dir/tls.crt"; then
        echo "cert-bootstrap: $service has a valid certificate"
        continue
    fi
    step ca certificate "$name" "$dir/tls.crt.new" "$dir/tls.key.new" \
        --provisioner "$service" \
        --provisioner-password-file "/run/secrets/step-ca-provisioner-$service" \
        --ca-url "$CA_URL" --root "$root" --kty EC --curve P-256 --force >/dev/null
    # What was issued must itself be valid: an expired intermediate fails here, visibly
    if ! step certificate verify "$dir/tls.crt.new" --roots "$root" --host "$name"; then
        echo "cert-bootstrap: $service: the issued certificate does not verify (ca status)" >&2
        rm -f "$dir/tls.crt.new" "$dir/tls.key.new"
        exit 1
    fi
    mv -f "$dir/tls.key.new" "$dir/tls.key"
    mv -f "$dir/tls.crt.new" "$dir/tls.crt"
    echo "cert-bootstrap: $service issued ($(step certificate inspect "$dir/tls.crt" --format json \
        | sed -n 's/.*"end": *"\([^"]*\)".*/until \1/p' | head -1))"
done
