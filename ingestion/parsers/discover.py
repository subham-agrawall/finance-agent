import pandas as pd
from .base import StatementParser, Transaction
from ingestion.normalizer import extract_merchant


class DiscoverParser(StatementParser):
    """Discover credit card CSV export.

    Columns: Trans. Date, Post Date, Description, Amount, Category
    Amount: positive = charge, negative = credit/refund.
    We flip sign to match convention: negative = expense.
    """

    def parse(self, filepath: str) -> list[Transaction]:
        df = pd.read_csv(filepath)
        transactions = []
        for _, row in df.iterrows():
            raw_amount = float(str(row.get("Amount", 0)).replace(",", ""))
            amount = -raw_amount  # Discover: positive charge → make negative
            description = str(row.get("Description", "")).strip()
            transactions.append(Transaction(
                date=self._to_date(str(row["Trans. Date"])),
                description=description,
                merchant=extract_merchant(description),
                amount=amount,
                account_id=self.account_id,
                source_category=str(row.get("Category", "")).strip() or None,
            ))
        return [t for t in transactions if t.amount != 0]
