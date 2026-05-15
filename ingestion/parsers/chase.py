import pandas as pd
from .base import StatementParser, Transaction
from ingestion.normalizer import extract_merchant


class ChaseParser(StatementParser):
    """Handles Chase credit card and Chase checking CSV exports.

    Credit card columns: Transaction Date, Post Date, Description, Category, Type, Amount, Memo
    Checking columns:    Details, Posting Date, Description, Amount, Type, Balance, Check or Slip #
    """

    def parse(self, filepath: str) -> list[Transaction]:
        df = pd.read_csv(filepath)
        cols = {c.strip().lower() for c in df.columns}

        if "transaction date" in cols:
            return self._parse_credit(df)
        return self._parse_checking(df)

    def _parse_credit(self, df: pd.DataFrame) -> list[Transaction]:
        transactions = []
        for _, row in df.iterrows():
            amount = float(str(row.get("Amount", 0)).replace(",", ""))
            # Chase credit: negative = charge, positive = payment/refund
            description = str(row.get("Description", "")).strip()
            transactions.append(Transaction(
                date=self._to_date(str(row["Transaction Date"])),
                description=description,
                merchant=extract_merchant(description),
                amount=amount,
                account_id=self.account_id,
                source_category=str(row.get("Category", "")).strip() or None,
            ))
        return [t for t in transactions if t.amount != 0]

    def _parse_checking(self, df: pd.DataFrame) -> list[Transaction]:
        transactions = []
        for _, row in df.iterrows():
            amount = float(str(row.get("Amount", 0)).replace(",", ""))
            description = str(row.get("Description", "")).strip()
            transactions.append(Transaction(
                date=self._to_date(str(row["Posting Date"])),
                description=description,
                merchant=extract_merchant(description),
                amount=amount,
                account_id=self.account_id,
                source_category=str(row.get("Type", "")).strip() or None,
            ))
        return [t for t in transactions if t.amount != 0]
