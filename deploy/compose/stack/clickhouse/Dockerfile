# ClickHouse's image (docs/architecture/data-plane.md, decision 6): the upstream image's Alpine
# variant of the 26.8 LTS line, pinned by its index digest (amd64 and arm64), with nothing changed
# but the packages below. Chosen by the scan of 2026-11-19: the Debian-based distroless variant
# carried five libc6 findings, three with no fix; this one only zlib's and OpenSSL's, fixed here.
# The start script, the server configuration and the migrations are mounted from this folder at
# run time, not copied in; the image's own configuration in /etc/clickhouse-server is not used.
FROM clickhouse/clickhouse-server:26.8.40.7-alpine@sha256:0b353134e425a8eefbca83bf6ac04afcc3e7b5309ab19be4e63174320a6f30e1

# zlib (CVE-2026-4738, Critical) and OpenSSL (four High) fixed in Alpine, not yet in the base image
# (scan of 2026-11-19), as in postgres-auth/Dockerfile; drop them once the base carries them
ARG ZLIB_VERSION=1.4-r0
ARG OPENSSL_VERSION=3.5.9-r1
RUN apk add --no-cache "zlib=${ZLIB_VERSION}" "libssl3=${OPENSSL_VERSION}" "libcrypto3=${OPENSSL_VERSION}"
