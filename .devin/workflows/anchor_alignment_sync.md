---
description: Anchor alignment sync — Parse anchor_alignment.md into JSON and calculate initial targets
---

## Anchor Alignment Sync

Use this workflow whenever the `anchor_alignment.md` file is manually updated.
It reads the markdown table and calculates the **Simple Relativeness** (`My Allocation %` / `Anchor Investment in Cr`). 

The output is a lightweight `.json` file that is then repeatedly used by the **Daily Run** (via the `anchor_momentum_rotation.py` script) to layer on latest price action and DMAs.

### Run Sync

```bash
cd /Users/pradeep.bansal1/Documents/learning/equity-assistant
/Users/pradeep.bansal1/miniconda3/envs/equity-assistant/bin/python scripts/_parse_anchor_alignment.py --user ZV3899
# For alternate account: --user WN5759
```

**What this does:**
1. Opens `portfolio/{user_id}/anchor_alignment.md`.
2. Extracts all 100+ holdings from the Anchor Table.
3. Computes the baseline mismatch to generate the top 30 Exit candidates and top 30 Entry candidates.
4. Saves to `portfolio/{user_id}/anchor_relativeness.json`.

*Note: Once this file is generated, the daily workflow `daily_run.md` (Step 6) will automatically calculate technicals (LTP vs 40-DMA vs 90-DMA) to give you actionable timing buckets like `Exit Immediately` or `Early Accumulation`.*
