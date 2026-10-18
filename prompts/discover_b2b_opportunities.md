# Prompt — Paste this into a new Claude conversation

You have access to the `filesystem` MCP tool. The path `/Users/pradeep.bansal1/Documents/learning/equity-assistant/data/companies` contains folders for 100+ Indian listed companies. Each company folder has a `reports/transcript_analysis.md` file — an AI-generated deep summary of the company's last 4 earnings calls including management quotes, risks flagged, red flags, and guidance evolution.

## Your task

Go through every company's `transcript_analysis.md` file, reading them in batches of 5 using `read_multiple_files`. For each company, extract **real, structural business problems** that management has flagged — problems that a third-party B2B company could solve.

### Step 1: Read all companies

Start by listing all company directories:
```
filesystem:list_directory → /Users/pradeep.bansal1/Documents/learning/equity-assistant/data/companies
```

Then read transcript analyses in batches of 5:
```
filesystem:read_multiple_files → [
  "/Users/pradeep.bansal1/Documents/learning/equity-assistant/data/companies/{COMPANY}/reports/transcript_analysis.md",
  ...
]
```

Skip companies where the file doesn't exist and move to the next batch. Keep going until every company has been attempted.

### Step 2: Extract problems

For each company, look for problems in these sections of the transcript analysis:
- **Key Risks Flagged by Management** — what they openly admitted is hurting them
- **Red Flags** — what keeps getting deferred, what they sound stuck or frustrated about
- **Guidance vs Delivery misses** — what they promised but couldn't deliver, and WHY
- **Margin Commentary** — what's compressing margins that they can't fix internally
- **Guidance Evolution** — topics marked "deferred_repeatedly" signal chronic unsolved problems

### Step 3: Filter strictly

INCLUDE only problems that are:
- **Solvable by a third-party B2B company** — someone could start a business or consultancy to address this
- **Recurring** — mentioned across multiple quarters (strongest signal)
- **Operational, not macro** — supply chain, logistics, talent, testing, compliance, technology, working capital, hedging, certification, data gaps

EXCLUDE:
- Macro/cyclical: monsoon, recession, commodity super-cycles, currency depreciation
- Geopolitical: wars, tariffs, sanctions, Red Sea disruptions
- Pure competition: "too many players" (unless there's a specific operational dimension)
- Seasonality: "Q2 is always weak"
- Deliberate choices: "we chose margin over volume"
- Minor one-quarter blips that management resolved quickly

### Step 4: Cluster into opportunity themes

Group similar problems across companies into opportunity themes. A theme needs at least 2 companies facing the same problem to be worth noting. For each theme, provide:

1. **Opportunity title** — clear, specific (e.g., "Commodity Hedging Advisory for Mid-Cap Manufacturers" not "Financial Services")
2. **The problem** — 2-3 sentences describing what's broken, with specifics
3. **Companies facing it** — company name + the specific quote or evidence from their transcript analysis
4. **Severity signal** — how many companies, how many quarters it's been mentioned, is it getting worse?
5. **Who would pay** — which role at which type of company would buy this solution
6. **Business model sketch** — how a solver would charge (SaaS, per-transaction, retainer, per-unit)
7. **Lean start actions** — 3-5 concrete things someone could do in the next 30 days with minimal capital to validate and start on this opportunity:
   - What's the cheapest way to test if this problem is real beyond transcripts? (e.g., "call 5 CFOs of mid-cap manufacturers and ask about hedging")
   - What's the MVP? (e.g., "a spreadsheet-based hedging advisory for 3 pilot clients" not "build a platform")
   - What's the first revenue event? (e.g., "charge ₹50K/month retainer to 2 companies for manual commodity risk reports")
   - What existing infrastructure or tools can you piggyback on? (e.g., "use MCX data APIs + Google Sheets")
   - Who is the first hire or partner you'd need? (e.g., "a commodity trader with 5+ years MCX experience")

### Step 5: Rank and present

Rank opportunities by a combination of:
- **Number of companies** reporting this problem (breadth)
- **Recurrence** across quarters (depth / stickiness)  
- **Lean startability** — can someone start this in 30 days with <₹5L?
- **Revenue speed** — how fast can you get to first paying customer?

Present as a single comprehensive document. For each opportunity, use this structure:

```
## OPPORTUNITY [rank]: [Title]

**Problem:** [2-3 sentences]

**Evidence from transcripts:**
| Company | What they said | Quarters mentioned |
|---------|---------------|-------------------|

**Severity:** [X companies, recurring Y+ quarters, trend: worsening/stable]

**Who pays:** [Role + company type]

**Business model:** [How you charge]

**Lean start (30-day plan):**
1. Validate: [cheapest way to confirm the problem]
2. MVP: [minimum viable offering]  
3. First revenue: [what you sell to whom for how much]
4. Tools/infra: [what you piggyback on]
5. First hire/partner: [who you need]
```

### Important notes
- Read EVERY company. Don't stop at 30-40. Keep batching until all are done.
- Quote management directly when possible — their exact words are the evidence.
- If a problem is mentioned by only 1 company, still note it as a "single-signal" at the end — it might be an emerging theme.
- After presenting all opportunities, add a section called **"Opportunities I'd start tomorrow"** — pick the top 3 that have the best combination of: clear pain, multiple companies, lean startability, and fast path to revenue.
