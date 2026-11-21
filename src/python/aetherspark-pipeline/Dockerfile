# The pipeline worker's image (docs/architecture/data-plane.md, section 4), built from the repository
# root. Python's Alpine image (the owner's choice of 2026-11-21), the one the stack tests already pin
# (tests/egress/compose.layer3.yaml; tests/python/test_aspire_run.py keeps the AppHost on it too).
# Third-party packages only as prebuilt wheels with the hashes of uv.lock (requirements.txt, kept
# equal to the lock by tests/python/test_pipeline_requirements.py); the two workspace packages are
# plain Python, copied in, with no build step.
FROM python:3.12.16-alpine3.24@sha256:e064671ca452259bab50e8e2c7bd896e39e9a343bb06ca25234adf6fe18d0fc7 AS deps
COPY src/python/aetherspark-pipeline/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --disable-pip-version-check --root-user-action=ignore \
        --require-hashes --only-binary=:all: --no-deps --target /deps -r /tmp/requirements.txt

FROM python:3.12.16-alpine3.24@sha256:e064671ca452259bab50e8e2c7bd896e39e9a343bb06ca25234adf6fe18d0fc7
# The same OpenSSL fix as postgres-auth's (CVE-2026-88823, -95691, -95692 in 3.5.9-r0); drop it
# together with that file's once the base carries 3.5.9-r1 or newer. And the base's krb5-libs
# (nine High findings) and zlib (CVE-2026-4738, Critical) raised to Alpine's fixed releases, as
# postgres-auth's base and step-ca carry them (the scan of 2026-11-21); drop each once the base
# carries it
ARG OPENSSL_VERSION=3.5.9-r1
ARG KRB5_VERSION=1.22.3-r0
ARG ZLIB_VERSION=1.4-r0
# psycopg's libpq from Alpine, linked to the OpenSSL above, so the database TLS is scanned and
# pinned like the rest (psycopg-binary bundles its own libpq and an older OpenSSL; the owner's
# choice of 2026-11-21); the release of the data Postgres itself
ARG LIBPQ_VERSION=18.7-r0
RUN apk add --no-cache "libssl3=${OPENSSL_VERSION}" "libcrypto3=${OPENSSL_VERSION}" \
        "krb5-libs=${KRB5_VERSION}" "zlib=${ZLIB_VERSION}" "libpq=${LIBPQ_VERSION}" \
    # The worker never installs anything: no pip in the running image
    && python -m pip uninstall --yes --root-user-action=ignore pip \
    && rm -rf /root/.cache
COPY --from=deps /deps/ /usr/local/lib/python3.12/site-packages/
COPY src/python/aetherspark-common/src/aetherspark_common/ /usr/local/lib/python3.12/site-packages/aetherspark_common/
COPY src/python/aetherspark-pipeline/src/aetherspark_pipeline/ /usr/local/lib/python3.12/site-packages/aetherspark_pipeline/
# No bytecode written at run time (the root filesystem is read-only), output unbuffered for the log
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# -I: no PYTHON* variables, no user site, no current folder on the path
ENTRYPOINT ["python", "-I", "-m", "aetherspark_pipeline"]
