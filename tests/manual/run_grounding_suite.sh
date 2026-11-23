#!/usr/bin/env bash
# Run the 10-task grounding A/B suite, one task per process to bound memory.
# Requires ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_MODEL in the environment.
set -u

ROOT="/d/code/computer-use"
OUT="$ROOT/outputs/grounding_eval/final10"
LOGS="$OUT/logs"
BASE_PKGS="/d/code/computer-use-pre536-pkgs"
BASE_CWD="/d/code/computer-use-pre536"
PY="$ROOT/.venv/Scripts/python.exe"
TASKS=(wps_append vscode_file wemeet_form wemeet_join vscode_settings)
mkdir -p "$LOGS"

cleanup_vscode() {
  powershell -NoProfile -Command \
    'Get-CimInstance Win32_Process | Where-Object { $_.Name -eq "Code.exe" -and $_.CommandLine -like "*grounding_eval*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }' \
    >/dev/null 2>&1 || true
}

run_one() {
  local variant="$1" task="$2" rep="$3"
  local run_id="final10-${variant}-${task}-r${rep}"
  local log="$LOGS/${run_id}.log"
  echo "=== $(date +%H:%M:%S) ${variant} ${task} r${rep} ==="
  if [ "$task" = "vscode_file" ] || [ "$task" = "vscode_settings" ]; then
    cleanup_vscode
  fi
  if [ "$variant" = "treatment" ]; then
    (cd "$ROOT" && uv run python tests/manual/grounding_task_eval.py \
      --task "$task" --variant treatment --run-id "$run_id" --repeat 1 --max-iterations 30) \
      >"$log" 2>&1
  else
    (cd /tmp && PYTHONPATH="$BASE_PKGS" CUA_EVAL_CWD="$BASE_CWD" "$PY" \
      "$ROOT/tests/manual/grounding_task_eval.py" \
      --task "$task" --variant baseline --run-id "$run_id" --repeat 1 --max-iterations 30) \
      >"$log" 2>&1
  fi
  local status=$?
  echo "    exit=$status log=$log"
}

for task in "${TASKS[@]}"; do
  for rep in 1 2; do
    run_one treatment "$task" "$rep"
    run_one baseline "$task" "$rep"
  done
done
echo "=== suite complete $(date +%H:%M:%S) ==="
