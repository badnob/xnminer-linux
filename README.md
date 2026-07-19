# XenBlocks Miner by Tony.x1 #

Modular **Python + native CUDA** miner for [XenBlocks](https://xenblocks.io) — **Linux** port (`xnminer-linux`) with a live console dashboard, smart submit queue, halving-aware rewards, and hardware-safe GPU mining.

> CUDA/CPU miner · live dashboard · XNM / XUNI / XBLK · %VRAM safety caps · superblock detection · Woodyminer online stats

---

## Features

### Mining engines

- **Native CUDA backend** (`libxen_cuda.so`) — high-performance Argon2id hashing without relying on a legacy binary path as primary
- **CPU backend** — pure Python Argon2 mining when GPU/CUDA isn’t available
- **Legacy GPU bridge** — optional supervision of an external xenblocks binary + DB watcher
- **Merged mining** of **XNM**, **XUNI**, and **XBLK** (superblocks) in one hash stream
- **Official-style block classification**
  - **XUNI** — `XUNI` + digit, time-window rules
  - **XBLK** — XEN11 + superblock uppercase rule (digest body; threshold aligned with the open-source miner)
  - **XNM** — normal XEN11 blocks
- **Key strategies** — random (default), Fibonacci, pluggable strategy registry
- **Difficulty-aware batch sizing** — Argon2 memory cost scales with network difficulty; batch size is recomputed to stay within VRAM caps

### Low-difficulty multi-lane harvest (VRAM fill)

When network difficulty **drops** (e.g. 1100 → 100), each hash uses **less VRAM**. A single lane would leave most of the GPU idle. The miner then:

- **Spins up extra CUDA lanes** (configurable `max_lanes`, e.g. up to 4) with distinct key prefixes  
- **Re-plans batch size per lane** so combined work **keeps the configured VRAM budget full**  
- Logs a **harvest push** summary (lanes × batch, projected VRAM / free)  
- On difficulty **rising** again, **collapses back toward 1 lane** and restores the normal plan  
- During the short transition window, new hits are **queued** (no live submit/flush races) until mining is stable  
- Thermal stress can **temporarily reduce lane cap**; cool-down restores it when safe  

This is the “keep the card full when difficulty dips” path — not a fixed single batch for all difficulties.

### Live dashboard

- Full-screen **Rich** live UI (alternate screen — resize-safe, no scrollback mess)
- Compact stats table: Found / Accepted (tokens) / Rejected (pool) / Queued / Resubmit
- **Session timelapse** with **1-hour H/s sparkline** and 1h average
- Recent events feed (FOUND, ACCEPTED, QUEUED, RESUBMIT, WARN, …)
- GPU readout: VRAM, util %, temperature, power, **CUDA batch / multi-lane** (e.g. `4 lanes × batch`)
- Network status + current difficulty (including stale/offline awareness)

### Wallet & rewards

- On-chain **wallet balances** (XNM / XUNI / XBLK) via XenBlocks RPC
- **Day vs yesterday** and **week-to-date vs Monday** comparisons (1am mining-day boundary)
- **Local accepts** history with day/week deltas
- **Halving-aware token display** for XNM (yearly schedule: 10 → 5 → **2.5** → …)
- XUNI / XBLK shown as **1 token per accepted block**
- First-run **wallet setup** saved into `miner.ini`

### Queue & reliability

- Persistent **SQLite + JSONL** queue for blocks that can’t submit yet
- Holds blocks during difficulty transitions, XUNI window, network down, and shutdown
- **CPU-capped parallel submit pool** for verify/flush work
- Distinguishes true pool rejects vs difficulty mismatch / window / timeouts
- Graceful **Ctrl+C**: stop mining, flush queue when possible, exit cleanly
- Single-instance **lock** (`miner.lock`) so two miners don’t fight one GPU

### Hardware safety & efficiency

- **VRAM caps as % of each GPU’s total** (safe headroom on 8 GB–32 GB+ cards)
  - ~69% target use · ~25% desktop free · ~93% emergency · ~4% min free · ~6% CUDA overhead
- Absolute floors so tiny cards never go to zero free VRAM
- **Temp guard**: warn / graceful cooldown / auto-restart
- **Lane cap reduction** after thermal stress during multi-lane harvest; restore when cool
- **NVML power boost** toward configured % of card power limit; ease near warn temp
- NVML / nvidia-smi power boost (Windows power-plan toggle is disabled on Linux)
- Continuous NVML monitoring (temp, VRAM, util, power) via **pynvml**  
  (uses the **installed** NVIDIA driver — **no drivers are shipped**)
- CUDA `max_lanes` / `lane_reserve` / `vram_reference_difficulty` in `miner.ini` control harvest behaviour

### Network & ops

- Background **difficulty poller** (non-blocking mining loop)
- Server **uptime tracker** (last 6 hours, newest first, outage stats)
- Session logging to `data/session.log`
- Diagnostics mode (`--diagnose`)
- One-command Linux start: `./start-miner.sh`

### Woodyminer online stats (away from hardware)

- Integrates with **[Woodyminer](https://woodyminer.com)** so you can check mining performance **from any browser** while away from the rig
- Periodic upload of live stats (hashrate, accepts, uptime, GPU snapshot, difficulty, custom worker name)
- **Leaderboard** presence for your wallet / machine without needing the local console open
- Optional and configurable in `miner.ini` (`woodyminer_enabled`, upload URL/period, `woodyminer_custom_name`)
- Does not replace the local dashboard — local UI for at-the-machine detail; Woodyminer for **remote readability**

### Configuration

- Single **`miner.ini`** for wallet, backend, efficiency, CUDA, monitoring, queue
- CLI overrides: `--backend`, `--strategy`, `--lanes`, `--max-seconds`, `--no-dashboard`
- Data directory for DB, queue, stats history, balances, uptime, timelapse

### Developer-friendly

- Modular layout: `core`, `mining`, `monitoring`, `efficiency`, `block_queue`, `networking`
- Native CUDA engine build script (`native/build.sh`)
- Unit tests for rewards, VRAM policy, block types, queue, dashboard, and more

---

## Requirements

| Component | Notes |
|-----------|--------|
| **Python** | 3.10+ (`python3`) |
| **Linux** | `./start-miner.sh` |
| **NVIDIA GPU + driver** | For CUDA mining — install from [NVIDIA](https://www.nvidia.com/Download/index.aspx) |
| **EVM wallet** | `0x…` address for rewards (prompted on first run) |

> This project does **not** ship NVIDIA drivers. Build `libxen_cuda.so` with `./native/build.sh` for CUDA mining.

---

## Quick start (Linux)

**Full walkthrough:** **[HOWTO.md](HOWTO.md)**

### One-liner (Ubuntu / Debian)

Installs Python, pip, cmake, NVIDIA driver (if needed), CUDA toolkit, requirements, and builds the CUDA engine:

```bash
curl -fsSL https://raw.githubusercontent.com/badnob/xnminer-linux/main/install.sh | bash
```

Then:

```bash
cd xnminer-linux
./start-miner.sh
```

> Reboot if a new NVIDIA driver was installed, then run `./install.sh --no-driver` if the engine build was skipped.

### Manual steps

1. Install **Python 3.10+**, **NVIDIA drivers**, and (for CUDA) **CUDA Toolkit + cmake** (or use `./install.sh`).  
2. Copy or clone this tree onto the Linux machine.  
3. Build the engine: **`./native/build.sh`**.  
4. Run **`./start-miner.sh`**.  
5. Enter your **EVM wallet** when asked — then mining starts.

The launcher creates `miner.ini`, installs Python packages, and prompts for your wallet. See **HOWTO.md** for package details.

**Ctrl+C** stops mining, flushes the queue when possible, then exits.

> **Privacy:** Real `miner.ini` (wallet, worker name, local paths) is gitignored. Only `miner.ini.example` is published. Never commit `data/`.

---

## Configuration

Edit **`miner.ini`**:

```ini
[account]
address = 0xYourWallet...
worker =                # empty = auto unique name (xnminer-xxxxxxxx)

[mining]
backend = cuda          # cuda | cpu | gpu (legacy)
strategy = random

[efficiency]
# VRAM policy as % of each GPU's total (auto-scales for all card sizes)
target_vram_pct = 69.09
desktop_headroom_pct = 25.12
emergency_vram_pct = 92.78
min_headroom_pct = 3.68
runtime_overhead_pct = 6.28
max_gpu_temp_c = 75
warn_gpu_temp_c = 72
gpu_power_target_pct = 100

[cuda]
dll_path = native/build/bin/libxen_cuda.so
max_lanes = 4
```

### CLI examples

```bash
python3 main.py
python3 main.py --backend cpu
python3 main.py --no-dashboard
python3 main.py --diagnose
python3 main.py --max-seconds 3600
```

---

## Build CUDA engine

Requires **g++**, **CMake** 3.18+, optional **Ninja**, and the **CUDA Toolkit** (`nvcc` on `PATH`).

```bash
chmod +x native/build.sh
./native/build.sh
```

Output: `native/build/bin/libxen_cuda.so`

> Default arches are sm_90 / sm_120. For older GPUs:
>
> ```bash
> CMAKE_CUDA_ARCHITECTURES=75;86 ./native/build.sh
> ```

---

## Project layout

```text
├── main.py                 # Entry point
├── miner.ini               # Config
├── install.sh              # One-shot host setup (apt + CUDA + deps)
├── start-miner.sh          # Linux launcher
├── core/                   # Supervisor, models, instance lock
├── mining/                 # CUDA / CPU backends, block types, Argon2
├── monitoring/             # Dashboard, wallet, uptime, rewards, woodyminer
├── efficiency/             # VRAM %, temp guard, power boost, lanes
├── block_queue/            # Persist + flush submit queue
├── networking/             # Difficulty poller, submitter
├── strategies/             # Key generation strategies
├── native/                 # CUDA engine source + build
├── data/                   # Runtime DB, logs, stats (local)
└── tests/                  # Unit tests
```

---

## Limitations

- **No NVIDIA drivers bundled** — install from NVIDIA for CUDA mining
- **CUDA binary** may need a rebuild for older GPU architectures
- **No automatic CPU fallback** if CUDA fails — set `backend = cpu` in `miner.ini`
- **Linux-first** in this tree; Windows version lives in sibling `../xnminer`
- Network RPC for wallet balances can time out; dashboard shows cached/stale state when needed

---

## License / credits

- Miner UI & orchestration: **Tony.x1**
- Native hashing roots / reference behaviour: XenBlocks ecosystem and open-source miner patterns (Argon2id, XEN11 / XUNI / superblock rules)

---

## Support

- Logs: `data/session.log`
- Queue / DB: `data/blocks.db`, `data/queue.jsonl`
- Stats: `data/mining_stats_history.json`, `data/balance_history.json`, `data/server_uptime.json`

If you hit issues, include the relevant log lines and your GPU model + `backend` setting.
