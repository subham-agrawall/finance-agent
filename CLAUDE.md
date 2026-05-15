# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the app
streamlit run app.py

# Run tests
python -m pytest tests/ -v

# Run a single test file
python -m pytest tests/test_parsers.py -v
```

Set `ANTHROPIC_API_KEY` in the environment before running if LLM categorization is enabled (`use_llm: true` in `config/settings.yaml`).

## Architecture

The app is a local Streamlit tool for two people (user + partner) to reconcile shared finances. It has four tabs: Upload, Review, Summary, Rules.

**Data flow:**
1. `ingestion/parsers/` — per-institution CSV parsers (Chase, US Bank, Discover, Apple Card, Bilt) → all output `Transaction` dataclass from `ingestion/parsers/base.py`
2. `ingestion/normalizer.py` — `extract_merchant()` strips a raw bank description down to a clean merchant token (e.g., `"UBER* EATS 1234 SF CA"` → `"UBER EATS"`). This is the only text that ever reaches the LLM.
3. `processing/deduplicator.py` — inserts into SQLite, skips on `(date, description, amount, account_id)` collision
4. `processing/categorizer.py` — category priority: bank source category → attribution rule → SQLite merchant cache → LLM → `"uncategorized"`
5. `processing/attributor.py` — person/group assignment: account owner → merchant rule (fuzzy match via rapidfuzz) → joint-account flag for review
6. `reports/summary.py` — monthly category × person table + settlement balance

**Config files (edit these to customize):**
- `config/accounts.yaml` — account registry mapping account IDs to person + institution
- `config/attribution_rules.yaml` — merchant → `{person, category, split_ratio}`; grows automatically from Review tab corrections
- `config/split_ratios.yaml` — default per-category split (e.g., `dining: 0.5`)
- `config/settings.yaml` — `use_llm`, `pdf_llm_fallback`, `llm_model`, `owner_names`

**Storage:** SQLite at `data/finance.db` (gitignored). Two tables: `transactions` and `merchant_cache`.

## Privacy Constraint

The LLM (Anthropic API) only ever receives sanitized merchant tokens — never amounts, dates, account IDs, descriptions, or card numbers. `llm/sanitizer.py` enforces this before any API call. The scrubbing runs in `extract_merchant()` (normalizer) and `scrub_pdf_text()` (sanitizer). Do not change this without explicit intent.

## Adding a New Institution

1. Create `ingestion/parsers/<institution>.py` inheriting `StatementParser`
2. Add the institution key to `INSTITUTION_PARSERS` in `app.py`
3. Add accounts to `config/accounts.yaml`
