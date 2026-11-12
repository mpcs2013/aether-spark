# A stand-in for the NAS's backup server, for the stack test only (tests/stack/run.sh and
# layer3.sh; docs/architecture/backup-job.md, "CI's stand-in"): rest-server, as the home
# infrastructure runs it. Built here from its tagged source with a current Go: the release image
# (restic/rest-server 0.14.0, the latest) carries Go 1.24 and old Alpine libraries with 24 Critical
# and 141 High fixed findings (scan of 2026-11-11). bcrypt-login (bcrypt-login.go, built in the same
# module) makes the test's login line, so the image needs no apache2-utils: an exact pin of an Alpine
# package breaks as soon as Alpine replaces it (2026-11-12). Builds for amd64 and arm64.
FROM golang:1.27.3-alpine3.24@sha256:d200d2dd76b03a99b52651fd1325aff7dcd35422c35c01bac56571b0507f3deb AS build
ARG REST_SERVER_VERSION=0.14.0
ARG REST_SERVER_SHA256=8b3f91d561819ba9bce454505958fcca6d61ecd12e10086954ebfc92ba163ba4
ENV CGO_ENABLED=0 GOTOOLCHAIN=local
COPY bcrypt-login.go /tmp/bcrypt-login.go
RUN set -eu; \
    wget -qO /tmp/src.tar.gz "https://github.com/restic/rest-server/archive/refs/tags/v$REST_SERVER_VERSION.tar.gz"; \
    echo "$REST_SERVER_SHA256  /tmp/src.tar.gz" | sha256sum -c -; \
    mkdir /src && tar -xzf /tmp/src.tar.gz -C /src --strip-components=1; \
    cd /src; \
    mkdir -p cmd/bcrypt-login && cp /tmp/bcrypt-login.go cmd/bcrypt-login/main.go; \
    GOFLAGS=-mod=mod go build -trimpath -ldflags "-s -w" -o /out/rest-server ./cmd/rest-server; \
    GOFLAGS=-mod=mod go build -trimpath -ldflags "-s -w" -o /out/bcrypt-login ./cmd/bcrypt-login; \
    /out/rest-server --version

FROM alpine:3.24.4@sha256:93b41ef40b1677fce8fcc64ff4af09daddfacf5040afde618bc46c1e2d1cc7d4
ARG ZLIB_VERSION=1.4-r0
RUN apk add --no-cache "zlib=${ZLIB_VERSION}"
COPY --from=build /out/rest-server /out/bcrypt-login /usr/bin/
ENTRYPOINT ["/usr/bin/rest-server"]
