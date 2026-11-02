# The Grype image deploy/images/scan.sh runs (ADR-0005 amendment 3). A Dockerfile, so that Dependabot
# proposes its updates; nothing builds it.
FROM ghcr.io/anchore/grype:v0.121.1@sha256:6b06708b036f74b2cd70e94b706b4586af96d1932e6ac46255940b2e39dd44a9
