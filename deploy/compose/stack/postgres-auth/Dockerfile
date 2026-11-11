# postgres-auth's image (docs/architecture/compose-base.md, "postgres-auth"): the official Postgres
# image, Alpine variant, with nothing changed but zlib. The Debian variant carried 30 High and
# Critical advisories at the scan of 2026-11-08, most marked "won't fix"; this one only zlib's.
# The start script and pg_hba.conf are mounted from this folder at run time, not copied in.
FROM postgres:18.6-alpine3.24@sha256:123b17998ccaf47e53b502e8c6a1e79afd8f579c6f3eec83c563da026f957395

# zlib fixed for CVE-2026-4738 (Critical, 2026-11-08); Alpine 3.24.4 still ships 1.3.2. Drop this
# line once the base image carries 1.4 or newer (as in step-ca/Dockerfile).
ARG ZLIB_VERSION=1.4-r0
# krb5-libs (9 High) and libxslt (3 High) fixed in Alpine, not yet in the base image (scan of
# 2026-11-11); drop these once the base carries them
ARG KRB5_VERSION=1.22.3-r0
ARG LIBXSLT_VERSION=1.1.43-r4
RUN apk add --no-cache "zlib=${ZLIB_VERSION}" "krb5-libs=${KRB5_VERSION}" "libxslt=${LIBXSLT_VERSION}"
