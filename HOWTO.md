# How to get started (Linux)

**XenBlocks Miner by Tony.x1** — Linux GPU miner for [XenBlocks](https://xenblocks.io).

This tree is the **Linux port** of xnminer (`xnminer-linux`). It is separate from the Windows miner.

---

## One-liner install (Ubuntu / Debian)

```bash
curl -fsSL https://raw.githubusercontent.com/badnob/xnminer-linux/main/install.sh | bash
```

Already cloned?

```bash
cd xnminer-linux
chmod +x install.sh
./install.sh
```

### Install order (fixed to avoid conflicts)

`install.sh` always does steps in this sequence:

| Step | What | Why this order |
|------|------|----------------|
| 1 | Base apt tools | Network/certs/git first |
| 2 | **Python 3.10+**, pip, venv, **cmake**, g++ | Build/runtime tools before GPU stack |
| 3 | **NVIDIA driver** (`ubuntu-drivers`) | Driver first; needs kernel headers |
| 4 | **CUDA toolkit only** (`cuda-toolkit` or `nvidia-cuda-toolkit`) | Toolkit only — **not** the full `cuda` meta package (that would reinstall drivers and conflict) |
| 5 | **`.venv` + `requirements.txt`** | Isolated pip; never system site-packages |
| 6 | **`./native/build.sh`** → `libxen_cuda.so` | Needs cmake + nvcc last |

Useful flags:

| Flag | Effect |
|------|--------|
| `--cpu-only` | Python deps only (no driver / CUDA / native build) |
| `--no-driver` | Skip NVIDIA driver (use after reboot, or if already installed) |
| `--no-cuda` | Skip CUDA toolkit packages |
| `--no-build` | Skip `./native/build.sh` |

After a **new driver install**, reboot, then:

```bash
cd xnminer-linux
source .venv/bin/activate
./install.sh --no-driver    # finish engine build if it was skipped
./start-miner.sh
```

> CUDA network repo is tried first (`cuda-toolkit`). If that fails, falls back to distro `nvidia-cuda-toolkit`. Do not install the meta package named `cuda` alongside `ubuntu-drivers`.

---

## What you need

1. **Linux** (Ubuntu 22.04+ / Debian / similar recommended)
2. **Python 3.10+** (`python3` and `python3-pip` / venv)
3. **NVIDIA GPU + proprietary drivers**
   - Confirm with `nvidia-smi`
   - This miner does **not** ship NVIDIA drivers
4. For CUDA mining: **CUDA Toolkit** (nvcc + headers) and **cmake** 3.18+
5. An **EVM wallet** address (`0x` + 40 hex characters) for rewards

No NVIDIA GPU? Mine on **CPU** (much slower) — set `backend = cpu` in `miner.ini` after first run, or use `./install.sh --cpu-only`.

---

## Setup (manual)

```bash
cd /path/to/xnminer-linux
chmod +x start-miner.sh native/build.sh

# Optional: isolated env
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Build native CUDA engine (required for backend = cuda)
./native/build.sh
```

If `nvcc` is not on PATH:

```bash
export PATH=/usr/local/cuda/bin:$PATH
export LD_LIBRARY_PATH=/usr/local/cuda/lib64:${LD_LIBRARY_PATH:-}
./native/build.sh
```

For older GPUs, set architectures explicitly, e.g.:

```bash
CMAKE_CUDA_ARCHITECTURES=75;86 ./native/build.sh
```

---

## Run

```bash
./start-miner.sh
```

Or:

```bash
python3 main.py
```

- First run → enter wallet → mining starts  
- Live dashboard in the terminal  
- **Ctrl+C** stops mining and flushes any queued blocks  

---

## Check it’s working

| Check | Where |
|--------|--------|
| Hashrate | Dashboard speed / H/s |
| Accepts | Accepted / local accepts |
| Network | Dashboard network status |
| Logs | `data/session.log` |
| Away from machine | [woodyminer.com](https://woodyminer.com) (enabled by default) |

---

## Common issues

| Problem | Fix |
|---------|-----|
| `python3` not found | Install Python 3.10+ |
| `libxen_cuda.so` missing | Run `./native/build.sh` or `./install.sh` |
| CUDA / NVML errors | Install latest NVIDIA driver; reboot; check `nvidia-smi` |
| No GPU | Set `backend = cpu` in `miner.ini` |
| Another miner running | Stop the other process, or delete stale `data/miner.lock` |
| Wrong wallet | Edit `miner.ini` → `[account] address = 0x...` |
| Power boost failed | Run as root **or** allow NVML power control; otherwise mining still works at default power limit |

---

## Safety

- VRAM limits scale with **your** GPU size (desktop headroom kept free).  
- Temp guard cools the card if it runs hot.  
- Run **only one** copy of this miner per machine (instance lock).  
- Keep the Windows miner (`../xnminer`) and this Linux tree separate — do not run both against the same GPU at once.

---

## More detail

See **`README.md`** for features, advanced config, and project layout.

Happy mining.
