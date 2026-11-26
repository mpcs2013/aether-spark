# postgres-data's image (docs/architecture/data-plane.md, decision 6): the research data's own
# server, built like postgres-auth's (the official Postgres image, Alpine variant, the same pin and
# package fixes) with the pgvector extension compiled in from its tagged source. The extension is
# built in now and created in the database only when the embeddings come (P5a). The start script,
# migrations and pg_hba.conf are mounted from this folder at run time, not copied in.
FROM postgres:18.7-alpine3.24@sha256:ff64c9e3bdf8e2120107d3559de8db314e8305f7e38c5105903c6295876738ff AS build
ARG PGVECTOR_VERSION=0.8.7
ARG PGVECTOR_SHA256=cac0b10c360f05b2d521200105ba3697e773d4cd3731f5a915a7e37ebe0bea85
# OPTFLAGS="": no -march=native, so the image runs on any CPU of its architecture; with_llvm=no: no
# JIT bitcode, so no clang or llvm in the build
RUN set -eu; \
    apk add --no-cache --virtual .build build-base wget; \
    wget -qO /tmp/pgvector.tar.gz "https://github.com/pgvector/pgvector/archive/refs/tags/v${PGVECTOR_VERSION}.tar.gz"; \
    echo "${PGVECTOR_SHA256}  /tmp/pgvector.tar.gz" | sha256sum -c -; \
    mkdir /src; \
    tar -xzf /tmp/pgvector.tar.gz -C /src --strip-components=1; \
    cd /src; \
    make OPTFLAGS="" with_llvm=no; \
    make OPTFLAGS="" with_llvm=no install DESTDIR=/out; \
    test -f /out/usr/local/lib/postgresql/vector.so

FROM postgres:18.7-alpine3.24@sha256:ff64c9e3bdf8e2120107d3559de8db314e8305f7e38c5105903c6295876738ff
# The same fix as postgres-auth/Dockerfile (OpenSSL); drop it together with that file's once the
# base image carries 3.5.9-r1 or newer
ARG OPENSSL_VERSION=3.5.9-r1
# libxml2 fixed for CVE-2026-86144 (High), as in postgres-auth/Dockerfile (the owner's choice of
# 2026-11-21; 2.13.9-r5 since 2026-11-26, as there); drop it together with that file's
ARG LIBXML2_VERSION=2.13.9-r5
RUN apk add --no-cache "libssl3=${OPENSSL_VERSION}" "libcrypto3=${OPENSSL_VERSION}" "libxml2=${LIBXML2_VERSION}"
COPY --from=build /out/usr/local/ /usr/local/
