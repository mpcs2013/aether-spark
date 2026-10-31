#!/usr/bin/env bash
# Image scan (ADR-0005 amendment 3, #31): builds every image the repository builds, scans it and
# every digest-pinned image its Compose files run with Grype, for one architecture (the runner's
# own), then applies scan_policy.py. Grype runs from the digest-pinned image in grype.Dockerfile;
# its database is downloaded once per run into a Docker volume. Built images reach Grype as a
# saved archive, so Grype gets no access to the Docker socket.
#
#   bash deploy/images/scan.sh amd64|arm64
set -euo pipefail

arch="${1:?usage: scan.sh amd64|arm64}"
case "$arch" in amd64|arm64) ;; *) echo "scan.sh: unknown architecture $arch" >&2; exit 2 ;; esac
host="$(docker version --format '{{.Server.Arch}}')"
[ "$host" = "$arch" ] || { echo "scan.sh: builds natively only: Docker runs on $host, not $arch" >&2; exit 2; }

here="$(cd "$(dirname "$0")" && pwd)"
grype_image="$(sed -n 's/^FROM \([^ ]*@sha256:[0-9a-f]\{64\}\)$/\1/p' "$here/grype.Dockerfile")"
[ -n "$grype_image" ] || { echo "scan.sh: no digest-pinned FROM line in grype.Dockerfile" >&2; exit 2; }
out="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/image-scan-$arch"
rm -rf "$out"
mkdir -p "$out"
volume="aetherspark-grype-db"
docker volume create "$volume" >/dev/null

grype() {
    docker run --rm -v "$volume:/db" -e GRYPE_DB_CACHE_DIR=/db -v "$out:/in:ro" "$grype_image" \
        "$@" --platform "linux/$arch" -o json -q
}

args=()
i=0
targets="$(python3 -I "$here/scan_policy.py" targets)"
while read -r kind name source dockerfile; do
    dockerfile="${dockerfile%$'\r'}"  # a Windows Python prints CRLF (local runs only)
    i=$((i + 1))
    case "$kind" in
        build)
            echo "build and scan $name ($dockerfile)"
            docker build -q -t "aetherspark-scan/$i:$arch" -f "$dockerfile" "$source" >/dev/null
            docker save "aetherspark-scan/$i:$arch" -o "$out/$i.tar"
            grype "docker-archive:/in/$i.tar" > "$out/$i.json"
            rm -f "$out/$i.tar"
            ;;
        pull)
            echo "scan $name ($source)"
            grype "registry:$source" > "$out/$i.json"
            ;;
        *) echo "scan.sh: unknown target kind '$kind'" >&2; exit 2 ;;
    esac
    args+=(--result "$name=$out/$i.json")
done <<< "$targets"

python3 -I "$here/scan_policy.py" evaluate --arch "$arch" --exceptions "$here/exceptions.json" "${args[@]}"
