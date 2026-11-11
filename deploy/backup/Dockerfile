# The backup job's restic (docs/architecture/backup-job.md). Built here from its tagged source with a
# current Go, as whoami and step-ca are: the release image (restic/restic 0.19.1, the latest) carries
# Go 1.26 and old Alpine libraries with 10 Critical and 60 High fixed findings (scan of 2026-11-11).
# The static binary goes onto the stack's pinned Alpine (zlib fixed), whose busybox gives the job its
# shell, `cp`, `sed` and `nc`. Builds for amd64 and arm64.
FROM golang:1.27.3-alpine3.24@sha256:d200d2dd76b03a99b52651fd1325aff7dcd35422c35c01bac56571b0507f3deb AS build
ARG RESTIC_VERSION=0.19.1
ARG RESTIC_SHA256=bb9b1a19040744d26d8a79be029d4e6b189c45ccc9d8831d7fe367d3c33df725
# Modules with fixed advisories, raised to the scan's fix versions, only upwards; the build fails if
# one stays below its target
ARG RAISE="golang.org/x/net@v0.62.0 golang.org/x/text@v0.39.0 github.com/klauspost/compress@v1.20.2 google.golang.org/grpc@v1.83.2"
ENV CGO_ENABLED=0 GOTOOLCHAIN=local
RUN set -eu; \
    at_least() { [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -1)" = "$1" ]; }; \
    wget -qO /tmp/src.tar.gz "https://github.com/restic/restic/archive/refs/tags/v$RESTIC_VERSION.tar.gz"; \
    echo "$RESTIC_SHA256  /tmp/src.tar.gz" | sha256sum -c -; \
    mkdir /src && tar -xzf /tmp/src.tar.gz -C /src --strip-components=1; \
    cd /src; \
    for m in $RAISE; do \
        cur="$(go list -m -f '{{.Version}}' "${m%@*}")"; \
        at_least "$cur" "${m#*@}" || go get "$m"; \
    done; \
    for m in $RAISE; do \
        cur="$(go list -m -f '{{.Version}}' "${m%@*}")"; \
        at_least "$cur" "${m#*@}" || { echo "${m%@*} is $cur, below ${m#*@}" >&2; exit 1; }; \
    done; \
    GOFLAGS=-mod=mod go build -trimpath -ldflags "-s -w -X main.version=$RESTIC_VERSION" -o /out/restic ./cmd/restic; \
    /out/restic version

FROM alpine:3.24.4@sha256:93b41ef40b1677fce8fcc64ff4af09daddfacf5040afde618bc46c1e2d1cc7d4
# zlib fixed for CVE-2026-4738 (Critical, 2026-11-08); drop once the base carries 1.4 or newer
ARG ZLIB_VERSION=1.4-r0
RUN apk add --no-cache "zlib=${ZLIB_VERSION}"
COPY --from=build /out/restic /usr/bin/restic
ENTRYPOINT ["/usr/bin/restic"]
