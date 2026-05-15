import re
import yaml
from pathlib import Path

_SETTINGS_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"
_CARD_NUMBER_RE = re.compile(r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b")
_AMOUNT_RE = re.compile(r"\$?-?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?")


def _owner_names() -> list[str]:
    try:
        with open(_SETTINGS_PATH) as f:
            return yaml.safe_load(f).get("owner_names", [])
    except Exception:
        return []


def scrub_pdf_text(text: str) -> str:
    """Remove card numbers, dollar amounts, and owner names from PDF text
    before it can be sent to the LLM. Never sends financial amounts or card details.
    """
    text = _CARD_NUMBER_RE.sub("****", text)
    text = _AMOUNT_RE.sub("[AMOUNT]", text)
    for name in _owner_names():
        if name:
            text = re.sub(re.escape(name), "[NAME]", text, flags=re.IGNORECASE)
    return text


def is_safe_merchant(merchant: str) -> bool:
    """Sanity-check that the merchant token doesn't accidentally contain PII
    before sending to LLM. Must be non-empty and not look like an amount/card number.
    """
    if not merchant or len(merchant.strip()) < 2:
        return False
    if re.search(r"\d{4}", merchant):
        return False
    if re.search(r"\$|\.\d{2}", merchant):
        return False
    return True
