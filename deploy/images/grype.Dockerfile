# The Grype image deploy/images/scan.sh runs (ADR-0005 amendment 3). A Dockerfile, so that Dependabot
# proposes its updates; nothing builds it.
FROM ghcr.io/anchore/grype:v0.122.1@sha256:661583704a253c0448ed63998b5ea2cd9d5a76ee71eedc4aed439b8ce3e93a1c
