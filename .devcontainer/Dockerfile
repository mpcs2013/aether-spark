# Dev container: Ubuntu 24.04, the DGX OS userland, with the repo's pinned toolchain (WP2.4).
# Every input is pinned by digest or checksum (ADR-0005); builds for amd64 and arm64.

FROM ghcr.io/astral-sh/uv:0.13.3@sha256:bee0a2a458fd956c5df7b8b4c5fed2257b0af34866e3da7b51db0be3acb375d1 AS uv
FROM ghcr.io/gitleaks/gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f AS gitleaks

# .NET SDK 10.0.401 on Ubuntu 24.04 (noble), matching global.json
FROM mcr.microsoft.com/dotnet/sdk:10.0.401-noble@sha256:e70cdb7f80b0348f5cb85f19a8f670fca061f033d57eed12fa003d58b0e06317

ARG TARGETARCH
ARG JUST_VERSION=1.59.0
ARG JUST_SHA256_AMD64=409044617b6f06517c1035e55ca38c8c856ff8b650e41bc2aa1ef82d5903a23e
ARG JUST_SHA256_ARM64=23871b1702bfd550fb5f901778fd4290ecf2ee034a45a889fbe96d89b188086d

RUN set -eu; \
    case "${TARGETARCH}" in \
        amd64) triple=x86_64-unknown-linux-musl; sum="${JUST_SHA256_AMD64}" ;; \
        arm64) triple=aarch64-unknown-linux-musl; sum="${JUST_SHA256_ARM64}" ;; \
        *) echo "unsupported architecture ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    archive="just-${JUST_VERSION}-${triple}.tar.gz"; \
    curl -fsSLo "/tmp/${archive}" "https://github.com/casey/just/releases/download/${JUST_VERSION}/${archive}"; \
    echo "${sum}  /tmp/${archive}" | sha256sum -c -; \
    tar -xzf "/tmp/${archive}" -C /usr/local/bin just; \
    rm "/tmp/${archive}"

COPY --from=uv /uv /uvx /usr/local/bin/
COPY --from=gitleaks /usr/bin/gitleaks /usr/local/bin/gitleaks

# The workspace's .venv and bin/obj belong to the host OS; the container keeps its own
ENV UV_PROJECT_ENVIRONMENT=/home/app/.venv \
    AETHERSPARK_ARTIFACTS=/home/app/artifacts \
    UV_LINK_MODE=copy \
    DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_NOLOGO=1

USER app
