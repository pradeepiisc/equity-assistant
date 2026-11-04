"""
Tracker CLI.

  python -m tracker init                 # exchange master data + universe (holdings, watchlist, notes)
  python -m tracker prices [--days 400]  # official NSE/BSE bhavcopy → prices (back-fills gaps)
  python -m tracker signals              # DMAs, phases, crossovers → events
  python -m tracker feeds                # NSE filings RSS (orders, fund raising, SAST, pledges…)
  python -m tracker deals                # NSE bulk / block deals
  python -m tracker shareholding         # quarterly shareholding history from Screener files
  python -m tracker notes                # index notes/*.md
  python -m tracker alerts [--no-push]   # raise alerts for new events, push macOS notifications
  python -m tracker docs [--seconds 600] [--no-llm]   # filings → PDFs → LLM extraction (daily budget)
  python -m tracker results              # quarterly numbers from NSE results XBRL
  python -m tracker bse                  # one due BSE-only company via Screener (Chrome debug session)
  python -m tracker tick [--force]       # one scheduler pass (runs only what is due; --force polls every feed)
  python -m tracker serve [--port 7788]  # dashboard + background polling (tick at start-up, then every 3 min)
  python -m tracker reread ID [ID …]     # read documents again with the current pipeline; rewrites their events
  python -m tracker status
"""
from __future__ import annotations

import argparse
import sys

from . import jobs


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m tracker", description="Stock tracker")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sp = sub.add_parser("prices")
    sp.add_argument("--days", type=int, default=None)
    sub.add_parser("signals")
    sub.add_parser("feeds")
    sub.add_parser("deals")
    sub.add_parser("shareholding")
    sub.add_parser("notes")
    sd = sub.add_parser("docs")
    sd.add_argument("--seconds", type=float, default=600)
    sd.add_argument("--no-llm", action="store_true", help="discover, download and prepare only")
    sub.add_parser("results")
    sub.add_parser("bse")
    sa = sub.add_parser("alerts")
    sa.add_argument("--no-push", action="store_true")
    st = sub.add_parser("tick")
    st.add_argument("--no-push", action="store_true")
    st.add_argument("--force", action="store_true", help="poll every feed now, ignoring cadence")
    st.add_argument("--source", default="cli", help=argparse.SUPPRESS)
    sv = sub.add_parser("serve")
    sv.add_argument("--port", type=int, default=None)
    sv.add_argument("--no-poll", action="store_true", help="serve pages only; don't poll in the background")
    sub.add_parser("status")
    sk = sub.add_parser("kite-daily", help="save today's holdings for every Kite account (the 16:00 job)")
    sk.add_argument("--force", action="store_true", help="also accounts already saved today")
    sv2 = sub.add_parser("valuations", help="write the valuation memo (analyst prompt) for held / watched companies")
    sv2.add_argument("--all", action="store_true", help="every held and watched company, not just stale ones")
    sv2.add_argument("--sid", action="append", help="only these companies (repeatable)")
    sv2.add_argument("--workers", type=int, default=4)
    st2 = sub.add_parser("transcripts", help="old concall transcripts: what has been read, and moving them to the Trash")
    st2.add_argument("--trash", action="store_true", help="move the read ones to the Trash (asks first)")
    sr = sub.add_parser("reread", help="read documents again with the current pipeline (OCR, tables as rows, number check)")
    sr.add_argument("ids", type=int, nargs="+", help="document ids")
    args = p.parse_args(argv)

    if args.cmd == "reread":
        import json as _json
        from . import config, db, extract
        settings = config.load_settings()
        with db.session() as conn:
            for i in args.ids:
                print(i, _json.dumps(extract.reread(conn, i, settings), ensure_ascii=False, indent=1))
        return 0
    if args.cmd == "kite-daily":
        from . import kite_daily
        import json as _json
        print(_json.dumps(kite_daily.attempt(force=args.force), indent=1))
        return 0
    if args.cmd == "valuations":
        from . import valuation
        print(valuation.run_batch(all_=args.all, sids=args.sid, workers=args.workers))
        return 0
    if args.cmd == "transcripts":
        from . import transcripts
        return transcripts.cli(trash=args.trash)
    if args.cmd == "serve":
        from .server import run
        run(args.port, poll=not args.no_poll)
        return 0
    fn = {
        "init": lambda: jobs.init(),
        "prices": lambda: jobs.prices_job(args.days),
        "signals": lambda: jobs.signals_job(),
        "feeds": lambda: jobs.feeds_job(),
        "deals": lambda: jobs.deals_job(),
        "shareholding": lambda: jobs.shareholding_job(),
        "notes": lambda: jobs.notes_job(),
        "alerts": lambda: jobs.alerts_job(push=not args.no_push),
        "docs": lambda: jobs.documents_job(max_seconds=args.seconds, llm=False if args.no_llm else None),
        "results": lambda: jobs.results_job(),
        "bse": lambda: jobs.bse_job(),
        "tick": lambda: jobs.tick(push=not args.no_push, source=args.source, force=args.force),
        "status": lambda: jobs.status(),
    }[args.cmd]
    ok = fn()
    return 0 if ok is not False else 1


if __name__ == "__main__":
    sys.exit(main())
