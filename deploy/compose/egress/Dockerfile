# Egress proxy image (docs/architecture/egress.md, WP3n.3): Squid built with OpenSSL, which the SNI
# check (ssl_bump peek) needs; Canonical's ubuntu/squid image is the GnuTLS build and cannot do it.
# Every input is pinned (ADR-0005): base images by digest, packages by exact version. Not by an
# Ubuntu snapshot date: snapshot.ubuntu.com does not serve ports.ubuntu.com, so arm64 (the Spark)
# could not use it.

# CA bundle only: the Ubuntu base has none, and apt must fetch over HTTPS (the ops proxy allows 443 only)
FROM mcr.microsoft.com/dotnet/runtime-deps:10.0-noble@sha256:9cab4d7d886226d47b5489ef9abf0544e2f109fde11a8acc190217610af26c00 AS ca

FROM ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852

# Versions from noble-security, the same on amd64 and arm64. The archive keeps only the newest
# version of a package per pocket, so a new Ubuntu security notice for squid or openssl makes this
# build fail until the pin is bumped: deliberate, the notice is the trigger for the bump.
ARG SQUID_VERSION=6.14-0ubuntu0.24.04.5
ARG OPENSSL_VERSION=3.0.13-0ubuntu3.16
# apt itself, fixed for CVE-2026-109518; the base image still ships 2.8.3 (2026-11-06)
ARG APT_VERSION=2.8.4ubuntu0.1

COPY --from=ca /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt

RUN set -eu; \
    sed -i 's|http://|https://|g' /etc/apt/sources.list.d/ubuntu.sources; \
    apt-get update -qq; \
    apt-get install -y -qq --no-install-recommends \
        "squid-openssl=${SQUID_VERSION}" "squid-common=${SQUID_VERSION}" "openssl=${OPENSSL_VERSION}" \
        "apt=${APT_VERSION}" "libapt-pkg6.0t64=${APT_VERSION}"; \
    rm -rf /var/lib/apt/lists/* /etc/squid/squid.conf; \
    squid -v | grep -q -- '--with-openssl'

COPY entrypoint.sh /usr/local/bin/egress-proxy
COPY squid-common.conf /etc/squid/common.conf

USER proxy
ENTRYPOINT ["/usr/local/bin/egress-proxy"]
