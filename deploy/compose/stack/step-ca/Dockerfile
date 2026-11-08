# step-ca and the step CLI from their tagged sources, built with a current Go on a current Alpine
# (compose-base.md, "Certificates", as built). The upstream image (step-ca 0.30.2, the latest
# release, 2026-03-23) carries Go 1.26.1, old modules and Alpine packages with 223 fixed High and
# Critical findings (#31's scan, 2026-11-06), and gives the step-ca binary a file capability that
# the kernel refuses to run with cap_drop: [ALL] and no-new-privileges. This image has neither: two
# static binaries on Alpine, no curl, no jq, no plugin. Source archives are checked by SHA-256,
# modules by go.sum and sum.golang.org. Builds for amd64 and arm64.
FROM golang:1.27.3-alpine3.24@sha256:d200d2dd76b03a99b52651fd1325aff7dcd35422c35c01bac56571b0507f3deb AS build
ARG STEP_CA_VERSION=0.30.2
ARG STEP_CA_SHA256=327391f301b79818f2418302516b4f6b41bdf08e38edc9011cc7f1b351e19452
ARG STEP_CLI_VERSION=0.31.0
ARG STEP_CLI_SHA256=cc79207697f733eade1017a2be0da067362bf3bc0c05a6c68eeb7bbd288713d3
# Modules with fixed advisories, raised to the scan's fix versions. Only upwards, and only where the
# module is already a dependency; the build fails if one stays below its target. Two lists: the
# CLI's own libraries do not compile against every raise step-ca takes (tried 2026-11-06).
ARG RAISE_CA="golang.org/x/net@v0.62.0 golang.org/x/crypto@v0.52.0 golang.org/x/text@v0.41.0 google.golang.org/grpc@v1.83.2 github.com/go-jose/go-jose/v3@v3.0.5 github.com/go-jose/go-jose/v4@v4.1.6 github.com/jackc/pgx/v5@v5.9.0 github.com/klauspost/compress@v1.20.2 go.opentelemetry.io/otel@v1.41.0"
ARG RAISE_CLI="golang.org/x/net@v0.62.0 github.com/klauspost/compress@v1.20.2 github.com/go-jose/go-jose/v4@v4.1.6"
ENV CGO_ENABLED=0 GOTOOLCHAIN=local
RUN set -eu; \
    at_least() { [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -1)" = "$1" ]; }; \
    fetch() { \
        wget -qO /tmp/src.tar.gz "https://github.com/smallstep/$1/archive/refs/tags/v$2.tar.gz"; \
        echo "$3  /tmp/src.tar.gz" | sha256sum -c -; \
        rm -rf /src && mkdir /src && tar -xzf /tmp/src.tar.gz -C /src --strip-components=1; \
        cd /src; \
        for m in $4; do \
            cur="$(go list -m -f '{{.Version}}' "${m%@*}" 2>/dev/null)" || continue; \
            at_least "$cur" "${m#*@}" || go get "$m"; \
        done; \
        for m in $4; do \
            cur="$(go list -m -f '{{.Version}}' "${m%@*}" 2>/dev/null)" || continue; \
            at_least "$cur" "${m#*@}" || { echo "${m%@*} is $cur, below ${m#*@}" >&2; exit 1; }; \
        done; \
    }; \
    fetch certificates "$STEP_CA_VERSION" "$STEP_CA_SHA256" "$RAISE_CA"; \
    GOFLAGS=-mod=mod go build -trimpath -ldflags "-s -w -X main.Version=$STEP_CA_VERSION" \
        -o /out/step-ca ./cmd/step-ca; \
    fetch cli "$STEP_CLI_VERSION" "$STEP_CLI_SHA256" "$RAISE_CLI"; \
    GOFLAGS=-mod=mod go build -trimpath -ldflags "-s -w -X main.Version=$STEP_CLI_VERSION" \
        -o /out/step ./cmd/step; \
    /out/step-ca version; /out/step version

FROM alpine:3.24.4@sha256:93b41ef40b1677fce8fcc64ff4af09daddfacf5040afde618bc46c1e2d1cc7d4
# zlib fixed for CVE-2026-4738 (Critical, 2026-11-08); Alpine 3.24.4 still ships 1.3.2. Drop this
# line once the base digest carries 1.4 or later. The services run as the stack user (`user:` in
# Compose); the user created here owns nothing they need.
ARG ZLIB_VERSION=1.4-r0
RUN apk add --no-cache "zlib=${ZLIB_VERSION}" && adduser -D -u 1000 step
COPY --from=build /out/step-ca /out/step /usr/local/bin/
USER step
ENV STEPPATH=/home/step
WORKDIR /home/step
