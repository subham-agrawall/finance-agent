import re
from datetime import date, datetime
from pathlib import Path

import pdfplumber

from ingestion.normalizer import extract_merchant
from ingestion.parsers.base import Transaction

# Matches: MM/DD  DESCRIPTION  AMOUNT  (e.g. "03/07 GITHUB, INC. GITHUB.COM CA 9.03")
_TX_LINE_RE = re.compile(
    r"^(\d{2}/\d{2})\s+(.+?)\s+(-?[\d,]+\.\d{2})\s*$"
)

# Section headers that tell us whether amounts are purchases or credits
_PURCHASE_HEADERS = {"PURCHASE", "PURCHASES", "TRANSACTIONS"}
_CREDIT_HEADERS = {"PAYMENTS AND OTHER CREDITS", "PAYMENTS", "CREDITS", "OTHER CREDITS"}

# Lines to skip that aren't transactions (currency exchange continuation lines, etc.)
_SKIP_LINE_RE = re.compile(
    r"^[\d,]+\.\d{2}\s+X\s+[\d.]+\s+\(EXCHG",  # e.g. "3,000.00 X 0.016806666 (EXCHG RATE)"
    re.IGNORECASE,
)


def parse_pdf(filepath: str, account_id: str, use_llm: bool = True) -> list[Transaction]:
    """Extract transactions from a bank PDF statement.

    Strategy:
    1. Try text-line parsing (handles Chase and similar statement layouts).
    2. Fall back to pdfplumber table extraction.
    3. Fall back to LLM only if both produce < 3 transactions and use_llm is True.
    """
    full_text = _extract_full_text(filepath)
    year = _detect_year(full_text)

    transactions = _parse_text_lines(full_text, account_id, year)

    if len(transactions) < 3:
        transactions = _extract_with_pdfplumber(filepath, account_id, year)

    if len(transactions) < 3 and use_llm:
        from llm.client import extract_transactions_from_pdf_text
        transactions = extract_transactions_from_pdf_text(full_text, account_id)

    return transactions


# ── Text-line parser ──────────────────────────────────────────────────────────

def _parse_text_lines(full_text: str, account_id: str, year: int) -> list[Transaction]:
    """Parse transactions from statement text using MM/DD DESCRIPTION AMOUNT pattern.

    The "ACCOUNT ACTIVITY" header uses doubled characters in Chase PDFs (font artifact),
    so we skip that gate and rely on the section headers + regex to identify transactions.
    """
    transactions = []
    is_purchase_section = True  # default: treat unrecognised sections as purchases

    for line in full_text.splitlines():
        line = line.strip()
        if not line:
            continue

        # Detect section headers (PURCHASE vs PAYMENTS)
        line_upper = line.upper()
        if any(line_upper == h for h in _PURCHASE_HEADERS):
            is_purchase_section = True
            continue
        if any(line_upper == h for h in _CREDIT_HEADERS):
            is_purchase_section = False
            continue

        # Skip currency exchange continuation lines
        if _SKIP_LINE_RE.match(line):
            continue

        # Skip header rows
        if "MERCHANT NAME" in line_upper or "TRANSACTION DESCRIPTION" in line_upper:
            continue

        m = _TX_LINE_RE.match(line)
        if not m:
            continue

        date_str, description, amount_str = m.group(1), m.group(2).strip(), m.group(3)
        amount = float(amount_str.replace(",", ""))

        # Normalize sign: purchases → negative (expense), payments → positive (credit)
        if is_purchase_section and amount > 0:
            amount = -amount
        elif not is_purchase_section and amount < 0:
            # Negative in payments section = a true payment/credit (keep positive)
            amount = abs(amount)

        try:
            tx_date = _parse_date(date_str, year)
        except ValueError:
            continue

        transactions.append(Transaction(
            date=tx_date,
            description=description,
            merchant=extract_merchant(description),
            amount=amount,
            account_id=account_id,
        ))

    return transactions


# ── pdfplumber table fallback ─────────────────────────────────────────────────

def _extract_with_pdfplumber(filepath: str, account_id: str, year: int) -> list[Transaction]:
    transactions = []
    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                for row in table:
                    t = _try_parse_table_row(row, account_id, year)
                    if t:
                        transactions.append(t)
    return transactions


def _try_parse_table_row(row: list, account_id: str, year: int) -> Transaction | None:
    if not row or len(row) < 3:
        return None
    date_val = None
    description = None
    amount = None

    for cell in row:
        text = str(cell or "").strip()
        if date_val is None:
            date_val = _try_parse_date_cell(text, year)
        if amount is None:
            amount = _try_parse_amount_cell(text)
        if (description is None and len(text) > 3
                and not _try_parse_date_cell(text, year)
                and not _try_parse_amount_cell(text)):
            description = text

    if date_val and description and amount is not None:
        return Transaction(
            date=date_val,
            description=description,
            merchant=extract_merchant(description),
            amount=amount,
            account_id=account_id,
        )
    return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_full_text(filepath: str) -> str:
    pages = []
    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
    return "\n".join(pages)


def _detect_year(text: str) -> int:
    """Extract the statement year from common header patterns."""
    # "Opening/Closing Date 03/04/26 - 04/03/26" or "Statement Date: 04/03/26"
    m = re.search(r"(?:Opening/Closing Date|Statement Date)[:\s]+\d{2}/\d{2}/(\d{2,4})", text)
    if m:
        y = int(m.group(1))
        return y if y > 100 else 2000 + y

    # "April 2026" or "March 2025"
    m = re.search(r"\b(20\d{2})\b", text)
    if m:
        return int(m.group(1))

    return datetime.now().year


def _parse_date(date_str: str, year: int) -> date:
    """Parse MM/DD with a known year."""
    month, day = date_str.split("/")
    return date(year, int(month), int(day))


def _try_parse_date_cell(text: str, year: int) -> date | None:
    m = re.match(r"^(\d{1,2})/(\d{1,2})$", text.strip())
    if m:
        try:
            return date(year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    return None


def _try_parse_amount_cell(text: str) -> float | None:
    m = re.match(r"^-?\$?([\d,]+\.\d{2})$", text.strip())
    if m:
        try:
            return -abs(float(m.group(1).replace(",", "")))
        except ValueError:
            return None
    return None
