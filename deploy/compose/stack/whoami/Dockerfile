# Gate G3's test page (docs/architecture/compose-base.md, "Gate G3's test relying party"): Traefik's
# whoami, which echoes the request, so it shows the user forward-auth signed in. Built here from its
# tagged source with a current Go, as step-ca is: the release image (v1.12.0, the latest, 2026-07-30)
# carries Go 1.26.5 and old modules with 1 Critical and 16 High fixed findings (scan of 2026-11-09).
# The static binary goes onto the stack's pinned Alpine (zlib fixed) because it reads its TLS
# certificate only at start, and deploy/compose/stack/cert-watch/cert-watch.sh, which restarts it
# after each renewal, needs a shell. Builds for amd64 and arm64.
FROM golang:1.27.3-alpine3.24@sha256:d200d2dd76b03a99b52651fd1325aff7dcd35422c35c01bac56571b0507f3deb AS build
ARG WHOAMI_VERSION=1.12.0
ARG WHOAMI_SHA256=ed07fcf7005cef51d74e42bc34bfec9e76965fe1efb972d6d28e8ce84ed11e8c
# Modules with fixed advisories, raised to the scan's fix versions, only upwards; the build fails if
# one stays below its target
ARG RAISE="golang.org/x/net@v0.62.0 google.golang.org/grpc@v1.83.2"
ENV CGO_ENABLED=0 GOTOOLCHAIN=local
RUN set -eu; \
    at_least() { [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -1)" = "$1" ]; }; \
    wget -qO /tmp/src.tar.gz "https://github.com/traefik/whoami/archive/refs/tags/v$WHOAMI_VERSION.tar.gz"; \
    echo "$WHOAMI_SHA256  /tmp/src.tar.gz" | sha256sum -c -; \
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
    GOFLAGS=-mod=mod go build -trimpath -ldflags "-s -w" -o /out/whoami .; \
    /out/whoami --help 2>&1 | head -1

FROM alpine:3.24.4@sha256:93b41ef40b1677fce8fcc64ff4af09daddfacf5040afde618bc46c1e2d1cc7d4
# zlib fixed for CVE-2026-4738 (Critical, 2026-11-08); drop once the base carries 1.4 or newer
ARG ZLIB_VERSION=1.4-r0
RUN apk add --no-cache "zlib=${ZLIB_VERSION}"
COPY --from=build /out/whoami /usr/local/bin/whoami
