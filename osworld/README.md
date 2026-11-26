# This repo's agent on OSWorld-Verified

`osworld/` runs this repo's own agent (`computer_use_agent`, on this repo's SDK) on **OSWorld-Verified** tasks.
You bring your own OSWorld-Verified checkout; it supplies the task files, the VM image and the graders, used as-is
and never edited. Everything of ours lives here. Setup, step by step: [`docs/JIUWEN_SETUP.md`](docs/JIUWEN_SETUP.md).

## How a run works

`run_task_sdk.py` runs in your checkout's `.venv` on the host:

1. OSWorld's `DesktopEnv` starts the task's VM (Docker, Ubuntu 22.04) and runs the task's setup; `ensure_guest_tools.py` installs xdotool offline from `guest_debs/` (the Verified image lacks it; the environment snapshot lists open windows with it), as the earlier agent's runner did.
2. The agent environment (`vm_env/dist/cu-agent-env.tar.gz`, built once by `vm_env/build_agent_env.py`: a
   Python 3.12 venv with this repo's SDK wheel, its `[agent]` extra and PyGObject) and this `osworld` package are
   unpacked into the VM.
3. `vm_agent.py` runs **inside the VM**: `ComputerUseAgent` as `computer-use-agent` builds it, plus the OSWorld
   pieces below. The SDK acts on the VM's own desktop.
4. If the agent called `declare_infeasible`, the runner records OSWorld's `FAIL` action; then OSWorld grades the VM.

What `vm_agent.py` adds to the agent, all taken from the earlier agent-core OSWorld agent (the `kwangyy/OSWorld`
fork, branch `verified-agent`) and adapted only where it named tools this agent does not have:

| Piece | Source |
|---|---|
| `declare_infeasible` (infeasible tasks score 1 only through `FAIL`) | `tools/infeasible.py` |
| Benchmark-integrity guard: blocks reading OSWorld's tasks, graders and answers, and VM restarts | `benchmark_integrity.py`, `rails/benchmark_integrity_rail.py` |
| Environment snapshot (OS, app versions, open windows, `~/Desktop`, date) in the system prompt | `env_snapshot.py` |
| The skills of the apps the task names or has open, in the system prompt; `inspect_file`, `read_skill`, `list_skill` | `skills/`, `tools/osworld_tools.py` |
| The message history in `trajectory.jsonl` after every step | `rails/trajectory_rail.py` |
| The task rules (`TASK_RULES`, `V1_RULES`) in the system prompt | `osworld_rules.py` |
| GUI step cap: stops at `--max-gui-steps` GUI actions (default 100, the leaderboard budget), counted from `computer_use`'s action receipts (one per input sent, reads free), and tells the model its budget | `rails/gui_step_cap_rail.py` |
| Images and bytes per model request in `agent.log`; a failing model call retried, then re-routed to `OPENJIUWEN_REFUSAL_FALLBACK_MODEL` | `rails/request_size_rail.py`, `rails/model_fallback_rail.py` |
| The VM sudo password, the VM note and the integrity text in the prompt | `vm_agent.py` |
| Pre-task gate (`--gate`, `--gate-binding`, both on): a read-only pass that loads the task's skills, checks its resources and decides feasibility; a binding INFEASIBLE verdict with evidence records `FAIL` without running the agent, otherwise the agent gets the gate's skills and its notes every step. It sees the screen through this repo's SDK, read-only | `gate.py`, `rails/gate_notes_rail.py` |
| Autosave reminder after `--autosave-every` GUI edits (default 6) in one window without a save | `rails/autosave_rail.py` |
| Residential proxy (`--proxy`, on) for tasks marked `"proxy": true`, checked before the VM starts | `proxy.py` |

## Running

From this repo's root, with your checkout's venv (`<osworld>`):

```
<osworld>\.venv\Scripts\python.exe osworld\run_task_sdk.py --domain chrome --task-id 06fe7178-4491-4589-810f-2e2bc9502122
<osworld>\.venv\Scripts\python.exe osworld\run_parallel.py --max-parallel 2 --tag mytest chrome/06fe7178-4491-4589-810f-2e2bc9502122 gimp/045bf3ff-9077-4b86-b483-a1040a949cff
```

- Results, in this repo's root: `results\sdk_agent\<domain>_<uuid8>[_<tag>]\` (`result.json`, `agent.log`,
  `trajectory.jsonl`, `events.jsonl`, `env_snapshot.txt`); `run_parallel`'s logs and scoreboard in
  `results\sdk_agent\_parallel_logs[_<tag>]\`. `run_parallel` exits 1 and names the runs that produced no score or
  whose agent did not finish.
- `--time-budget` (default 1800 s) ends an agent that does not finish; `--max-iterations` caps its steps.
- `OPENJIUWEN_BENCHMARK_INTEGRITY_MODE=off` turns the integrity guard off.
- Keep `--max-parallel` small: each VM takes about 5 GB of RAM.
- "/dev/kvm is not available to Docker": the runner loads the KVM module in Docker Desktop's WSL VM itself and stops
  if it cannot (`ensure_kvm.py`). By hand: `wsl -d docker-desktop -u root -e modprobe kvm_intel` (`kvm_amd` on AMD).

## Not ported

From the earlier agent: the look-only-loop nudge. The completion review was dropped. The earlier agent's own code (agent-core's cua tools, its runner and venv) is not carried over.
