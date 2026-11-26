# Reproducible setup — this repo's agent on OSWorld-Verified

How to run this repo's agent (`computer_use_agent`, on this repo's SDK) on **OSWorld-Verified** (upstream
`xlang-ai/OSWorld`) with the **Docker provider** on a Windows host. You set up your own OSWorld-Verified checkout
(called `<osworld>` below); everything of ours lives in this repo's `osworld/` folder. It supplements upstream's
`README.md` and `SETUP_GUIDELINE.md` in `<osworld>`; read those for AWS, VMware or proxy setup.

## Pin point

| Piece | Pin |
|---|---|
| Agent | this repo, branch `osworld`, folder `osworld/` |
| OSWorld checkout | upstream `xlang-ai/OSWorld@b138d348` (2026-09-14), unmodified |
| VM image | upstream `xlangai/ubuntu_osworld` `Ubuntu.qcow2.zip` (12,273,896,463 bytes) |
| Tasks / graders | upstream's, unmodified (`evaluation_examples/`, `desktop_env/`) |
| Agent environment in the VM | built from this repo's HEAD by `osworld/vm_env/build_agent_env.py`; openjiuwen from PyPI |

The OSWorld checkout is not edited at all. What the harness needs on Windows is patched at runtime by the runner,
not in upstream's files:

- `/dev/kvm`: Verified's docker provider tests the path on the *host*, which is never there on Windows, so every
  guest ran without KVM (4 min -> 25 min). `run_task_sdk.py` first proves the Docker daemon has it
  (`osworld/ensure_kvm.py`), then answers that one path.
- `osworld/verified_patches.py`: `setup.py` removes the browse-history temp dir while the db is still open
  (`WinError 32`, crashed `chrome/44ee5668`), and some scripted setup and grader waits are too short on a slow guest.
- `osworld/ensure_guest_tools.py`: the Verified image lacks `xdotool`, which the environment snapshot's
  open-window list needs; it is installed offline from `osworld/guest_debs/`.

## Prerequisites

Verified on Windows 11 + Docker Desktop 29.4 (WSL2 backend).

- `git`, [`uv`](https://astral.sh/uv), Docker Desktop
- An OpenRouter API key
- Disk: ~37 GB for the VM image (12 GB zip + 24.5 GB unzipped; the zip can go once unzipped), ~250 MB for the agent
  environment, ~20 MB of results per task
- Memory: ~5 GB per guest VM on top of what else is running (see step 3)

## 1. Clone and install

Your OSWorld-Verified checkout, `<osworld>`:

```bash
git clone https://github.com/xlang-ai/OSWorld
cd OSWorld
git checkout b138d348
# the agp_client submodule is registered with an SSH url; fetch it over HTTPS
git -c url."https://github.com/".insteadOf="git@github.com:" submodule update --init mm_agents/surferH/agp_client
uv sync --frozen --python 3.12                     # <osworld>/.venv: DesktopEnv + graders
```

`--python 3.12` matters: `uv` may pick 3.14, and `paddlepaddle` has no 3.14 wheels. Nothing of ours is installed in
`<osworld>/.venv`; the runners only add this repo's root to its path.

Then point this repo at the checkout: put `OSWORLD_VERIFIED_ROOT=<osworld>` (an absolute path) in this repo's `.env`
(step 5). The runners refuse to start without it.

## 2. Docker and KVM — do not skip

```bash
docker info
docker run --rm --device /dev/kvm busybox ls -la /dev/kvm
```

The second command must print a device line. Without KVM the guest runs in
software emulation: tasks take 5–6x longer and some time out.

On Windows the WSL kernel builds KVM as a module and does not always reload it
after Docker Desktop or WSL restarts. `run_task_sdk.py` runs
`osworld/ensure_kvm.py` first, which loads it for you and aborts if it cannot;
by hand:

```bash
wsl -d docker-desktop -u root -e modprobe kvm_intel   # AMD: kvm_amd
```

## 3. Host capacity (Windows)

Each guest is a full VM. Before a run, check free commit (RAM + page file),
which is what runs out first on Windows:

```powershell
[math]::Round((Get-CimInstance Win32_OperatingSystem).FreeVirtualMemory/1MB,1)
```

Keep ≥ ~12 GB free for `--max-parallel 2`, ≥ ~7 GB for 1. With a fixed page
file Docker Desktop's WSL backend crashed at the commit limit; a
system-managed page file plus `~/.wslconfig` (`memory=22GB`, `swap=8GB`) ran 3
guests in parallel on a 32 GB host. Keep `--max-parallel` at 3 or less.

## 4. VM image

The first `DesktopEnv` start downloads the image into `docker_vm_data/` and
unzips it. Downloading it yourself first is easier to watch and resume:

```bash
mkdir -p docker_vm_data
curl -L -C - --retry 5 -o docker_vm_data/Ubuntu.qcow2.zip \
  https://huggingface.co/datasets/xlangai/ubuntu_osworld/resolve/main/Ubuntu.qcow2.zip
# expect 12273896463 bytes; at ~1 MB/s this is ~3 h (parallel ranges did not help)
<osworld>/.venv/Scripts/python.exe -c "import zipfile; zipfile.ZipFile('docker_vm_data/Ubuntu.qcow2.zip').extractall('docker_vm_data')"
```

Unzip before the first parallel run so several runs do not race to extract it.
Never delete `Ubuntu.qcow2` afterwards. The guest user's password is
`password` (`DesktopEnv`'s default for Docker); the runner passes it to the
agent for `sudo`.

## 5. Environment variables

`.env` in this repo's root (git-ignored; never commit it):

```
OSWORLD_VERIFIED_ROOT=C:\path\to\OSWorld   # your checkout, absolute
OPENROUTER_API_KEY=...
OPENROUTER_API_BASE=https://openrouter.ai/api/v1
MODEL_NAME=anthropic/claude-opus-5
# LLM-judged graders, routed through OpenRouter:
OSWORLD_EVAL_MODEL_PROVIDER=openai_compatible
OSWORLD_EVAL_MODEL_BASE_URL=https://openrouter.ai/api/v1
OSWORLD_EVAL_MODEL_API_KEY_ENV=OPENROUTER_API_KEY
OSWORLD_EVAL_MODEL_NAME=openai/gpt-5.2
OSWORLD_EVAL_MODEL_REASONING_EFFORT=none
# optional
OPENJIUWEN_BENCHMARK_INTEGRITY_MODE=   # set to off to disable the guard
```

The runner writes the model settings into the agent's config inside the VM, so the API key is on the VM's disk for
the run (the graders do not read it; the VM's disk is discarded afterwards).

## 6. Agent environment

The agent runs inside each task's VM, from an environment built once, inside an OSWorld VM (the same Ubuntu 22.04
image the tasks use), from this repo's committed HEAD:

```bash
"$OSWORLD/.venv/Scripts/python.exe" osworld/vm_env/build_agent_env.py
```

About 10 minutes with network access in the VM. It writes `osworld/vm_env/dist/` (git-ignored):
`cu-agent-env.tar.gz` (a Python 3.12 venv with this repo's Linux wheel, its `[agent]` extra and PyGObject/pycairo
built for 3.12, plus uv's Python), the wheel itself and the PyGObject/pycairo wheels, and `build.log`. Rebuild it
after changing the SDK or `computer_use_agent`; changes to `osworld/` need no rebuild, because the runner uploads
the `osworld` package on every run.

Why Python 3.12 in the VM: the SDK runs its Linux driver on the system Python only when that is 3.11 or newer, and
Ubuntu 22.04's is 3.10, so the driver runs on the venv's Python (`COMPUTER_USE_PYTHON`), which then needs its own
PyGObject. The build also adds `opentelemetry-exporter-otlp-proto-common`, which openjiuwen imports at start-up
without declaring it.

## 7. Smoke test

Commands in steps 7 and 8 run from this repo's root; `OSWORLD` is your checkout.

```bash
"$OSWORLD/.venv/Scripts/python.exe" osworld/run_parallel.py --max-parallel 1 --tag smoke \
  chrome/06fe7178-4491-4589-810f-2e2bc9502122 \
  gimp/045bf3ff-9077-4b86-b483-a1040a949cff
```

| Task | Expect | What it shows |
|---|---|---|
| `chrome/06fe7178-4491-4589-810f-2e2bc9502122` (reopen the last closed tab) | 1 | the SDK drives the VM's desktop; ~30–40 s of agent time |
| `gimp/045bf3ff-9077-4b86-b483-a1040a949cff` (CMYK mode in GIMP) | 1 | an infeasible task answered with `declare_infeasible`, recorded as `FAIL` |

Both scored 1 on 2026-11-25 (Opus 5).

## 8. Running tasks

Several tasks (`<domain>/<uuid>`, bounded by `--max-parallel`):

```bash
"$OSWORLD/.venv/Scripts/python.exe" osworld/run_parallel.py --max-parallel 2 --tag <name> <domain>/<uuid> ...
```

One task, without the scoreboard:

```bash
"$OSWORLD/.venv/Scripts/python.exe" osworld/run_task_sdk.py --domain chrome --task-id 06fe7178-4491-4589-810f-2e2bc9502122
```

Upstream's manifests list task ids: `evaluation_examples/test_all.json` (369), `test_nogdrive.json` (361, without
the 8 Google Drive tasks that need account setup). Results go to `results/sdk_agent/` in this repo's root.

## 9. Tests

Agent-side tests run in this repo's `[agent]` extra on Python 3.12, the VM's agent environment; runner-side tests run
in your checkout's venv:

```bash
uv run --isolated -p 3.12 --extra agent --with pytest --with opentelemetry-exporter-otlp-proto-common \
  pytest -c osworld/pytest.ini --rootdir osworld osworld/tests
OSWORLD_VERIFIED_ROOT="$OSWORLD" PYTHONPATH="$OSWORLD" uv run --no-project --with pytest \
  --python "$OSWORLD/.venv/Scripts/python.exe" pytest -c osworld/pytest.ini --rootdir osworld \
  osworld/tests/test_sdk_runner_wait.py osworld/tests/test_jiuwen_run_parallel.py \
  osworld/tests/test_jiuwen_verified_patches.py osworld/tests/test_jiuwen_ensure_kvm.py
```
