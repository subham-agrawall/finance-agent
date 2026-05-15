from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
import pandas as pd


@dataclass
class Transaction:
    date: date
    description: str
    merchant: str        # cleaned merchant token for LLM/rules (no PII)
    amount: float        # negative = expense, positive = credit/refund
    account_id: str
    source_category: str | None = None  # bank-provided category, if any


class StatementParser(ABC):
    def __init__(self, account_id: str):
        self.account_id = account_id

    @abstractmethod
    def parse(self, filepath: str) -> list[Transaction]:
        """Parse a CSV file and return a list of normalized Transactions."""

    def _to_date(self, value: str) -> date:
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y", "%d/%m/%Y"):
            try:
                return pd.to_datetime(value, format=fmt).date()
            except (ValueError, TypeError):
                continue
        return pd.to_datetime(value, infer_datetime_format=True).date()
