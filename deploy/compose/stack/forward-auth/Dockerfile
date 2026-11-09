# forward-auth (docs/architecture/compose-base.md, "Gate G3's test relying party"): oauth2-proxy's
# own static binary on the stack's pinned Alpine (zlib fixed, as for step-ca), because the server
# reads its TLS certificate only at start and needs a shell for deploy/compose/stack/cert-watch/
# cert-watch.sh, which restarts it after each renewal. The official image is distroless (no shell).
FROM quay.io/oauth2-proxy/oauth2-proxy:v7.15.6@sha256:c3415e8660af553f5e18f5d90db050614c02493c0ac6260ac59442f6d9b12048 AS upstream

FROM alpine:3.24.4@sha256:93b41ef40b1677fce8fcc64ff4af09daddfacf5040afde618bc46c1e2d1cc7d4
# zlib fixed for CVE-2026-4738 (Critical, 2026-11-08); drop once the base carries 1.4 or newer
ARG ZLIB_VERSION=1.4-r0
RUN apk add --no-cache "zlib=${ZLIB_VERSION}"
COPY --from=upstream /bin/oauth2-proxy /usr/local/bin/oauth2-proxy
