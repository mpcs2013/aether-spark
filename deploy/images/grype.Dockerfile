# The Grype image deploy/images/scan.sh runs (ADR-0005 amendment 3). A Dockerfile, so that Dependabot
# proposes its updates; nothing builds it.
FROM ghcr.io/anchore/grype:v0.121.0@sha256:621d56d1d68106b787022c81546f7e0a36202a25d1239f0c20195fab8c16f7ea
