import re
import sqlite3
from dataclasses import dataclass

from ingestion.parsers.base import Transaction

_PAYMENT_PATTERNS = re.compile(
    r"\b(payment|autopay|auto[\s\-]pay|thank[\s\-]you|online[\s\-]pmt|"
    r"mobile[\s\-]pmt|bill[\s\-]pay|ach[\s\-]credit|ach[\s\-]deposit|"
    r"ach[\s\-]debit[\s\-]payment|internet[\s\-]payment|direct[\s\-]debit)\b",
    re.IGNORECASE,
)


def is_card_payment(description: str) -> bool:
    return bool(_PAYMENT_PATTERNS.search(description))


@dataclass
class InsertResult:
    inserted: int
    payments_dropped: int


def insert_transactions(conn: sqlite3.Connection, transactions: list[Transaction]) -> InsertResult:
    """Insert all transactions. No deduplication — every row is stored as-is.

    Only card bill payments (amount > 0 + payment keywords) are dropped.
    Merchant refunds (amount > 0, no payment keywords) are kept.
    """
    inserted = 0
    payments_dropped = 0

    for t in transactions:
        if t.amount >= 0 and is_card_payment(t.description):
            payments_dropped += 1
            continue

        conn.execute(
            """INSERT INTO transactions
               (date, description, merchant, amount, account_id, source_category)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                t.date.isoformat(),
                t.description,
                t.merchant,
                t.amount,
                t.account_id,
                t.source_category,
            ),
        )
        inserted += 1

    conn.commit()
    return InsertResult(inserted=inserted, payments_dropped=payments_dropped)
