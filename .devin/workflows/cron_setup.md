---
description: Set up cron jobs for daily portfolio run and earnings season automation
---

## Cron Job Setup

Installs two automated cron jobs:

| Job | Schedule | What it does |
|---|---|---|
| **Daily Run** | Mon-Fri 5:30pm IST | Portfolio report + watchlist alerts + news digest |
| **Earnings Season** | Mon-Sat 8pm IST (Jan-Feb, Apr-May, Jul-Aug, Oct-Nov) | Delta scan + DMA deployment + transcript fetch |

Gap months (Mar, Jun, Sep, Dec) — earnings season cron does NOT run.

---

### Install cron jobs

// turbo
```bash
cd /Users/pradeep.bansal1/Documents/learning/equity-assistant
bash scripts/cron_setup.sh
```

### Show current cron entries

// turbo
```bash
bash scripts/cron_setup.sh --show
```

### Remove cron jobs

```bash
bash scripts/cron_setup.sh --remove
```

---

### Important notes

1. **Daily cron needs Kite login first** — you must manually run the login step before the 5:30pm cron fires:
   ```bash
   python -m integrations.kite_connect --login --save-portfolio
   ```
   The cron only generates reports from the latest saved snapshot.

2. **Earnings season cron uses `--fetch`** — it will fetch new transcripts automatically (no Chrome CDP needed). For full data refresh including financials/shareholding, run manually with `--fetch-all` after launching Chrome debug.

3. **Logs** — saved to `logs/daily_cron_YYYY-MM-DD.log` and `logs/earnings_season_YYYY-MM-DD.log`

4. **Timezone** — cron times are local (IST). Adjust in `scripts/cron_setup.sh` if needed.
