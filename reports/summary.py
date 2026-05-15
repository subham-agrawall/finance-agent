import sqlite3
from pathlib import Path

import pandas as pd
import yaml

_ACCOUNTS_PATH = Path(__file__).parent.parent / "config" / "accounts.yaml"


def get_monthly_summary(
    conn: sqlite3.Connection,
    start: str,
    end: str,
    account_ids: list[str] | None = None,
    review_status: str = "All",
) -> pd.DataFrame:
    """Return Category | Total Spent | Sub's Share | Adsy's Share | Other.

    start / end: 'YYYY-MM-DD' strings; end is exclusive.
    account_ids=None means all accounts.
    """
    query = """
        SELECT category, amount, person, is_group,
               split_ratio, COALESCE(adsy_ratio, 1.0 - split_ratio) AS adsy_ratio
        FROM transactions
        WHERE date >= ? AND date < ? AND amount < 0
    """
    params: list = [start, end]
    if account_ids:
        query += f" AND account_id IN ({','.join('?' * len(account_ids))})"
        params.extend(account_ids)
    if review_status == "Reviewed":
        query += " AND reviewed = 1"
    elif review_status == "Unreviewed":
        query += " AND reviewed = 0"

    df = pd.read_sql_query(query, conn, params=params)

    if df.empty:
        return pd.DataFrame(
            columns=["Category", "Total Spent", "Sub's Share", "Adsy's Share", "Other"]
        )

    df["expense"] = df["amount"].abs()

    def _shares(row):
        exp = row["expense"]
        if row["is_group"] == 0:
            return pd.Series({
                "sub_s":   exp if row["person"] == "Sub"   else 0.0,
                "adsy_s":  exp if row["person"] == "Adsy"  else 0.0,
                "other_s": exp if row["person"] == "other" else 0.0,
            })
        sub  = exp * row["split_ratio"]
        adsy = exp * row["adsy_ratio"]
        return pd.Series({"sub_s": sub, "adsy_s": adsy, "other_s": max(0.0, exp - sub - adsy)})

    shares = df.apply(_shares, axis=1)
    df = pd.concat([df, shares], axis=1)

    return (
        df.groupby("category", as_index=False)
        .agg(total_spent=("expense", "sum"),
             sub_share=("sub_s", "sum"),
             adsy_share=("adsy_s", "sum"),
             other_share=("other_s", "sum"))
        .sort_values("total_spent", ascending=False)
        .rename(columns={"category": "Category", "total_spent": "Total Spent",
                         "sub_share": "Sub's Share", "adsy_share": "Adsy's Share",
                         "other_share": "Other"})
    )


def get_balance(
    conn: sqlite3.Connection,
    start: str,
    end: str,
    account_ids: list[str] | None = None,
    review_status: str = "All",
) -> dict:
    """Settlement balance. Positive → Adsy owes Sub. Negative → Sub owes Adsy.

    Assumes whoever's card the transaction is on paid the full amount.
    """
    with open(_ACCOUNTS_PATH) as f:
        accounts = yaml.safe_load(f).get("accounts", {})
    account_owner = {k: v.get("person") for k, v in accounts.items()}

    query = """
        SELECT amount, person, split_ratio, is_group, account_id,
               COALESCE(adsy_ratio, 1.0 - split_ratio) AS adsy_ratio
        FROM transactions
        WHERE date >= ? AND date < ? AND amount < 0
    """
    params: list = [start, end]
    if account_ids:
        query += f" AND account_id IN ({','.join('?' * len(account_ids))})"
        params.extend(account_ids)
    if review_status == "Reviewed":
        query += " AND reviewed = 1"
    elif review_status == "Unreviewed":
        query += " AND reviewed = 0"

    df = pd.read_sql_query(query, conn, params=params)
    if df.empty:
        return {"balance": 0.0, "sub_paid_for_adsy": 0.0, "adsy_paid_for_sub": 0.0}

    df["expense"] = df["amount"].abs()

    # Shared expenses: person = card owner, use split ratios
    shared    = df[df["is_group"] == 1]
    sub_paid_for_adsy = float((shared[shared["person"] == "Sub"]["expense"]  * shared[shared["person"] == "Sub"]["adsy_ratio"]).sum())
    adsy_paid_for_sub = float((shared[shared["person"] == "Adsy"]["expense"] * shared[shared["person"] == "Adsy"]["split_ratio"]).sum())

    # Personal cross-account: e.g. Adsy's card charged but 100% Sub's expense
    personal = df[df["is_group"] == 0].copy()
    personal["card_owner"] = personal["account_id"].map(account_owner)
    sub_paid_for_adsy += float(personal[(personal["card_owner"] == "Sub")  & (personal["person"] == "Adsy")]["expense"].sum())
    adsy_paid_for_sub += float(personal[(personal["card_owner"] == "Adsy") & (personal["person"] == "Sub")]["expense"].sum())

    return {
        "balance":           round(sub_paid_for_adsy - adsy_paid_for_sub, 2),
        "sub_paid_for_adsy": round(sub_paid_for_adsy, 2),
        "adsy_paid_for_sub": round(adsy_paid_for_sub, 2),
    }
