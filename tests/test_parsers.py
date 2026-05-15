import csv
import io
import tempfile
from pathlib import Path

import pytest

from ingestion.parsers.apple_card import AppleCardParser
from ingestion.parsers.bilt import BiltParser
from ingestion.parsers.chase import ChaseParser
from ingestion.parsers.discover import DiscoverParser
from ingestion.parsers.us_bank import USBankParser
from ingestion.normalizer import extract_merchant


def _write_csv(rows: list[dict], fieldnames: list[str]) -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False)
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    f.close()
    return f.name


# ── Normalizer ────────────────────────────────────────────────────────────────

def test_extract_merchant_strips_noise():
    assert extract_merchant("UBER* EATS 1234 SAN FRANCISCO CA") == "UBER EATS"

def test_extract_merchant_strips_card_numbers():
    result = extract_merchant("AMZN MKTP US 1234-5678-9012-3456")
    assert "1234" not in result

def test_extract_merchant_strips_amounts():
    result = extract_merchant("STARBUCKS $5.75 STORE 123")
    assert "$" not in result

def test_extract_merchant_fallback_on_empty():
    result = extract_merchant("")
    assert result == ""


# ── Chase parser ──────────────────────────────────────────────────────────────

def test_chase_credit_parser():
    path = _write_csv(
        [{"Transaction Date": "01/15/2024", "Post Date": "01/16/2024",
          "Description": "UBER EATS", "Category": "Food & Drink",
          "Type": "Sale", "Amount": "-24.50", "Memo": ""}],
        ["Transaction Date", "Post Date", "Description", "Category", "Type", "Amount", "Memo"],
    )
    txns = ChaseParser("chase_cc_you").parse(path)
    assert len(txns) == 1
    assert txns[0].amount == -24.50
    assert txns[0].account_id == "chase_cc_you"
    assert txns[0].merchant != ""


# ── Discover parser ───────────────────────────────────────────────────────────

def test_discover_parser_flips_sign():
    path = _write_csv(
        [{"Trans. Date": "01/15/2024", "Post Date": "01/16/2024",
          "Description": "WHOLE FOODS", "Amount": "55.20", "Category": "Groceries"}],
        ["Trans. Date", "Post Date", "Description", "Amount", "Category"],
    )
    txns = DiscoverParser("discover_you").parse(path)
    assert txns[0].amount == -55.20  # Discover positive → expense negative


# ── Apple Card parser ─────────────────────────────────────────────────────────

def test_apple_card_parser():
    path = _write_csv(
        [{"Transaction Date": "2024-01-15", "Clearing Date": "2024-01-16",
          "Description": "Uber Eats", "Merchant": "Uber Eats",
          "Category": "Food and Drink", "Type": "Purchase", "Amount (USD)": "18.75"}],
        ["Transaction Date", "Clearing Date", "Description", "Merchant", "Category", "Type", "Amount (USD)"],
    )
    txns = AppleCardParser("apple_card_you").parse(path)
    assert txns[0].amount == -18.75
    assert txns[0].merchant == "UBER EATS"


# ── Deduplication ─────────────────────────────────────────────────────────────

def test_identical_transactions_both_inserted():
    import sqlite3
    from storage.db import _ensure_schema
    from processing.deduplicator import insert_transactions
    from ingestion.parsers.base import Transaction
    from datetime import date

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)

    t = Transaction(date=date(2024, 1, 15), description="STARBUCKS", merchant="STARBUCKS",
                    amount=-5.75, account_id="chase_sapphire_sub")

    r1 = insert_transactions(conn, [t])
    r2 = insert_transactions(conn, [t])

    # Both inserted — no dedup, two identical charges are both real
    assert r1.inserted == 1
    assert r2.inserted == 1
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 2
    conn.close()


def test_card_payment_dropped():
    import sqlite3
    from storage.db import _ensure_schema
    from processing.deduplicator import insert_transactions, is_card_payment
    from ingestion.parsers.base import Transaction
    from datetime import date

    # is_card_payment detection
    assert is_card_payment("Payment Thank You-Mobile")
    assert is_card_payment("AUTOPAY PAYMENT")
    assert is_card_payment("Online PMT")
    assert not is_card_payment("AMAZON REFUND")
    assert not is_card_payment("UBER EATS")

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)

    payment = Transaction(date=date(2024, 1, 15), description="Payment Thank You-Mobile",
                          merchant="PAYMENT", amount=3599.77, account_id="chase_sapphire_sub")
    refund  = Transaction(date=date(2024, 1, 16), description="AMAZON REFUND",
                          merchant="AMAZON", amount=29.99, account_id="chase_sapphire_sub")
    expense = Transaction(date=date(2024, 1, 17), description="WHOLE FOODS",
                          merchant="WHOLE FOODS", amount=-85.00, account_id="chase_sapphire_sub")

    result = insert_transactions(conn, [payment, refund, expense])
    assert result.payments_dropped == 1
    assert result.inserted == 2   # refund + expense both kept
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 2
    conn.close()


# ── Normalizer category map ───────────────────────────────────────────────────

def test_normalize_bank_category():
    from ingestion.normalizer import normalize_bank_category
    assert normalize_bank_category("Restaurants") == "dining"
    assert normalize_bank_category("Groceries") == "groceries"
    assert normalize_bank_category("Unknown XYZ") is None
