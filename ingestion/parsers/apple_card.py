import pandas as pd
from .base import StatementParser, Transaction
from ingestion.normalizer import extract_merchant

class AppleCardParser(StatementParser):
    """Apple Card CSV export.

    Columns: Transaction Date, Clearing Date, Description, Merchant, Category, Type, Amount (USD)
    Amount: positive = charge, negative = credit/refund.
    Apple provides a clean Merchant column — prefer it over Description.
    Only Type == "Purchase" rows are imported; Payment, Credit, and Debit are dropped.
    """

    def parse(self, filepath: str) -> list[Transaction]:
        df = pd.read_csv(filepath)
        transactions = []
        for _, row in df.iterrows():
            # Only import actual purchases — drop payments, credits (returns), and debit adjustments
            if str(row.get("Type", "")).strip() != "Purchase":
                continue

            raw_amount = float(str(row.get("Amount (USD)", 0)).replace(",", ""))
            amount = -raw_amount  # Apple: positive charge → make negative

            if amount == 0:
                continue

            merchant_raw = str(row.get("Merchant", row.get("Description", ""))).strip()
            description  = str(row.get("Description", merchant_raw)).strip()

            transactions.append(Transaction(
                date=self._to_date(str(row["Transaction Date"])),
                description=description,
                merchant=extract_merchant(merchant_raw),
                amount=amount,
                account_id=self.account_id,
                source_category=str(row.get("Category", "")).strip() or None,
            ))
        return transactions
