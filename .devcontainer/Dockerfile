# Dev container: Ubuntu 24.04, the DGX OS userland, with the repo's pinned toolchain (WP2.4).
# Every input is pinned by digest or checksum (ADR-0005); builds for amd64 and arm64.

FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv

# gitleaks from its tagged source, built with a current Go: the release image and binaries carry an
# old Alpine and Go 1.24's standard library, with fixed High/Critical findings (#31). The source
# archive is checked by SHA-256, its modules by go.sum. CI's gitleaks job runs this same binary
# (target gitleaks-bin), so the pre-commit hook, the container and CI share one version.
FROM golang:1.27.3-alpine3.24@sha256:d200d2dd76b03a99b52651fd1325aff7dcd35422c35c01bac56571b0507f3deb AS gitleaks-build
ARG GITLEAKS_VERSION=8.30.1
ARG GITLEAKS_SHA256=e90fb266d75837e75894c778bf594ab8e2787f12dce5a62651f21b893eaf9abb
RUN set -eu; \
    wget -qO /tmp/gitleaks.tar.gz "https://github.com/gitleaks/gitleaks/archive/refs/tags/v${GITLEAKS_VERSION}.tar.gz"; \
    echo "${GITLEAKS_SHA256}  /tmp/gitleaks.tar.gz" | sha256sum -c -; \
    mkdir /src; \
    tar -xzf /tmp/gitleaks.tar.gz -C /src --strip-components=1; \
    cd /src; \
    # v8.30.1 pins modules with fixed advisories: x/text v0.22.0 (GO-2026-5970, fixed in v0.39.0),
    # brotli (GO-2026-6888, fixed in v1.2.7), compress v1.17.11 (GO-2026-6865, fixed in v1.20.2);
    # raised here, each checked against sum.golang.org
    GOTOOLCHAIN=local go get golang.org/x/text@v0.39.0 github.com/andybalholm/brotli@v1.2.7 \
        github.com/klauspost/compress@v1.20.2; \
    CGO_ENABLED=0 GOTOOLCHAIN=local GOFLAGS=-mod=readonly go build -trimpath \
        -ldflags "-s -w -X=github.com/zricethezav/gitleaks/v8/version.Version=${GITLEAKS_VERSION}" \
        -o /gitleaks .; \
    test "$(/gitleaks version)" = "${GITLEAKS_VERSION}"

FROM scratch AS gitleaks-bin
COPY --from=gitleaks-build /gitleaks /gitleaks

# .NET SDK on Ubuntu 24.04 (noble); global.json names the minimum (latestFeature roll-forward)
FROM mcr.microsoft.com/dotnet/sdk:10.0.404-noble@sha256:fe0fd4e93bd2fbb47c883b7a6369e5c8d3db8bf01cdc5981f6ee35fbdf3ff4fb

ARG TARGETARCH
# apt itself, fixed for CVE-2026-109518; the base image still ships 2.8.3 (2026-11-06)
ARG APT_VERSION=2.8.4ubuntu0.1
ARG JUST_VERSION=1.59.0
ARG JUST_SHA256_AMD64=409044617b6f06517c1035e55ca38c8c856ff8b650e41bc2aa1ef82d5903a23e
ARG JUST_SHA256_ARM64=23871b1702bfd550fb5f901778fd4290ecf2ee034a45a889fbe96d89b188086d

RUN set -eu; \
    apt-get update -qq; \
    apt-get install -y -qq --no-install-recommends "apt=${APT_VERSION}" "libapt-pkg6.0t64=${APT_VERSION}"; \
    rm -rf /var/lib/apt/lists/*; \
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
