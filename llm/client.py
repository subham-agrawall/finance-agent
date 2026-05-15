import json
import os
import re
from datetime import date
from pathlib import Path

import yaml
from openai import OpenAI

from ingestion.normalizer import STANDARD_CATEGORIES, extract_merchant
from ingestion.parsers.base import Transaction
from llm.sanitizer import is_safe_merchant, scrub_pdf_text

_SETTINGS_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"

_client: OpenAI | None = None


def _settings() -> dict:
    with open(_SETTINGS_PATH) as f:
        return yaml.safe_load(f)


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    return _client


def classify_merchant(merchant: str) -> str | None:
    """Ask the LLM to classify a cleaned merchant name into one standard category.

    PRIVACY: Only the sanitized merchant token is sent — no amounts, dates, or account info.
    Returns None if the merchant fails the safety check or LLM is uncertain.
    """
    if not is_safe_merchant(merchant):
        return None

    model = _settings().get("llm_model", "gpt-4o-mini")
    cats = ", ".join(STANDARD_CATEGORIES)

    try:
        response = _get_client().chat.completions.create(
            model=model,
            max_tokens=20,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a spending classifier. Reply with exactly one word from the allowed list. "
                        "If uncertain, reply 'uncategorized'."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Merchant: {merchant}\n"
                        f"Categories: {cats}\n"
                        "Reply with one category word only."
                    ),
                },
            ],
        )
        result = response.choices[0].message.content.strip().lower()
        return result if result in STANDARD_CATEGORIES else None
    except Exception:
        return None


def extract_transactions_from_pdf_text(raw_text: str, account_id: str) -> list[Transaction]:
    """LLM fallback: extract transactions from scraped PDF text when pdfplumber fails.

    PRIVACY: raw_text is scrubbed before sending (card numbers and amounts masked).
    Amounts are redacted — returned transactions have amount=0.0 and require manual entry.
    """
    scrubbed = scrub_pdf_text(raw_text)
    model = _settings().get("llm_model", "gpt-4o-mini")

    try:
        response = _get_client().chat.completions.create(
            model=model,
            max_tokens=1024,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a data extractor. Extract transactions from bank statement text. "
                        "Return a JSON array of objects with keys: date (YYYY-MM-DD), merchant (string). "
                        "Do not include amounts — they have been redacted. "
                        "If you cannot extract transactions, return []."
                    ),
                },
                {"role": "user", "content": scrubbed[:4000]},
            ],
        )
        text = response.choices[0].message.content.strip()
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if not m:
            return []
        items = json.loads(m.group())
        transactions = []
        for item in items:
            try:
                d = date.fromisoformat(item["date"])
                merchant = str(item.get("merchant", "")).strip()
                transactions.append(Transaction(
                    date=d,
                    description=merchant,
                    merchant=extract_merchant(merchant),
                    amount=0.0,  # amount redacted — requires manual entry in Review tab
                    account_id=account_id,
                ))
            except Exception:
                continue
        return transactions
    except Exception:
        return []
