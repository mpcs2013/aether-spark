# Test machine for the Spark host role (tests/host-role/run.sh): Ubuntu 24.04, DGX OS 7's base, with
# systemd as PID 1 and the services the role configures. Pinned like the other test images
# (ADR-0005): the base and uv by digest, packages by exact version; a newer Ubuntu update makes the
# build fail until the pin is bumped. Hardware the container lacks (radios, the LAN port, Docker)
# is left to the Spark's own tests.
FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv

FROM ubuntu:25.10@sha256:7cc5e35f6567ee8c66d2abb4aab0fd866669e6207c237c3a8f0947a5c7f17092

ARG SYSTEMD_VERSION=255.4-1ubuntu8.18
ARG OPENSSH_VERSION=1:9.6p1-3ubuntu13.20
ARG PYTHON3_VERSION=3.12.3-0ubuntu2.1
ARG PYTHON3_APT_VERSION=2.7.7ubuntu5.3
ARG SUDO_VERSION=1.9.15p5-3ubuntu5.24.04.4
ARG KMOD_VERSION=31+20240202-2ubuntu7.2
# The role switches apt to HTTPS; DGX OS has the CA bundle, the bare base image does not
ARG CA_CERTIFICATES_VERSION=20260816ubuntu0.24.04.1
# ss, for the node-exporter assertion; DGX OS has it
ARG IPROUTE2_VERSION=6.1.0-1ubuntu6.4
# git, for the signed-tag checks of deploy/ansible/run.sh
ARG GIT_VERSION=1:2.43.0-1ubuntu7.4

RUN set -eu; \
    apt-get update -qq; \
    apt-get install -y -qq --no-install-recommends \
        "systemd=${SYSTEMD_VERSION}" "systemd-sysv=${SYSTEMD_VERSION}" \
        "systemd-resolved=${SYSTEMD_VERSION}" "systemd-timesyncd=${SYSTEMD_VERSION}" \
        "openssh-server=${OPENSSH_VERSION}" "python3=${PYTHON3_VERSION}" \
        "python3-apt=${PYTHON3_APT_VERSION}" "sudo=${SUDO_VERSION}" "kmod=${KMOD_VERSION}" \
        "ca-certificates=${CA_CERTIFICATES_VERSION}" "iproute2=${IPROUTE2_VERSION}" "git=${GIT_VERSION}"; \
    rm -rf /var/lib/apt/lists/*; \
    useradd --create-home --shell /bin/bash --groups sudo spark-admin; \
    echo "spark-admin:$(head -c 18 /dev/urandom | base64)" | chpasswd

COPY --from=uv /uv /usr/local/bin/uv
# uv uses the system Python, as on the Spark, and never downloads one
ENV UV_PYTHON=/usr/bin/python3 UV_PYTHON_DOWNLOADS=never

STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
