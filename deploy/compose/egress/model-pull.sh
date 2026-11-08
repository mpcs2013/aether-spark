#!/bin/sh
# One-shot model pull (ADR-0006 "Model files"; egress design, ops egress): one model at its pinned
# revision, through the ops proxy, into /models/<repo>/<revision>/. Every file is checked against the
# SHA-256 pinned in deploy/models/<name>.sha256; the folder gets its final name only when all match.
# An interrupted run resumes where it stopped. Runs in the pinned curl image (egress compose,
# service model-pull); HTTPS_PROXY points at the ops proxy.
#
#   model-pull.sh <name>     (deploy/models/<name>.source and <name>.sha256)
set -eu
name="${1:?usage: model-pull.sh <name of a model in deploy/models>}"
case "$name" in *[!a-z0-9.-]*) echo "model-pull: bad model name $name" >&2; exit 2 ;; esac
src="/manifests/$name.source"
sums="/manifests/$name.sha256"
repo="$(sed -n 's/^repo=//p' "$src")"
rev="$(sed -n 's/^revision=//p' "$src")"
case "$repo" in */*/* | *..* | "" | *[!A-Za-z0-9._/-]*) echo "model-pull: bad repo '$repo'" >&2; exit 2 ;; esac
if [ "${#rev}" -ne 40 ] || [ -n "$(printf '%s' "$rev" | tr -d '0-9a-f')" ]; then
    echo "model-pull: the revision must be a full commit hash" >&2
    exit 2
fi
dest="/models/$repo/$rev"
part="$dest.partial"
if [ -d "$dest" ]; then
    echo "model-pull: $repo at $rev is already in place"
    exit 0
fi
mkdir -p "$part"
count=0
while read -r sum file; do
    [ -n "$file" ] || continue
    case "$file" in */* | .. | . | -*) echo "model-pull: bad file name $file" >&2; exit 2 ;; esac
    count=$((count + 1))
    if [ -f "$part/$file" ] && echo "$sum  $part/$file" | sha256sum -c -s; then
        echo "model-pull: $file already verified"
        continue
    fi
    echo "model-pull: $file"
    curl -fL --proto '=https' --proto-redir '=https' --retry 5 --retry-all-errors -C - -sS \
        -o "$part/$file" "https://huggingface.co/$repo/resolve/$rev/$file"
    if ! echo "$sum  $part/$file" | sha256sum -c -s; then
        rm -f "$part/$file"
        echo "model-pull: $file does not match its pinned SHA-256; removed, run again" >&2
        exit 1
    fi
done < "$sums"
mv "$part" "$dest"
echo "model-pull: $repo at $rev: $count files verified, in $dest"
