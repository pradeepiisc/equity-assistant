# Stock tracker — setup on a new Mac

Everything the tracker needs, in order. Tested on macOS 26 (Apple M4 Pro, 48 GB) on 2026-11-04. Architecture:
[ARCHITECTURE.md](ARCHITECTURE.md).

## 1. Python

```bash
conda create -n equity-assistant python=3.11 -y
conda activate equity-assistant
pip install -r requirements.txt
```

`scripts/run_tracker.sh` and the LaunchAgent use `~/miniconda3/envs/equity-assistant/bin/python`; set
`TRACKER_PYTHON` if the environment lives elsewhere.

## 2. Secrets (never committed)

Create `.env` in the project root (git-ignored) with the API keys `llm/client.py` names for the config set in
`tracker.yaml` → `llm.client`, and the Kite keys `KITE_API_KEY_<ACCOUNT>` / `KITE_API_SECRET_<ACCOUNT>` per account.
Without an LLM key the tracker still polls prices, filings and signals; filings are read when the key is there.

## 3. OCR — tesseract (scanned results PDFs)

```bash
brew install tesseract
tesseract --version          # 5.5.x, with the "eng" language
```

`layout.py` finds it on `PATH`, `/opt/homebrew/bin` or `/usr/local/bin` (the LaunchAgent's PATH has no Homebrew).
Without it, scanned pages keep their embedded text, as before 2026-11-04.

## 4. Local model — ollama + qwen3.8:27b

```bash
brew install ollama
brew services start ollama                # runs at login, serves http://127.0.0.1:11434
ollama pull qwen3.8:27b                   # 18 GB download from the ollama registry (ollama.com/library/qwen3.8)
ollama list                               # qwen3.8:27b   47951e4a6661   18 GB
```

- Model: Qwen 3.8, 27.8B parameters, nvfp4 quantisation, 256K context; it runs on the GPU in about 28 GB of memory
  with the 32K context the tracker asks for — a 48 GB Mac or larger. Downloaded 2026-11-04 (ollama 0.40.5); ID
  `47951e4a6661`.
- The tracker calls it with thinking off and without ollama's JSON mode (this model answers 501 "structured output
  is unavailable" to it); the prompt asks for one JSON object and `localllm.py` parses the first one in the reply.
- Another model: set `llm.local_model` in `tracker.yaml` (and `llm.local_url` for another host). Smaller Macs:
  `gemma4:26b-a4b` (16 GB, faster, slightly weaker on tables) was the other candidate considered.
- Not running → company profiles, policy matching and the movers' reasons wait; nothing else changes.

## 5. Chrome debug profile (BSE-only stocks via Screener)

`scripts/launch_chrome_debug.sh` starts a separate Chrome profile with remote debugging; the tracker reads
Screener pages of BSE-only stocks through it (your own Chrome is left alone). Sign in to Screener once in that
profile.

## 6. First run

```bash
python -m tracker init                    # exchange master data + universe (holdings, watchlist, notes)
python -m tracker prices                  # bhavcopy back-fill (≈ 400 sessions)
python -m tracker signals
bash scripts/run_tracker.sh               # dashboard → http://127.0.0.1:7788 (ticks every 3 min while open)
bash scripts/tracker_schedule.sh install  # keep ticking when the dashboard is closed (LaunchAgent)
bash scripts/kite_daily_schedule.sh install   # 16:00 holdings save for each Kite account
```

On first install macOS may ask to let `python3.11` read the Documents folder; until allowed, the LaunchAgent's
ticks hang (the Status page says so).

## 7. Check

```bash
python -m tracker status
curl -s http://127.0.0.1:11434/api/tags | grep qwen3.8
```

The Status page shows the pollers, each feed's cadence, today's LLM spend (local calls are logged at ₹0) and the
job log (look for `policy`, `profiles` and `movers` once the model is up).
