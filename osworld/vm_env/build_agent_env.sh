#!/bin/bash
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
#
# Runs inside an OSWorld-Verified VM (Ubuntu 22.04: system Python 3.10, AT-SPI 2.44), from ~/cu-build with
# src.tar.gz (this repo) beside it; build_agent_env.py uploads both and starts it. Builds this repo's Linux wheel,
# a Python 3.12 venv with the wheel's [agent] extra and PyGObject/pycairo built for 3.12 against the VM's GObject
# libraries, checks the agent starts, and packs ~/cu-build/out/cu-agent-env.tar.gz. Writes ~/cu-build/DONE (exit code).
#
# Why Python 3.12 for the driver: the SDK runs the Linux driver on the system Python only when it is 3.11 or newer
# (computer_use.paths.linux_python); Ubuntu 22.04's is 3.10, so the driver runs on the venv's Python, which then
# needs its own PyGObject. The runner sets COMPUTER_USE_PYTHON to it.
set -uo pipefail
B=$HOME/cu-build
finish() { echo "$1" > "$B/DONE"; exit "$1"; }
cd "$B" || finish 90
export DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/$(id -u)/bus}
sudo_() { echo password | sudo -S -p '' "$@"; }  # the Verified image's user password

echo "== build tools and headers"
sudo_ apt-get update -qq >/dev/null 2>&1
sudo_ apt-get install -y -qq build-essential pkg-config libgirepository1.0-dev libcairo2-dev >/dev/null 2>&1 || finish 91
curl -LsSf https://sh.rustup.rs | sh -s -- -y -q --default-toolchain none >/dev/null 2>&1 || finish 92
. "$HOME/.cargo/env"
rustup toolchain install 1.85.1 --profile minimal >/dev/null 2>&1 || finish 93
curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1 || finish 94
export PATH="$HOME/.local/bin:$PATH"
uv python install 3.12 >/dev/null 2>&1 || finish 95

echo "== SDK wheel"
rm -rf src && mkdir src && tar -xzf src.tar.gz -C src || finish 96
(cd src && uv venv -q --python 3.12 .build/venv \
  && uv pip install -q --python .build/venv --group build -r pyproject.toml "pip>=23.1" \
  && rustup run 1.85.1 uv run --no-project --python .build/venv python -m build --wheel --no-isolation 2>&1 | tail -5) \
  || finish 97
WHEEL=$(ls src/dist/openjiuwen_computeruse-*.whl) || finish 98
grep -q "native_build_failed" <(unzip -p "$WHEEL" '*_status.json' 2>/dev/null) && finish 99

echo "== agent venv"
PY=$HOME/cu-agent/bin/python
uv venv -q --python 3.12 "$HOME/cu-agent" || finish 100
uv pip install -q --python "$PY" "$WHEEL[agent]" pip || finish 101
mkdir -p wheels
"$PY" -m pip wheel -q "PyGObject==3.48.2" pycairo -w wheels 2>&1 | tail -3
uv pip install -q --python "$PY" wheels/*.whl || finish 102
# openjiuwen imports the OTLP exporter at startup without declaring it (0.1.19.post3 and 0.1.21 alike).
OTEL=$(uv pip list --python "$PY" 2>/dev/null | awk 'tolower($1)=="opentelemetry-sdk" {print $2}')
uv pip install -q --python "$PY" "opentelemetry-exporter-otlp-proto-common==$OTEL" || finish 103

echo "== checks"
"$PY" -c "import gi; gi.require_version('Atspi', '2.0'); from gi.repository import Atspi; import computer_use_agent.agent; print('imports ok')" \
  || finish 104
# The agent's real start: it builds, checks the desktop backend and stops at the (dummy) model call.
echo '{"model": "m", "provider": "OpenAI", "api_base": "http://127.0.0.1:9/v1", "api_key": "x"}' > /tmp/cu-check.json
DISPLAY=:0 COMPUTER_USE_PYTHON=$PY timeout 120 "$HOME/cu-agent/bin/computer-use-agent" --config /tmp/cu-check.json \
  --max-iterations 1 "say hi" 2>&1 | grep -E "computer_use_reset|Error|错误" | head -5
uv pip list --python "$PY" 2>/dev/null | grep -i -E "^(openjiuwen|pygobject|opentelemetry-sdk) "

echo "== pack"
mkdir -p out && cp "$WHEEL" wheels/*.whl out/ || finish 105
tar -czf out/cu-agent-env.tar.gz -C "$HOME" cu-agent .local/share/uv/python .local/bin/uv || finish 106
ls -la out/
finish 0
