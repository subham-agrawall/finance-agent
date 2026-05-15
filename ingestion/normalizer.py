import re

# Tokens that commonly appear in bank descriptions but add no merchant signal
_NOISE_TOKENS = {
    "PURCHASE", "PAYMENT", "DEBIT", "CREDIT", "POS", "ACH", "CHECKCARD",
    "RECURRING", "ONLINE", "AUTOPAY", "TRANSFER", "WITHDRAWAL", "DEPOSIT",
    "SALE", "RETAIL", "STORE", "INC", "LLC", "CO", "CORP", "THE",
}

# State abbreviations that appear in US merchant strings
_US_STATES = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN",
    "IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV",
    "NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN",
    "TX","UT","VT","VA","WA","WV","WI","WY","DC",
}


def extract_merchant(description: str) -> str:
    """Reduce a raw bank description to a clean merchant name token.

    This is the only text that ever gets sent to the LLM — it must never
    contain amounts, dates, account numbers, or location PII.
    """
    if not description:
        return ""

    text = description.upper().strip()

    # Remove card/account number patterns
    text = re.sub(r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b", "", text)
    text = re.sub(r"\b\d{4}\b", "", text)  # 4-digit store/ref codes

    # Remove date patterns (MM/DD, MM/DD/YY, etc.)
    text = re.sub(r"\b\d{1,2}/\d{1,2}(/\d{2,4})?\b", "", text)

    # Remove dollar amounts
    text = re.sub(r"\$[\d,]+\.?\d*", "", text)

    # Bank descriptions encode location after a store/ref number: stripping that number
    # leaves a double-space gap. Take only the text before that gap (the merchant portion).
    if "  " in text:
        text = text.split("  ")[0]

    # Strip trailing bare state abbreviation (e.g., " CA" at end of "WHOLE FOODS CA")
    state_pat = "|".join(_US_STATES)
    text = re.sub(r"\s+(" + state_pat + r")\s*$", "", text)

    # Strip special chars except spaces and ampersand
    text = re.sub(r"[^A-Z0-9& ]", " ", text)

    # Remove pure-noise tokens
    tokens = [t for t in text.split() if t not in _NOISE_TOKENS and len(t) > 1]

    if not tokens:
        return description.upper().strip()[:40]

    # Keep first 3 meaningful tokens — enough to identify the merchant
    merchant = " ".join(tokens[:3])
    return merchant


# Map bank-provided category strings to standard categories
_BANK_CATEGORY_MAP = {
    # Dining / Food
    "restaurants": "dining",
    "dining": "dining",
    "food & drink": "dining",
    "food and drink": "dining",
    "fast food": "dining",
    "coffee shops": "dining",
    # Groceries
    "groceries": "groceries",
    "supermarkets": "groceries",
    "grocery stores": "groceries",
    # Travel
    "travel": "travel",
    "airlines": "travel",
    "hotels": "travel",
    "lodging": "travel",
    "gas stations": "travel",
    "auto & transport": "travel",
    # Rides
    "ride share": "rides",
    "rideshare": "rides",
    "taxi": "rides",
    "uber": "rides",
    "lyft": "rides",
    # Subscriptions
    "subscriptions": "subscriptions",
    "streaming": "subscriptions",
    "software": "subscriptions",
    # Entertainment
    "entertainment": "entertainment",
    "movies & dvds": "entertainment",
    "music": "entertainment",
    # Shopping
    "shopping": "shopping",
    "clothing": "shopping",
    "electronics": "shopping",
    "merchandise": "shopping",
    # Health
    "health & fitness": "health",
    "pharmacy": "health",
    "medical": "health",
    "doctor": "health",
    # Utilities
    "utilities": "utilities",
    "bills": "utilities",
    "phone": "utilities",
    "internet": "utilities",
    # Rent
    "rent": "rent",
    "mortgage": "rent",
    "housing": "rent",
}

STANDARD_CATEGORIES = [
    "dining", "travel", "vacation", "groceries", "rent", "rides",
    "entertainment", "health", "shopping", "utilities", "subscriptions",
]


def normalize_bank_category(raw: str | None) -> str | None:
    if not raw:
        return None
    return _BANK_CATEGORY_MAP.get(raw.strip().lower())
