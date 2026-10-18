"""
Classify exchange filings into event types the dashboard and alerts understand.

NSE announcement descriptions end with "|SUBJECT: <SEBI LODR category>", e.g.
"Bagging/Receiving of orders/contracts", "Capacity addition", "Alteration Of Capital
and Fund Raising". The subject decides first; keywords in the text refine it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .util import money_to_crore


@dataclass
class Classified:
    type: str
    severity: str
    label: str
    value_cr: float | None = None
    data: dict = field(default_factory=dict)


def _rx(*words: str) -> re.Pattern:
    return re.compile("|".join(words), re.I)


ROUTINE = _rx(
    r"newspaper", r"certificate under", r"regulation\s*74", r"trading window", r"compliance certificate",
    r"loss of share certificate", r"duplicate share", r"issue of duplicate", r"\bBRSR\b",
    r"business responsibility", r"investor complaints", r"declaration of nav", r"record date for (interest|payment)",
    r"statement of investor", r"reg(ulation)?\s*40\s*\(?9", r"reg(ulation)?\s*7\s*\(?3\)?", r"transfer of shares",
    r"change in (registered|corporate) office address", r"e-?voting", r"scrutini[sz]er", r"postal ballot notice",
    r"secretarial compliance", r"annual secretarial", r"intimation of (the )?book closure",
)
ORDER = _rx(
    r"bagging/receiving of orders", r"\b(bag(s|ged|ging)?|receiv(e|ed|es|ing)|secur(e|ed|es|ing)|won|wins?|"
    r"award(ed|s)?|bags|clinch(ed|es)?|grab(bed|s)?|acquired)\b.{0,80}\b(orders?|contracts?|lo[ai]s?|"
    r"letters? of (award|intent|acceptance)|work orders?|purchase orders?|tenders?|mandates?)\b",
    r"\b(orders?|contracts?) (worth|valued|aggregating|amounting)\b",
    r"\blowest bidder\b|\bL-?1 bidder\b",
)
FUND = _rx(
    r"fund\s*rais", r"alteration of capital", r"preferential", r"qualified institution", r"\bQIP\b",
    r"rights issue", r"\bwarrants?\b", r"\bFCCBs?\b", r"private placement", r"further issue of",
    r"issue of (equity|convertible)", r"\bOFS\b|offer for sale", r"\bNCDs?\b", r"commercial paper",
)
ALLOT = _rx(r"allotment of", r"conversion of warrants")
CAPACITY = _rx(
    r"capacity (addition|expansion|enhancement|augmentation)", r"commenc\w*\s+(of\s+)?(commercial\s+)?"
    r"(production|operations?|manufacturing)", r"\bcommission(ed|ing)\b", r"new (plant|facility|unit|"
    r"manufacturing)", r"greenfield", r"brownfield", r"expansion", r"capex",
)
ACQ = _rx(r"acqui(re|red|ring|sition)", r"amalgamation", r"\bmerger\b", r"scheme of arrangement",
          r"joint venture", r"\bJV\b", r"strategic (investment|partnership|alliance)", r"demerger",
          r"incorporation of (a )?(wholly owned )?subsidiar", r"divest")
RESULTS = _rx(r"financial results?", r"integrated filing", r"outcome of board meeting.*result")
TRANSCRIPT = _rx(r"transcript")
CONCALL = _rx(r"analyst", r"investor(s)? meet", r"earnings call", r"conference call", r"con-?call",
              r"audio recording", r"investor presentation", r"video recording")
BOARD = _rx(r"board meeting")
RATING = _rx(r"credit rating", r"\brating\b")
MGMT = _rx(r"resign", r"cessation", r"appointment", r"re-?appoint", r"change in (management|directorate|kmp)")
AUDITOR = _rx(r"auditor")
TOP_ROLE = _rx(r"\bCFO\b", r"\bCEO\b", r"managing director", r"\bMD\b", r"chief financial", r"chief executive",
               r"whole[- ]time director", r"chairman", r"company secretary")
LITIGATION = _rx(r"litigation", r"actions initiated", r"orders passed", r"penalt", r"show cause", r"demand notice",
                 r"\bGST\b", r"income tax", r"\bsearch(es)?\b", r"\braid\b", r"SEBI order", r"insolvency", r"\bNCLT\b",
                 r"fraud", r"default")
CORP_ACTION = _rx(r"\bbonus\b", r"\bsplit\b", r"sub-?division", r"buy-?back", r"dividend", r"record date")
INSIDER = _rx(r"regulation\s*7\s*\(\s*2\s*\)", r"insider trading", r"\bPIT\b", r"\bSAST\b",
              r"regulation\s*(29|31)", r"encumbrance", r"pledge", r"substantial acquisition")
ORDER_BOOK = re.compile(
    r"order\s*book[^.]{0,60}?((?:rs\.?|inr|₹|usd|\$)\s*\d[\d,]*(?:\.\d+)?\s*(?:lakh\s*crores?|crores?|cr|"
    r"lakhs?|million|mn|billion|bn)?)", re.I)


def _meeting_date(text: str) -> str | None:
    m = re.search(r"(?:held on|meeting date:?)\s*(\d{1,2}-[A-Za-z]{3}-\d{4})", text or "", re.I)
    return m.group(1) if m else None


def split_subject(description: str) -> tuple[str, str]:
    """'text |SUBJECT: Category' → (text, Category)."""
    m = re.search(r"\|\s*SUBJECT\s*:\s*(.*)$", description or "", re.S)
    if not m:
        return (description or "").strip(), ""
    return description[: m.start()].strip(), m.group(1).strip()


def classify_announcement(description: str, *, sales_ttm_cr: float | None, high_pct: float,
                          usd_inr: float) -> Classified:
    text, subject = split_subject(description)
    s = subject.lower()
    blob = f"{subject} {text}"
    data: dict = {"subject": subject}
    m = ORDER_BOOK.search(text)
    if m:   # an order-book figure is context, not the value of this filing
        data["order_book_cr"] = money_to_crore(m.group(1), usd_inr)
        value = money_to_crore(text[: m.start(1)] + text[m.end(1):], usd_inr)
    else:
        value = money_to_crore(text, usd_inr)

    if ROUTINE.search(blob) and not (ORDER.search(text) or FUND.search(text)):
        return Classified("routine", "info", "Routine filing", None, data)

    if "bagging/receiving" in s or ORDER.search(text):
        sev = "medium"
        if value is not None and sales_ttm_cr:
            pct = value / sales_ttm_cr * 100
            data["pct_of_revenue"] = round(pct, 2)
            if pct >= high_pct:
                sev = "high"
        elif value is not None and value >= 100:
            sev = "high"
        return Classified("order_win", sev, "Order win", value, data)

    if "board meeting" in s and "outcome" not in s:
        purpose = text.lower()
        data["meeting_date"] = _meeting_date(text)
        if FUND.search(purpose):
            return Classified("board_meeting", "high", "Board meeting: fund raising", None, data)
        if re.search(r"bonus|split|sub-?division|buy-?back", purpose):
            return Classified("board_meeting", "medium", "Board meeting: bonus / split / buyback", None, data)
        if RESULTS.search(purpose) or "result" in purpose:
            return Classified("board_meeting", "low", "Board meeting: results", None, data)
        return Classified("board_meeting", "low", "Board meeting", None, data)

    if "fund raising" in s or "alteration of capital" in s or FUND.search(text):
        sub = ("preferential" if re.search(r"preferential", blob, re.I) else
               "qip" if re.search(r"qualified institution|\bQIP\b", blob, re.I) else
               "rights" if re.search(r"rights issue", blob, re.I) else
               "warrants" if re.search(r"warrant", blob, re.I) else
               "debt" if re.search(r"\bNCD|debenture|commercial paper", blob, re.I) else "equity")
        data["instrument"] = sub
        sev = "medium" if sub == "debt" else "high"
        return Classified("fund_raising", sev, f"Fund raising ({sub})", value, data)

    if ALLOT.search(blob):
        esop = bool(re.search(r"\bESO[PS]\b|employee stock", blob, re.I))
        return Classified("allotment", "low" if esop else "medium",
                          "ESOP allotment" if esop else "Share / warrant allotment", value, data)

    if INSIDER.search(blob):
        pledge = bool(re.search(r"pledge|encumbrance|regulation\s*31", blob, re.I))
        return Classified("pledge" if pledge else "insider_disclosure", "high",
                          "Promoter pledge / encumbrance" if pledge else "Insider / SAST disclosure", value, data)

    if "capacity addition" in s or CAPACITY.search(text):
        return Classified("capacity", "medium", "Capacity / expansion", value, data)

    if "acquisition" in s or "restructuring" in s or ACQ.search(text):
        return Classified("acquisition", "medium", "Acquisition / restructuring", value, data)

    if RESULTS.search(blob):
        return Classified("results", "medium", "Financial results", None, data)

    if TRANSCRIPT.search(blob):
        return Classified("concall_transcript", "medium", "Concall transcript", None, data)

    if CONCALL.search(blob):
        return Classified("concall", "low", "Analyst / investor meet", None, data)

    if BOARD.search(blob):
        purpose = text.lower()
        data["meeting_date"] = _meeting_date(text)
        if FUND.search(purpose):
            return Classified("board_meeting", "high", "Board meeting: fund raising", None, data)
        if re.search(r"bonus|split|sub-?division|buy-?back", purpose):
            return Classified("board_meeting", "medium", "Board meeting: bonus / split / buyback", None, data)
        return Classified("board_meeting", "low", "Board meeting", None, data)

    if RATING.search(blob):
        direction = ("upgrade" if re.search(r"upgrad", blob, re.I) else
                     "downgrade" if re.search(r"downgrad", blob, re.I) else "")
        data["direction"] = direction
        return Classified("credit_rating", "high" if direction == "downgrade" else "medium",
                          f"Credit rating{(' ' + direction) if direction else ''}", None, data)

    if MGMT.search(blob):
        resign = bool(re.search(r"resign|cessation", blob, re.I))
        if resign and AUDITOR.search(blob):
            return Classified("auditor_resignation", "high", "Auditor resignation", None, data)
        if resign and TOP_ROLE.search(blob):
            return Classified("kmp_resignation", "high", "Key managerial resignation", None, data)
        return Classified("management_change", "low", "Management change", None, data)

    if "litigation" in s or "actions initiated" in s or LITIGATION.search(blob):
        return Classified("litigation", "medium", "Litigation / regulatory action", value, data)

    if CORP_ACTION.search(blob):
        big = bool(re.search(r"bonus|split|sub-?division|buy-?back", blob, re.I))
        return Classified("corporate_action", "medium" if big else "low",
                          "Bonus / split / buyback" if big else "Dividend / record date", None, data)

    return Classified("update", "low", subject or "Company update", value, data)
