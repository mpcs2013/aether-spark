# postgres-auth's image (docs/architecture/compose-base.md, "postgres-auth"): the official Postgres
# image, Alpine variant, with nothing changed but OpenSSL. The Debian variant carried 30 High and
# Critical advisories at the scan of 2026-11-08, most marked "won't fix"; this one only zlib's.
# The start script and pg_hba.conf are mounted from this folder at run time, not copied in.
# 18.7 (2026-11-18) fixes 38 High findings in 18.6 and carries zlib 1.4-r0, krb5-libs 1.22.3-r0
# and libxslt 1.1.43-r4 itself, so their pins are gone.
FROM postgres:18.7-alpine3.24@sha256:ff64c9e3bdf8e2120107d3559de8db314e8305f7e38c5105903c6295876738ff

# OpenSSL fixed for CVE-2026-88823, -95691 and -95692 (High) in Alpine, not yet in the base image
# (scan of 2026-11-19); drop this once the base carries 3.5.9-r1 or newer
ARG OPENSSL_VERSION=3.5.9-r1
# libxml2 fixed for CVE-2026-86144 (High) in Alpine, not yet in the base image (scan of
# 2026-11-21, the owner's choice that day). Raised to 2.13.9-r5 on 2026-11-26 (the owner's choice):
# Alpine keeps only the newest build, and r4 and r5 added only security fixes, so r3 could no
# longer be installed. Drop this once the base carries 2.13.9-r5 or newer
ARG LIBXML2_VERSION=2.13.9-r5
RUN apk add --no-cache "libssl3=${OPENSSL_VERSION}" "libcrypto3=${OPENSSL_VERSION}" "libxml2=${LIBXML2_VERSION}"
