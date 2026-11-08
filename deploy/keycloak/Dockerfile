# Keycloak, as AetherSpark runs it (docs/architecture/compose-base.md, "Keycloak exposure"). The base
# is the index digest of Ostium's latest Keycloak release notice (exchange D-008, notice O-035:
# 26.8.1, build 26.8.1-2); it moves only with a new notice (ADR-0009 amendment 3), so Dependabot
# ignores it. The build step fixes the options Keycloak reads only at build time, so the container
# starts with `start --optimized`, never in development mode.
FROM quay.io/keycloak/keycloak:26.8.1@sha256:3f3c5f8e03c614774b4bd9173b88391fefa4153bfc010d8012d59ad825377332 AS build
# Postgres only; health checks on the management port (never routed); no metrics until WP3.2; one
# node, no cluster discovery
ENV KC_DB=postgres \
    KC_HEALTH_ENABLED=true \
    KC_METRICS_ENABLED=false \
    KC_CACHE=local
RUN /opt/keycloak/bin/kc.sh build

FROM quay.io/keycloak/keycloak:26.8.1@sha256:3f3c5f8e03c614774b4bd9173b88391fefa4153bfc010d8012d59ad825377332
COPY --from=build /opt/keycloak/ /opt/keycloak/
ENTRYPOINT ["/opt/keycloak/bin/kc.sh"]
