#!/usr/bin/env bash
# The Spark host rules (docs/architecture/spark-host.md, "Tests") in a lab: one privileged container
# with no network of its own stands in for the Spark and builds a small LAN out of network
# namespaces inside itself, so default-deny never touches the machine that runs the test. Runs on
# the desktop (Docker Desktop) and in CI; needs Docker, and internet access for the image build only.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1
# Git Bash on Windows: container paths stay as written
export MSYS_NO_PATHCONV=1

name=aetherspark-host-lab
image=aetherspark/host-lab:local
docker build -q -t "$image" tests/egress/lab >/dev/null || { echo "lab image build failed"; exit 1; }
docker rm -f "$name" >/dev/null 2>&1
# AS_LAB_KEEP=1 keeps the lab for a look inside afterwards: docker exec -it aetherspark-host-lab bash
if [ "${AS_LAB_KEEP:-}" = 1 ]; then trap 'echo "lab kept: docker rm -f $name when done"' EXIT
else trap 'docker rm -f "$name" >/dev/null 2>&1' EXIT; fi
docker run -d --privileged --network none --name "$name" "$image" >/dev/null || exit 1
docker exec "$name" mkdir -p /repo/deploy /repo/tests/egress
docker cp deploy/host "$name:/repo/deploy/host" >/dev/null || exit 1
docker cp tests/egress/host-lab-tests.sh "$name:/repo/tests/egress/host-lab-tests.sh" >/dev/null || exit 1
docker exec "$name" bash /repo/tests/egress/host-lab-tests.sh
