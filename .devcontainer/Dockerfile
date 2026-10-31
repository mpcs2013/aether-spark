# Dev container: Ubuntu 24.04, the DGX OS userland, with the repo's pinned toolchain (WP2.4).
# Every input is pinned by digest or checksum (ADR-0005); builds for amd64 and arm64.

FROM ghcr.io/astral-sh/uv:0.13.3@sha256:bee0a2a458fd956c5df7b8b4c5fed2257b0af34866e3da7b51db0be3acb375d1 AS uv

# gitleaks from its tagged source, built with a current Go: the release image and binaries carry an
# old Alpine and Go 1.24's standard library, with fixed High/Critical findings (#31). The source
# archive is checked by SHA-256, its modules by go.sum. CI's gitleaks job runs this same binary
# (target gitleaks-bin), so the pre-commit hook, the container and CI share one version.
FROM golang:1.26.10-alpine3.24@sha256:a2012b4a1d1f9f7e9f44e0d39b805a901e856b1dd80b4e6ee4338db547050308 AS gitleaks-build
ARG GITLEAKS_VERSION=8.30.1
ARG GITLEAKS_SHA256=e90fb266d75837e75894c778bf594ab8e2787f12dce5a62651f21b893eaf9abb
RUN set -eu; \
    wget -qO /tmp/gitleaks.tar.gz "https://github.com/gitleaks/gitleaks/archive/refs/tags/v${GITLEAKS_VERSION}.tar.gz"; \
    echo "${GITLEAKS_SHA256}  /tmp/gitleaks.tar.gz" | sha256sum -c -; \
    mkdir /src; \
    tar -xzf /tmp/gitleaks.tar.gz -C /src --strip-components=1; \
    cd /src; \
    # v8.30.1 requires x/text v0.22.0 (GO-2026-5970, fixed in v0.39.0); checked against sum.golang.org
    GOTOOLCHAIN=local go get golang.org/x/text@v0.39.0; \
    CGO_ENABLED=0 GOTOOLCHAIN=local GOFLAGS=-mod=readonly go build -trimpath \
        -ldflags "-s -w -X=github.com/zricethezav/gitleaks/v8/version.Version=${GITLEAKS_VERSION}" \
        -o /gitleaks .; \
    test "$(/gitleaks version)" = "${GITLEAKS_VERSION}"

FROM scratch AS gitleaks-bin
COPY --from=gitleaks-build /gitleaks /gitleaks

# .NET SDK on Ubuntu 24.04 (noble); global.json names the minimum (latestFeature roll-forward)
FROM mcr.microsoft.com/dotnet/sdk:10.0.403-noble@sha256:de4fe90a98845d75910b9769662465c84de1a934665c431279d1382b2bddc02b

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
COPY --from=gitleaks-bin /gitleaks /usr/local/bin/gitleaks

# The workspace's .venv and bin/obj belong to the host OS; the container keeps its own
ENV UV_PROJECT_ENVIRONMENT=/home/app/.venv \
    AETHERSPARK_ARTIFACTS=/home/app/artifacts \
    UV_LINK_MODE=copy \
    DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_NOLOGO=1

USER app
