#!/usr/bin/env bash
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
output=$(mktemp -d)
trap 'rm -rf "$output"' EXIT
sources="$repo_root/os_adapter/macos/Sources"
xcrun swiftc -O -o "$output/observation-line" \
    "$repo_root/tests/native/macos/NativeObservationLineParity.swift" \
    "$sources"/Read/{NativeCollectedObservation,NativeObservationEncoding,NativeReadMetadataPool}.swift \
    "$sources"/Runtime/{NativeObservationJSON,NativeObservationLine}.swift
"$output/observation-line"
