# The Grype image deploy/images/scan.sh runs (ADR-0005 amendment 3). A Dockerfile, so that Dependabot
# proposes its updates; nothing builds it.
FROM ghcr.io/anchore/grype:v0.121.2@sha256:6a36234da13f9faa327306ac0b3dab3405cfa2b4eb20c8f904125a8707863913
