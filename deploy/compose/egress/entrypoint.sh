#!/bin/sh
# The ssl-bump port needs a certificate. Allowed tunnels are only peeked at and spliced, so they
# never see it; refused CONNECTs get Squid's 403 page under it. Nobody trusts it, it signs nothing
# (generate-host-certificates=off) and it is made fresh in tmpfs at every start, so no key lives
# in the image.
set -eu
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj "/CN=egress-proxy-refusal" \
    -keyout /tmp/bump.key -out /tmp/bump.crt 2>/dev/null
cat /tmp/bump.crt /tmp/bump.key > /tmp/bump.pem
rm /tmp/bump.key
squid -k parse -f /etc/squid/squid.conf
exec squid -N -f /etc/squid/squid.conf
