import sqlite3
from pathlib import Path

import yaml
from rapidfuzz import process, fuzz

_ACCOUNTS_PATH = Path(__file__).parent.parent / "config" / "accounts.yaml"
_RULES_PATH    = Path(__file__).parent.parent / "config" / "attribution_rules.yaml"
_SPLITS_PATH   = Path(__file__).parent.parent / "config" / "split_ratios.yaml"

_GROUP_ELIGIBLE = {"dining", "groceries", "utilities", "subscriptions", "rent", "travel", "vacation", "entertainment"}
_FUZZY_THRESHOLD = 85


def _load_accounts() -> dict:
    with open(_ACCOUNTS_PATH) as f:
        return yaml.safe_load(f).get("accounts", {})


def _load_rules() -> dict:
    with open(_RULES_PATH) as f:
        data = yaml.safe_load(f) or {}
        return data.get("rules", {})


def _load_splits() -> dict:
    with open(_SPLITS_PATH) as f:
        return yaml.safe_load(f) or {}


def attribute_transaction(
    conn: sqlite3.Connection,
    tx_id: int,
    merchant: str,
    account_id: str,
    category: str | None,
) -> dict:
    accounts = _load_accounts()
    rules    = _load_rules()
    splits   = _load_splits()

    account_info = accounts.get(account_id, {})
    base_person  = account_info.get("person", "Sub")

    # Look up compound rule: merchant (fuzzy) × card owner
    rule = _find_matching_rule(merchant.upper(), base_person, rules)

    if rule:
        person      = rule.get("person", base_person)
        split_ratio = rule.get("split_ratio", splits.get(category or "", 1.0))
        is_group    = split_ratio < 1.0
    elif base_person == "joint":
        person      = "Sub"
        split_ratio = splits.get(category or "", 0.5)
        is_group    = True
    else:
        person       = base_person
        default_split = splits.get(category or "", 1.0)
        if category in _GROUP_ELIGIBLE:
            split_ratio = default_split
            is_group    = split_ratio < 1.0
        else:
            split_ratio = 1.0
            is_group    = False

    adsy_ratio = 1.0 - split_ratio
    conn.execute(
        "UPDATE transactions SET person=?, is_group=?, split_ratio=?, adsy_ratio=? WHERE id=?",
        (person, int(is_group), split_ratio, adsy_ratio, tx_id),
    )
    conn.commit()
    return {"person": person, "is_group": is_group, "split_ratio": split_ratio}


def _find_matching_rule(merchant_upper: str, card_owner: str, rules: dict) -> dict | None:
    """Fuzzy-match merchant name, then look up card_owner sub-key."""
    if not rules:
        return None

    merchant_keys = list(rules.keys())
    match = process.extractOne(
        merchant_upper, merchant_keys,
        scorer=fuzz.token_set_ratio, score_cutoff=_FUZZY_THRESHOLD,
    )
    if not match:
        return None

    merchant_rule = rules[match[0]]

    # Only return a rule if this specific card owner has one — no cross-person fallback
    if isinstance(merchant_rule, dict) and card_owner in merchant_rule:
        rule = merchant_rule[card_owner]
        if isinstance(rule, dict):
            return rule

    return None


def save_rule(merchant: str, card_owner: str, category: str | None, split_ratio: float) -> None:
    """Write a compound merchant × card_owner rule to attribution_rules.yaml."""
    with open(_RULES_PATH) as f:
        data = yaml.safe_load(f) or {}

    rules = data.get("rules", {})
    key   = merchant.upper().strip()

    if key not in rules or not isinstance(rules[key], dict):
        rules[key] = {}

    inner = {"split_ratio": split_ratio}
    if category:
        inner["category"] = category
    rules[key][card_owner] = inner
    data["rules"] = rules

    with open(_RULES_PATH, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
