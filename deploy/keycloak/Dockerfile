# Keycloak, as AetherSpark runs it (docs/architecture/compose-base.md, "Keycloak exposure"). The base
# is the index digest of Ostium's latest Keycloak release notice (exchange D-008, notice O-047:
# 26.8.2); it moves only with a new notice (ADR-0009 amendment 3), so Dependabot ignores it. The
# build step fixes the options Keycloak reads only at build time, so the container starts with
# `start --optimized`, never in development mode.
FROM quay.io/keycloak/keycloak:26.8.2@sha256:7e66a96e4739239070aeaf72e4c5decf0f7f682cfc725b9f7ea2b336eaba38b3 AS build
# Postgres only; health checks on the management port (never routed); no metrics until WP3.2; one
# node, no cluster discovery
ENV KC_DB=postgres \
    KC_HEALTH_ENABLED=true \
    KC_METRICS_ENABLED=false \
    KC_CACHE=local
RUN /opt/keycloak/bin/kc.sh build

FROM quay.io/keycloak/keycloak:26.8.2@sha256:7e66a96e4739239070aeaf72e4c5decf0f7f682cfc725b9f7ea2b336eaba38b3
COPY --from=build /opt/keycloak/ /opt/keycloak/
ENTRYPOINT ["/opt/keycloak/bin/kc.sh"]
