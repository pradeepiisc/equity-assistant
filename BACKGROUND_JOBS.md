# Background jobs on this Mac

What runs in the background for this project, how often, and what it costs in CPU, GPU and memory.
Snapshot taken on 2026-11-05 (Apple M4 Pro, 48 GB unified memory).

## 1. Jobs and schedules

They are all macOS LaunchAgents in `~/Library/LaunchAgents/`, plus the dashboard server's own poller.

| Job / Agent | Schedule / Interval | Purpose | Resource Impact |
| :--- | :--- | :--- | :--- |
| **`com.equity-assistant.tracker`** <br>*(LaunchAgent + Server Poller)* | **Every 3 minutes** (`StartInterval 180`) | Calls `scripts/tracker_tick.sh` → `python -m tracker tick`, which runs the price feeds, NSE filing downloads and LLM extractions. The dashboard (`python -m tracker serve`) also ticks every 3 min while it is open. A lock file lets only one tick run at a time. | **Very High** (starts the local LLM jobs below) |
| ↳ **`local_model_job` (`local_read`)** | Every tick that has a pending filing *(until 2026-11-05)*; **now nightly 17:00–21:00 only, see §4** | Runs the local LLM (`qwen3.8:27b`) to write a second summary of each filing beside gpt-4.1's (`extract.shadow_read`). | **Highest CPU/GPU load** (1.5–3.5 min of full GPU per document) |
| ↳ movers / policy / profiles | One task per tick (movers after EOD, PIB policy every 3 h, 5 profiles a day) | Local model: why a stock moved, which companies a PIB release affects, company profiles. | Medium (fewer and shorter calls) |
| ↳ **`documents`** | Every tick (at most 60 s) | Downloads filing PDFs and extracts them with gpt-4.1 (Azure). | Low to Moderate (I/O & API calls) |
| ↳ **`feeds` & `catch_up`** | Announcements every 3 min on weekdays 09:00–18:00; other feeds every 10 min, 07:00–23:30; catch-up every 30 min | Polls NSE announcements, corporate actions, results, SAST and the rest. | Very Low (fast HTTP polling) |
| ↳ **`prices` & `signals`** | Weekdays after 18:15 IST | Syncs the EOD bhavcopy prices and recalculates the DMA technicals. | Low |
| ↳ `deals`, `shareholding`, `holders`, `results`, `history`, `alerts`, `bse_fallback`, `valuations` | When due inside a tick | Bulk/block deals, shareholding, XBRL results, alerts, the Screener fallback for BSE-only stocks, valuation memos (gpt-4.1). | Low |
| **`com.equity-assistant.kite-daily`** | Weekdays every 30 min from 16:00 to 21:00 | Fetches the Kite portfolio holdings and updates the daily tracking files. Accounts already saved that day are skipped. | Low |
| **`com.equity-assistant.llm-eval`** *(new)* | Weekdays 17:00 (runs until 21:00 at most) | Local-model summaries of the day's filings (qwen3.8:27b and gemma4:26b-a4b), graded by gpt-5.2, written to `data/tracker/evals/<mon>-<d>-eval.md`. | High, but only in the evening window |
| **`com.equity-assistant.llm-eval-final`** *(new)* | Weekdays 21:45; does something only once, after 7 weekdays of evals | Combines the daily evals into `data/tracker/evals/final-eval.md` and records the decision: local model or gpt-4.1. | Low (one cloud call at most) |
| **`com.equity-assistant.priority`** *(new)* | Every day 21:00 | Priority list of held and watched stocks with a results, presentation, press release or concall filing in the last 7 days, graded by gpt-5.2 as a small-cap momentum investor (tier 1 = invest more … 6 = exit), with the rotations. Written to `data/tracker/priority/<mon>-<d>-priority.md`. | Low (cloud calls, ~1–3 min) |
| **`sh.brew.ollama`** | Always running (`KeepAlive: true`) | Runs `ollama serve` at `http://127.0.0.1:11434`. | Memory-resident (~26–30 GB while a model is loaded) |

Logs: `data/tracker/logs/` (`tick.log`, `kite_daily.log`, `llm_eval.log`, `priority.log`) and `/opt/homebrew/var/log/ollama.log`.

### Which job uses the most resources?

**The `local_read` / `local_model_job`** uses by far the most:
- The filings from 4–5 November were queued for the comparison (`shadow_from: 2026-11-04`), so the scheduler sent a
  new filing to Ollama on almost every 3-minute tick.
- Each filing takes **50 to 215 seconds** of continuous GPU/Metal compute. That keeps the Apple Silicon chip under
  high load for long stretches, which is what spins the fans up.

## 2. Why Ollama uses ~30 GB of RAM

On Apple Silicon the CPU and GPU share **unified memory** (system RAM):

1. **Model weights (~18 GB):**
   - The local model is `qwen3.8:27b` (27.8 billion parameters, 4-bit quantised).
   - Ollama loads the whole ~18 GB model into unified memory so the GPU/Metal engine can run it.
2. **32K-token context KV cache (~8–12 GB):**
   - The tracker asks for a **32,768-token context window** (`num_ctx: 32768`) with `OLLAMA_KV_CACHE_TYPE: q8_0`.
   - Room for 32K tokens of key/value state plus Metal scratch buffers takes another 8–12 GB.
3. **Total memory footprint:**
   - From `/opt/homebrew/var/log/ollama.log`:
     ```text
     msg=memory peak="30.15 GiB" held="26.39 GiB"
     ```
   - Ollama keeps ~26.4 GB resident and peaks at ~30.15 GB on large documents.
   - The tracker sends `keep_alive: 30m`, so the model stays loaded for 30 minutes after its last call. The nightly
     eval job unloads each model as soon as it has finished with it.

## 3. Why it is loud and hot

A dense 27B model reads all ~18 GB of its weights for **every token it writes**. The M4 Pro moves about 273 GB/s,
so the GPU runs flat out for the whole 1–4 minutes of each filing. Any local runtime has to do this work (Ollama,
llama.cpp, LM Studio, MLX, Hugging Face `transformers`): it loads the weights into memory and runs them on the GPU.
What changes the heat:

- **Active parameters per token.** An MoE model such as `gemma4:26b-a4b` (25B total, 3.8B active) reads only ~2–3 GB
  per token, so it finishes the same filing ~4–5× faster and the GPU is busy for much less time.
- **The runtime.** MLX (LM Studio, `mlx-lm`) is Apple's own framework. Published M4 Pro benchmarks show ~20% less GPU
  power and roughly 1.5–1.8× more tokens per watt than Ollama's llama.cpp backend for the same model. It is not silent,
  just more efficient.
- **When it runs.** Running it in one evening window instead of every 3 minutes all day (done, §4).
- **Optional:** macOS Low Power Mode (`sudo pmset -a lowpowermode 1`, or System Settings → Battery) caps the GPU
  clock. Jobs run slower but much cooler.

## 4. Local-LLM comparison week (2026-11-05 → 2026-11-13, 7 weekdays)

- During the day, filings are summarised by gpt-4.1 only, so the dashboard and alerts are unchanged.
- **17:00–21:00 on weekdays** (`com.equity-assistant.llm-eval`, `python -m tracker eval-nightly`): every results,
  press release, presentation and transcript filing from the day is also summarised by each local model in
  `llm.eval_models`. gpt-5.2 grades all the summaries blind against the filing's pages, and the night's report goes
  to `data/tracker/evals/<mon>-<d>-eval.md`. Anything not finished by 21:00 is picked up the next evening.
- **After 7 weekdays** (`com.equity-assistant.llm-eval-final`, `python -m tracker eval-final`): runs once and writes
  `data/tracker/evals/final-eval.md` with the overall table and the decision.
- Install / remove: `bash scripts/evening_schedule.sh install|uninstall|status|logs`.
