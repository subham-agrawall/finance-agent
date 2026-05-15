from ingestion.pdf_parser import parse_pdf


SAMPLE_PDF = "sample/20260403-statements-2824-.pdf"


def test_chase_pdf_extracts_transactions():
    txns = parse_pdf(SAMPLE_PDF, "chase_cc_you", use_llm=False)
    assert len(txns) == 51


def test_chase_pdf_purchases_are_negative():
    txns = parse_pdf(SAMPLE_PDF, "chase_cc_you", use_llm=False)
    expenses = [t for t in txns if t.amount < 0]
    assert len(expenses) == 50  # 50 purchases, 1 payment


def test_chase_pdf_payment_is_positive():
    txns = parse_pdf(SAMPLE_PDF, "chase_cc_you", use_llm=False)
    payments = [t for t in txns if t.amount > 0]
    assert len(payments) == 1
    assert abs(payments[0].amount - 3599.77) < 0.01


def test_chase_pdf_merchants_are_clean():
    txns = parse_pdf(SAMPLE_PDF, "chase_cc_you", use_llm=False)
    for t in txns:
        assert t.merchant != ""
        # Merchant should never contain dollar amounts or card-number patterns
        assert "$" not in t.merchant
        assert len(t.merchant) >= 2


def test_chase_pdf_year_detected():
    txns = parse_pdf(SAMPLE_PDF, "chase_cc_you", use_llm=False)
    years = {t.date.year for t in txns}
    assert years == {2026}


def test_chase_pdf_account_id_set():
    txns = parse_pdf(SAMPLE_PDF, "chase_cc_you", use_llm=False)
    assert all(t.account_id == "chase_cc_you" for t in txns)
