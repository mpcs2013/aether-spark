# postgres-data's image (docs/architecture/data-plane.md, decision 6): the research data's own
# server, built like postgres-auth's (the official Postgres image, Alpine variant, the same pin and
# package fixes) with the pgvector extension compiled in from its tagged source. The extension is
# built in now and created in the database only when the embeddings come (P5a). The start script,
# migrations and pg_hba.conf are mounted from this folder at run time, not copied in.
FROM postgres:18.6-alpine3.24@sha256:123b17998ccaf47e53b502e8c6a1e79afd8f579c6f3eec83c563da026f957395 AS build
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

FROM postgres:18.6-alpine3.24@sha256:123b17998ccaf47e53b502e8c6a1e79afd8f579c6f3eec83c563da026f957395
# The same fixes as postgres-auth/Dockerfile (zlib, krb5-libs, libxslt); drop them together with
# that file's once the base image carries them
ARG ZLIB_VERSION=1.4-r0
ARG KRB5_VERSION=1.22.3-r0
ARG LIBXSLT_VERSION=1.1.43-r4
RUN apk add --no-cache "zlib=${ZLIB_VERSION}" "krb5-libs=${KRB5_VERSION}" "libxslt=${LIBXSLT_VERSION}"
COPY --from=build /out/usr/local/ /usr/local/
