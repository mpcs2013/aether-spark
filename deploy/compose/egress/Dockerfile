# Egress proxy image (docs/architecture/egress.md, WP3n.3): Squid built with OpenSSL, which the SNI
# check (ssl_bump peek) needs; Canonical's ubuntu/squid image is the GnuTLS build and cannot do it.
# Every input is pinned (ADR-0005): base images by digest, packages by exact version. Not by an
# Ubuntu snapshot date: snapshot.ubuntu.com does not serve ports.ubuntu.com, so arm64 (the Spark)
# could not use it.

# CA bundle only: the Ubuntu base has none, and apt must fetch over HTTPS (the ops proxy allows 443 only)
FROM mcr.microsoft.com/dotnet/runtime-deps:10.0-noble@sha256:62028293921f5bd6db3d9f9119cd29cd926dfd3c2336da23b09f425e40c3c832 AS ca

FROM ubuntu:25.10@sha256:7cc5e35f6567ee8c66d2abb4aab0fd866669e6207c237c3a8f0947a5c7f17092

# Versions from noble-security, the same on amd64 and arm64. The archive keeps only the newest
# version of a package per pocket, so a new Ubuntu security notice for squid or openssl makes this
# build fail until the pin is bumped: deliberate, the notice is the trigger for the bump.
ARG SQUID_VERSION=6.14-0ubuntu0.24.04.5
ARG OPENSSL_VERSION=3.0.13-0ubuntu3.16

COPY --from=ca /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt

RUN set -eu; \
    sed -i 's|http://|https://|g' /etc/apt/sources.list.d/ubuntu.sources; \
    apt-get update -qq; \
    apt-get install -y -qq --no-install-recommends \
        "squid-openssl=${SQUID_VERSION}" "squid-common=${SQUID_VERSION}" "openssl=${OPENSSL_VERSION}"; \
    rm -rf /var/lib/apt/lists/* /etc/squid/squid.conf; \
    squid -v | grep -q -- '--with-openssl'

COPY entrypoint.sh /usr/local/bin/egress-proxy
COPY squid-common.conf /etc/squid/common.conf

USER proxy
ENTRYPOINT ["/usr/local/bin/egress-proxy"]
