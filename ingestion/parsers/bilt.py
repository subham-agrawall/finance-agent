import pandas as pd
from .base import StatementParser, Transaction
from ingestion.normalizer import extract_merchant


class BiltParser(StatementParser):
    """Bilt credit card CSV export.

    Bilt's export format: Date, Description, Amount, Category
    Amount: negative = charge, positive = credit (same as Chase CC convention).
    """

    def parse(self, filepath: str) -> list[Transaction]:
        df = pd.read_csv(filepath)
        transactions = []
        for _, row in df.iterrows():
            amount = float(str(row.get("Amount", 0)).replace(",", ""))
            description = str(row.get("Description", "")).strip()
            transactions.append(Transaction(
                date=self._to_date(str(row["Date"])),
                description=description,
                merchant=extract_merchant(description),
                amount=amount,
                account_id=self.account_id,
                source_category=str(row.get("Category", "")).strip() or None,
            ))
        return [t for t in transactions if t.amount != 0]
