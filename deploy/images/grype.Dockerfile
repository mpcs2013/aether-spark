# The Grype image deploy/images/scan.sh runs (ADR-0005 amendment 3). A Dockerfile, so that Dependabot
# proposes its updates; nothing builds it.
FROM ghcr.io/anchore/grype:v0.121.3@sha256:13113eeceab017260370cf540e1d25442c59665fd07d1b09b66d63bb0c282887
