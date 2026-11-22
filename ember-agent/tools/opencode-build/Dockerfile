# From repo root, stage only verified upstream release archives in .local/build-image.
# Release: https://github.com/anomalyco/opencode/releases/tag/v2.3.2
# amd64 asset: opencode-linux-x64-baseline.tar.gz; arm64 asset: opencode-linux-arm64.tar.gz.
# SHA-256 values below are from upstream release asset metadata.
#
# Explicit setup (amd64 shown; substitute arm64 platform, asset and tag for arm64):
# docker pull --platform linux/amd64 docker.io/library/python:3.12-bookworm@sha256:4209fae09a3e20afd863e528bfddc235da5fd5977983e1996cd54cc9a72e6267
# docker buildx build --load --provenance=false --platform linux/amd64 --build-arg OPENCODE_ARCHIVE=opencode-linux-x64-baseline.tar.gz -t ember-opencode-build:2.3.2-amd64 -f ember-agent/tools/opencode-build/Dockerfile .local/build-image
# docker image inspect --format '{{.Id}}' ember-opencode-build:2.3.2-amd64
# Pin that local sha256: ID (not the tag) at runtime; never pull at runtime.

# Full Bookworm image already supplies glibc, bash, git and coreutils; no package fetches.
FROM docker.io/library/python:3.12-bookworm@sha256:4209fae09a3e20afd863e528bfddc235da5fd5977983e1996cd54cc9a72e6267

ARG TARGETARCH
ARG OPENCODE_ARCHIVE
ARG SOURCE_DATE_EPOCH=0

RUN --network=none --mount=type=bind,source=${OPENCODE_ARCHIVE},target=/opencode.tar.gz \
    --mount=type=tmpfs,target=/tmp \
    set -eu; \
    case "$TARGETARCH" in \
        amd64) sha=563b5d663637226b6e3bb4bb6803281bc39b0d2cd88db06d674b6420aab009b0 ;; \
        arm64) sha=9d10185acdb72336e086e699ca363a6065f410fd3c1caed0dc8046e95ac06ccb ;; \
        *) echo "Unsupported target architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    printf '%s  /opencode.tar.gz\n' "$sha" | sha256sum --check --strict; \
    tar -xOzf /opencode.tar.gz opencode > /usr/local/bin/opencode; \
    chmod 0755 /usr/local/bin/opencode; \
    touch -d "@$SOURCE_DATE_EPOCH" /usr/local/bin/opencode; \
    version="$(HOME=/tmp /usr/local/bin/opencode --version)"; \
    printf '%s\n' "$version"; \
    test "$version" = 'opencode v2.3.2'; \
    /usr/local/bin/python3 -c 'import sys, http.server, sqlite3, ssl; assert sys.version_info[:2] == (3, 12)'; \
    /bin/sleep 0; \
    /bin/bash --version > /dev/null; \
    git --version > /dev/null
