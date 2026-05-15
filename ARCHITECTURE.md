# Architecture Decision Record — Finance Agent

> Living document. Revisit any section when requirements change.

---

## What This App Does

A local tool for Sub and Adsy to reconcile shared finances. It ingests bank and credit card statements, assigns each transaction to a person (Sub / Adsy / other), categorizes spending, and produces a monthly summary with a settlement balance showing who owes whom.

---

## Prerequisites

### To Run
- Python 3.11+
- Dependencies installed into a virtual environment: `uv venv .venv && uv pip install -r requirements.txt`
- `OPENAI_API_KEY` set in the environment (only required if `use_llm: true` in `config/settings.yaml`)
- Run with: `source .venv/bin/activate && streamlit run app.py`

### To Develop
- `uv pip install pytest` for tests → `python -m pytest tests/ -v`
- No build step, no compilation — edit a `.py` file and Streamlit hot-reloads on save
- SQLite DB lives at `data/finance.db` (gitignored); delete it to start fresh

### External Dependencies
| Dependency | Purpose | Required? |
|---|---|---|
| `streamlit` | UI framework | Yes |
| `pandas` | CSV parsing, data manipulation, report aggregation | Yes |
| `pdfplumber` | PDF text and table extraction (runs fully local) | Yes |
| `openai` | Merchant categorization fallback | No — disable via `use_llm: false` |
| `rapidfuzz` | Fuzzy merchant name matching against rule keys | Yes |
| `PyYAML` | Reading/writing config files | Yes |
| `openpyxl` | Future Excel export support | Optional |

---

## Functional Requirements

### FR-01 — Statement Ingestion
- Accept CSV or PDF files via drag-and-drop upload in the UI
- Support Chase (CC + checking), US Bank (checking), Discover (CC), Apple Card (CC)
- Normalize all formats into a single schema: `{date, description, merchant, amount, account_id, source_category}`
- Amount sign convention: **negative = expense, positive = credit/refund** across all institutions

### FR-02 — Deduplication
- Re-uploading the same statement must not create duplicate rows
- Uniqueness key: `(date, description, amount, account_id)`
- Show count of inserted vs skipped rows after each upload

### FR-03 — Attribution
- Every transaction must be assigned to exactly one of: `Sub`, `Adsy`, `other`
- Primary signal: which account the transaction came from (`config/accounts.yaml`)
- Override signal: merchant-level rules in `config/attribution_rules.yaml`
- Transactions on shared/group-eligible categories automatically get a split ratio applied

### FR-04 — Categorization
- Every transaction must be assigned one of 10 standard categories: `dining`, `travel`, `groceries`, `rent`, `rides`, `entertainment`, `health`, `shopping`, `utilities`, `subscriptions`
- Uncategorized is a valid fallback state, not an error
- Bank-provided categories (Apple Card, Discover) are used where available
- LLM is called as last resort; result is cached permanently per merchant name

### FR-05 — Manual Review
- All transactions must be reviewable and correctable in one UI view (no nested clicks to open)
- Editable fields per transaction: person, category, Sub's split ratio
- One Save button per row; saving writes a permanent merchant rule so the correction applies to future uploads automatically

### FR-06 — Monthly Summary
- Show total spending broken down by category × person for any selected month
- Separate columns for: Sub individual, Adsy individual, Sub's share of shared, Adsy's share of shared
- Show settlement balance: who owes whom and by how much

### FR-07 — Privacy
- No transaction amounts, dates, or account identifiers may be sent to any external API
- Only sanitized merchant name tokens (e.g. `"UBER EATS"`) may leave the machine
- All processing except LLM categorization runs fully offline

---

## Non-Functional Requirements

### NFR-01 — Data Privacy (Hard Constraint)
- Financial data stays local. The only external call is `POST /v1/chat/completions` with a merchant name token and a list of category names — no amounts, no dates, no account info, no descriptions.
- Enforced in code at two layers: `extract_merchant()` in `normalizer.py` strips descriptions before storage; `is_safe_merchant()` in `sanitizer.py` validates before any API call.

### NFR-02 — Correctness Over Automation
- An uncategorized or unattributed transaction is preferable to a silently wrong one.
- When attribution is uncertain (e.g. joint account, no matching merchant rule), the transaction is flagged for manual review — the system does not guess.
- LLM results are one word from a fixed list; anything outside that list is treated as a miss, not used.

### NFR-03 — Transparency
- Every decision the system makes (attribution, category, split) can be traced to either a config file entry or a DB cache entry — no black-box behaviour.
- `config/attribution_rules.yaml` is human-readable and hand-editable. The Rules tab in the UI exposes it directly.

### NFR-04 — Performance
- Upload + parse + attribute + categorize a 50-transaction statement: under 5 seconds without LLM, under 30 seconds with LLM (first run; subsequent runs hit the cache).
- Summary and balance queries: under 1 second for up to 12 months of data (~600 transactions).

### NFR-05 — Resilience to Bank Format Changes
- When a bank changes its CSV column layout, the failure is loud and immediate (parse error on upload), not silent (wrong data stored silently).
- Each parser reads only the columns it expects; unexpected columns are ignored.

### NFR-06 — Portability
- The entire app state is two files: `data/finance.db` and `config/attribution_rules.yaml`. Copy these two files to another machine and the app resumes with full history and learned rules intact.
- No environment-specific paths, no cloud state.

### NFR-07 — Testability
- All parsing, deduplication, attribution, and categorization logic is in plain Python functions with no Streamlit dependency — fully unit-testable.
- Tests live in `tests/` and cover: merchant normalizer, each institution's CSV parser, PDF parser against a real Chase statement, dedup constraint, and category normalization.

---

## ADR-001 — Local Streamlit App, Not a Web Service

**Decision:** Run as a local Streamlit app (`streamlit run app.py`).

**Why:** This handles personal financial data. A hosted service introduces auth, secret management, cloud storage, and data-at-rest concerns. Running locally means data never leaves the machine — only merchant name tokens ever transit the network (to OpenAI).

**Trade-off:** No mobile access, no sharing across devices without syncing the SQLite file manually.

---

## ADR-002 — SQLite as the Database

**Decision:** Single SQLite file at `data/finance.db` (gitignored).

**Why:** Zero infrastructure. The dataset (a few thousand transactions per year) fits trivially in SQLite. Schema migrations are lightweight. The file is portable — copy it between machines to move all data.

**Trade-off:** No concurrent writers. Fine for a two-person personal tool; would need Postgres for multi-user or high-volume scenarios.

**Schema:**

```
transactions
  id, date, description, merchant, amount, account_id
  person, is_group, split_ratio, category, source_category
  reviewed, created_at
  UNIQUE(date, description, amount, account_id)   ← dedup on re-upload

merchant_cache
  merchant_name (PK), category, source, created_at
```

---

## ADR-003 — Per-Institution CSV Parsers, Not a Generic Parser

**Decision:** One parser class per institution in `ingestion/parsers/`, all inheriting `StatementParser`.

**Why:** Chase, Discover, Apple Card, US Bank all have different column names, date formats, and sign conventions (Chase CC: negative = expense; Discover/Apple: positive = expense). A generic parser would need heuristics for all of these and would be harder to debug when a bank changes its export format.

**Current institutions:**
| Account | Parser | Person | Sign convention |
|---|---|---|---|
| Chase Sapphire (Sub) | `chase.py` | Sub | negative = expense |
| Chase Freedom Unlimited (Sub) | `chase.py` | Sub | negative = expense |
| Chase Checking (Sub) | `chase.py` | Sub | negative = expense |
| US Bank Checking (Adsy) | `us_bank.py` | Adsy | negative = expense |
| Discover (Adsy) | `discover.py` | Adsy | positive = expense → flipped |
| Apple Card (Adsy) | `apple_card.py` | Adsy | positive = expense → flipped |

**Adding a new institution:** create `ingestion/parsers/<name>.py`, add to `INSTITUTION_PARSERS` in `app.py`, add account to `config/accounts.yaml`.

---

## ADR-004 — PDF Parsing: Text-Line Regex First, LLM Fallback

**Decision:** For PDFs, parse raw text lines with regex before trying tables or LLM.

**Why:** Chase PDF statements embed transaction data as plain text lines in the format `MM/DD DESCRIPTION AMOUNT`. The `pdfplumber` table extractor finds nothing useful (no table structure), but a `MM/DD .+ -?[\d,]+\.\d{2}` regex on each line gets all 51 transactions reliably. LLM fallback only fires if regex + table extraction together produce fewer than 3 transactions.

**Known quirk:** Chase PDFs use doubled characters in section headers (`AACCCCOOUUNNTT AACCTTIIVVIITTYY`). We do not gate on finding the "ACCOUNT ACTIVITY" header — we scan all lines and rely on `PURCHASE` / `PAYMENTS AND OTHER CREDITS` section headers + the regex pattern to identify transactions.

**Year detection:** Extracted from `Opening/Closing Date MM/DD/YY` or `April 2026`-style headers in the statement text.

---

## ADR-005 — LLM Used Only for Merchant Categorization, Not Core Logic

**Decision:** OpenAI API (`gpt-4o-mini`) is called only when a merchant name cannot be categorized any other way.

**Categorization priority (in order):**
1. Bank-provided category column (Apple Card and Discover include one) → mapped to standard list
2. `attribution_rules.yaml` — merchant rules written by manual corrections
3. `merchant_cache` SQLite table — prior LLM or manual result
4. OpenAI API — last resort; result is cached immediately so the same merchant is never called twice
5. `uncategorized` — if LLM also fails or is disabled

**LLM can be disabled** via `use_llm: false` in `config/settings.yaml`. Unknown merchants go to `uncategorized` for manual tagging.

---

## ADR-006 — Privacy: LLM Never Sees Financial Data

**Decision:** The only text sent to OpenAI is a sanitized merchant name token — never amounts, dates, account IDs, card numbers, or descriptions.

**How it works:**
- `ingestion/normalizer.py → extract_merchant()` strips the raw description to a clean merchant token (e.g. `"UBER* EATS 1234 SAN FRANCISCO CA"` → `"UBER EATS"`) before the token is stored or sent anywhere.
- `llm/sanitizer.py → is_safe_merchant()` validates the token contains no digits or dollar amounts before it reaches the API.
- For PDF LLM fallback: `scrub_pdf_text()` masks card numbers and dollar amounts before the text is sent.

**What OpenAI receives:** `"Merchant: UBER EATS\nCategories: dining, travel, ...\nReply with one category word only."`

---

## ADR-007 — Attribution: Account Ownership First, Merchant Rules Second

**Decision:** Assign `person` (Sub / Adsy / other) by looking up the account in `config/accounts.yaml` first, then checking `config/attribution_rules.yaml` for a merchant-level override.

**Flow:**
```
account_id → accounts.yaml → base person (Sub or Adsy)
     ↓
merchant name → attribution_rules.yaml (fuzzy match, threshold 85%) → override?
     ↓
category in group-eligible list? → apply split_ratio from split_ratios.yaml
     ↓
is_group = True if split_ratio < 1.0
```

**Group-eligible categories** (get a split ratio applied by default): dining, groceries, utilities, subscriptions, rent, travel, entertainment.

**Individual by default:** rides, health, shopping, uncategorized.

---

## ADR-008 — Learning from Manual Review via Persistent Rules

**Decision:** When a transaction is corrected in the Review tab, write a rule to `config/attribution_rules.yaml` and update `merchant_cache`. No ML model, no retraining.

**Why:** The simplest mechanism that actually gets smarter. A rule file is transparent (you can read and edit it), deterministic (same merchant → same result forever), and free. An ML classifier would need hundreds of labeled examples before it outperforms a well-curated rule file.

**How rules grow:**
- Correction saved → `attribution_rules.yaml` gets `MERCHANT_NAME: {person, category, split_ratio}`
- Next upload with same merchant → rule fires, no LLM call, no review flag

---

## ADR-009 — Deduplication via Unique Constraint, Not File Hashing

**Decision:** `UNIQUE(date, description, amount, account_id)` constraint in SQLite. Re-uploading the same statement skips already-present rows silently.

**Why:** File hashing (SHA-256) was considered but some bank PDFs embed a timestamp on every download, making the same statement produce a different hash. Transaction-level uniqueness is more robust.

**Trade-off:** Two genuinely identical charges on the same day at the same merchant for the same amount would be collapsed to one. This is accepted as a rare edge case.

---

## ADR-010 — Split Ratios: Category Defaults, Per-Merchant Overrides

**Decision:** Default split ratio (Sub's share) is set per spending category in `config/split_ratios.yaml`. Individual merchants can override via `attribution_rules.yaml`.

**Current defaults:**

| Category | Sub's share | Rationale |
|---|---|---|
| dining | 0.5 | Most dinners shared |
| groceries | 0.5 | Household |
| utilities | 0.5 | Household |
| subscriptions | 0.5 | Household |
| rent | 0.5 | Household |
| travel | 0.5 | Usually together |
| entertainment | 0.5 | Usually together |
| rides | 1.0 | Individual |
| health | 1.0 | Individual |
| shopping | 1.0 | Individual |

**Balance calculation:**
```
balance = Σ(Adsy's share of expenses Sub paid) − Σ(Sub's share of expenses Adsy paid)
positive → Adsy owes Sub
negative → Sub owes Adsy
```

---

## Open Questions / Future Decisions

| # | Question | Current stance |
|---|---|---|
| 1 | Add Bilt CC for Sub? | Removed for now; add back via accounts.yaml + parser |
| 2 | Export to Google Sheets or Notion? | Not implemented; CSV export from Summary tab would be the next step |
| 3 | What if a bank changes its CSV column names? | Parser breaks visibly; fix by updating the relevant parser class |
| 4 | Handle foreign currency transactions? | Currently stored at USD equivalent; currency exchange lines in PDFs are skipped |
| 5 | Multi-month trend view? | Summary tab is month-by-month; a trends tab would aggregate across months |
