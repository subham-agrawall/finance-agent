import sqlite3
from pathlib import Path

import yaml
from rapidfuzz import process, fuzz

from ingestion.normalizer import STANDARD_CATEGORIES, normalize_bank_category
from storage.db import get_cached_category, upsert_merchant_cache

_RULES_PATH = Path(__file__).parent.parent / "config" / "attribution_rules.yaml"
_SETTINGS_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"
_FUZZY_THRESHOLD = 85


def _use_llm() -> bool:
    try:
        import yaml as y
        with open(_SETTINGS_PATH) as f:
            return y.safe_load(f).get("use_llm", True)
    except Exception:
        return False


def _load_rules() -> dict:
    with open(_RULES_PATH) as f:
        data = yaml.safe_load(f) or {}
        return data.get("rules", {})


def categorize(conn: sqlite3.Connection, tx_id: int, merchant: str, source_category: str | None) -> str:
    """Determine and persist the category for a transaction.

    Priority:
    1. Bank-provided category (mapped to standard list)
    2. Attribution rules (merchant lookup)
    3. SQLite merchant cache (prior LLM or manual result)
    4. LLM (only if use_llm=True and merchant passes safety check)
    5. Fallback: 'uncategorized'
    """
    category = None

    # 1. Bank-provided category
    if source_category:
        category = normalize_bank_category(source_category)

    # 2. Merchant rule
    if not category:
        rules = _load_rules()
        rule = _find_matching_rule(merchant.upper(), rules)
        if rule and rule.get("category") in STANDARD_CATEGORIES:
            category = rule["category"]

    # 3. Merchant cache
    if not category:
        category = get_cached_category(conn, merchant)

    # 4. LLM
    if not category and _use_llm():
        from llm.client import classify_merchant
        category = classify_merchant(merchant)
        if category:
            upsert_merchant_cache(conn, merchant, category, source="llm")

    category = category or "uncategorized"

    conn.execute("UPDATE transactions SET category=? WHERE id=?", (category, tx_id))
    conn.commit()
    return category


def _find_matching_rule(merchant_upper: str, rules: dict) -> dict | None:
    if not rules:
        return None
    rule_keys = list(rules.keys())
    match = process.extractOne(
        merchant_upper, rule_keys, scorer=fuzz.token_set_ratio, score_cutoff=_FUZZY_THRESHOLD
    )
    if match:
        return rules[match[0]]
    return None


def apply_correction(conn: sqlite3.Connection, tx_id: int, merchant: str, category: str) -> None:
    """Save a manual category correction to both the DB and the merchant cache."""
    conn.execute("UPDATE transactions SET category=?, reviewed=1 WHERE id=?", (category, tx_id))
    conn.commit()
    upsert_merchant_cache(conn, merchant, category, source="manual")
