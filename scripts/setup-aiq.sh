#!/usr/bin/env bash
# Pinned AI-Q clone with our patches applied: the source for the aiq and ui images.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -d external/aiq ]; then
  git clone --depth 1 --branch v2.2.1 https://github.com/NVIDIA-AI-Blueprints/deep-researcher-agent external/aiq
  (cd external/aiq && git apply ../../patches/aiq/*.patch)
fi
echo "external/aiq ready ($(git -C external/aiq describe --tags))"
