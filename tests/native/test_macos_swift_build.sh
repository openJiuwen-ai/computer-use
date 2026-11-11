#!/usr/bin/env bash
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
set -euo pipefail

# Select the compiler with DEVELOPER_DIR. CLT 16.4 (Swift 6.1.2) reproduces
# the compatibility regression. Pass SwiftPM options to test other targets.
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
exec xcrun swift build --package-path "$repo_root/os_adapter/macos" --configuration release "$@"
