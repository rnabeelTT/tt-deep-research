#!/usr/bin/env bash
# Pinned AI-Q clone with our patches applied: the source for the aiq and ui images.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -d external/aiq ]; then
  git clone --depth 1 --branch v2.2.1 https://github.com/NVIDIA-AI-Blueprints/deep-researcher-agent external/aiq
  (cd external/aiq && git apply ../../patches/aiq/*.patch)
fi
# the images copy this tree as-is: a checkout made under a restrictive umask (007) leaves files their user can't read
chmod -R a+rX external/aiq --quiet || true
echo "external/aiq ready ($(git -C external/aiq describe --tags))"
