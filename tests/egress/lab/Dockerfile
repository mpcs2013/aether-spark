# Lab for the Spark host rules (tests/egress/host-lab.sh): Ubuntu 24.04, DGX OS 7's base, with the
# tools the tests use. Pinned like the proxy image (ADR-0005): the base by digest, packages by exact
# version; a newer Ubuntu update makes the build fail until the pin is bumped. Runs privileged, with
# no network of its own: it builds a small LAN out of network namespaces inside itself.
FROM ubuntu:25.10@sha256:7cc5e35f6567ee8c66d2abb4aab0fd866669e6207c237c3a8f0947a5c7f17092

ARG IPTABLES_VERSION=1.8.10-3ubuntu2
ARG IPROUTE2_VERSION=6.1.0-1ubuntu6.4
ARG PYTHON3_VERSION=3.12.3-0ubuntu2.1
ARG SOCAT_VERSION=1.8.0.0-4ubuntu0.1
# For one test only: deleting the tables (`nft flush ruleset`), the case the rules cannot survive
ARG NFTABLES_VERSION=1.0.9-1ubuntu0.1

RUN set -eu; \
    apt-get update -qq; \
    apt-get install -y -qq --no-install-recommends \
        "iptables=${IPTABLES_VERSION}" "iproute2=${IPROUTE2_VERSION}" \
        "python3=${PYTHON3_VERSION}" "socat=${SOCAT_VERSION}" "nftables=${NFTABLES_VERSION}"; \
    rm -rf /var/lib/apt/lists/*; \
    iptables -V | grep -q nf_tables; \
    for u in systemd-resolve systemd-timesync nut otelcol; do useradd --system --no-create-home "$u"; done

CMD ["sleep", "infinity"]
