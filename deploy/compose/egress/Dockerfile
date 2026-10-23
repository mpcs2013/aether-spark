# Egress proxy image (docs/architecture/egress.md, WP3n.3): Squid built with OpenSSL, which the SNI
# check (ssl_bump peek) needs; Canonical's ubuntu/squid image is the GnuTLS build and cannot do it.
# Every input is pinned (ADR-0005): base images by digest, packages by an Ubuntu archive snapshot.

# CA bundle only: the Ubuntu base has none, and apt must fetch over HTTPS (the ops proxy allows 443 only)
FROM mcr.microsoft.com/dotnet/runtime-deps:10.0-noble@sha256:1c20bc9ebda297198be069d82b6c5d1716faeeec8b8dca53ef963f9c92d3e2e3 AS ca

FROM ubuntu:24.04@sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac

# Bump deliberately, e.g. after an Ubuntu security notice for squid or openssl
ARG UBUNTU_SNAPSHOT=20261023T000000Z

COPY --from=ca /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt

RUN set -eu; \
    sed -i 's|http://|https://|g' /etc/apt/sources.list.d/ubuntu.sources; \
    apt-get update -qq --snapshot "${UBUNTU_SNAPSHOT}"; \
    apt-get install -y -qq --no-install-recommends --snapshot "${UBUNTU_SNAPSHOT}" squid-openssl openssl; \
    rm -rf /var/lib/apt/lists/* /etc/squid/squid.conf; \
    squid -v | grep -q -- '--with-openssl'

COPY entrypoint.sh /usr/local/bin/egress-proxy
COPY squid-common.conf /etc/squid/common.conf

USER proxy
ENTRYPOINT ["/usr/local/bin/egress-proxy"]
