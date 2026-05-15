import pandas as pd
from .base import StatementParser, Transaction
from ingestion.normalizer import extract_merchant


class USBankParser(StatementParser):
    """US Bank checking CSV export.

    Columns: Date, Transaction, Name, Memo, Amount
    Negative amount = debit/expense, positive = credit.
    """

    def parse(self, filepath: str) -> list[Transaction]:
        df = pd.read_csv(filepath)
        transactions = []
        for _, row in df.iterrows():
            amount = float(str(row.get("Amount", 0)).replace(",", "").replace("$", ""))
            description = str(row.get("Name", row.get("Memo", ""))).strip()
            transactions.append(Transaction(
                date=self._to_date(str(row["Date"])),
                description=description,
                merchant=extract_merchant(description),
                amount=amount,
                account_id=self.account_id,
                source_category=str(row.get("Transaction", "")).strip() or None,
            ))
        return [t for t in transactions if t.amount != 0]
