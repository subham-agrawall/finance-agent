import datetime
import os
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from ingestion.parsers.apple_card import AppleCardParser
from ingestion.parsers.bilt import BiltParser
from ingestion.parsers.chase import ChaseParser
from ingestion.parsers.discover import DiscoverParser
from ingestion.parsers.us_bank import USBankParser
from ingestion.pdf_parser import parse_pdf
from processing.attributor import attribute_transaction, save_rule
from processing.categorizer import apply_correction, categorize
from processing.deduplicator import insert_transactions
from reports.summary import get_balance, get_monthly_summary
from storage.db import get_connection

_ACCOUNTS_PATH = Path(__file__).parent / "config" / "accounts.yaml"
_RULES_PATH    = Path(__file__).parent / "config" / "attribution_rules.yaml"
_SETTINGS_PATH = Path(__file__).parent / "config" / "settings.yaml"

INSTITUTION_PARSERS = {
    "chase":    ChaseParser,
    "us_bank":  USBankParser,
    "discover": DiscoverParser,
    "apple":    AppleCardParser,
    "bilt":     BiltParser,
}

st.set_page_config(page_title="Finance Agent", layout="wide")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def load_accounts() -> dict:
    with open(_ACCOUNTS_PATH) as f:
        return yaml.safe_load(f).get("accounts", {})


def load_settings() -> dict:
    with open(_SETTINGS_PATH) as f:
        return yaml.safe_load(f)


def _add_months(d: datetime.date, n: int) -> datetime.date:
    """Add n months to the first-of-month date d."""
    m = d.month - 1 + n
    return d.replace(year=d.year + m // 12, month=m % 12 + 1, day=1)


_PERIOD_OPTIONS = [
    "All Time", "This Month", "Last Month",
    "Last 3 Months", "Last 6 Months",
    "This Year", "Last 12 Months", "Custom Range",
]


def _date_range(key_prefix: str, default: str = "This Month") -> tuple[str | None, str | None]:
    """Render a period preset selectbox (+ custom from/to when needed).

    Returns (start, end) as 'YYYY-MM-DD' strings (end is exclusive),
    or (None, None) for "All Time".
    """
    today = datetime.date.today()
    first = today.replace(day=1)

    period = st.selectbox(
        "Period", _PERIOD_OPTIONS,
        index=_PERIOD_OPTIONS.index(default),
        key=f"{key_prefix}_period",
    )

    if period == "All Time":
        return None, None

    if period == "Custom Range":
        cr1, cr2 = st.columns(2)
        from_str = cr1.text_input(
            "From (YYYY-MM)", value=f"{today.year}-01",
            key=f"{key_prefix}_from",
        )
        to_str = cr2.text_input(
            "To (YYYY-MM, inclusive)", value=today.strftime("%Y-%m"),
            key=f"{key_prefix}_to",
        )
        try:
            yf, mf = map(int, from_str.split("-"))
            start = datetime.date(yf, mf, 1)
        except Exception:
            start = first.replace(month=1)
        try:
            yt, mt = map(int, to_str.split("-"))
            end = _add_months(datetime.date(yt, mt, 1), 1)
        except Exception:
            end = _add_months(first, 1)
        return start.isoformat(), end.isoformat()

    presets = {
        "This Month":    (first,              _add_months(first,  1)),
        "Last Month":    (_add_months(first, -1), first),
        "Last 3 Months": (_add_months(first, -2), _add_months(first, 1)),
        "Last 6 Months": (_add_months(first, -5), _add_months(first, 1)),
        "This Year":     (today.replace(month=1, day=1),
                          today.replace(year=today.year + 1, month=1, day=1)),
        "Last 12 Months":(_add_months(first, -11), _add_months(first, 1)),
    }
    start, end = presets[period]
    return start.isoformat(), end.isoformat()


def _account_filter(key_prefix: str, accounts: dict) -> list[str]:
    """Multiselect for accounts. Returns list of account_ids (empty = all)."""
    display_to_id = {v["display_name"]: k for k, v in accounts.items()}
    all_names     = list(display_to_id.keys())
    selected      = st.multiselect(
        "Accounts", all_names, default=all_names,
        key=f"{key_prefix}_accounts",
    )
    return [display_to_id[n] for n in selected] if selected else []


# ─── Upload Tab ───────────────────────────────────────────────────────────────

def render_upload():
    st.header("Upload Statements")
    accounts = load_accounts()
    settings = load_settings()

    account_options = {v["display_name"]: k for k, v in accounts.items()}
    selected_display = st.selectbox("Account", list(account_options.keys()))
    account_id  = account_options[selected_display]
    institution = accounts[account_id]["institution"]

    uploaded = st.file_uploader(
        "Drop CSV or PDF statement here",
        type=["csv", "pdf"],
        accept_multiple_files=False,
    )

    if uploaded and st.button("Process"):
        conn   = get_connection()
        suffix = Path(uploaded.name).suffix.lower()
        tmp    = Path("/tmp") / uploaded.name
        tmp.write_bytes(uploaded.read())

        with st.spinner("Parsing…"):
            if suffix == ".csv":
                parser_cls = INSTITUTION_PARSERS.get(institution)
                if not parser_cls:
                    st.error(f"No parser for institution: {institution}")
                    conn.close()
                    return
                transactions = parser_cls(account_id).parse(str(tmp))
            else:
                use_llm = settings.get("use_llm", True) and settings.get("pdf_llm_fallback", True)
                if use_llm and not os.environ.get("OPENAI_API_KEY"):
                    st.warning("OPENAI_API_KEY not set — PDF LLM fallback disabled.")
                    use_llm = False
                transactions = parse_pdf(str(tmp), account_id, use_llm=use_llm)

        result = insert_transactions(conn, transactions)
        msg = f"Inserted {result.inserted} transactions."
        if result.payments_dropped:
            msg += f" Dropped {result.payments_dropped} card payment(s)."
        st.success(msg)

        if result.inserted > 0:
            with st.spinner("Attributing and categorising…"):
                new_rows = conn.execute(
                    "SELECT id, merchant, account_id, source_category FROM transactions "
                    "WHERE person IS NULL ORDER BY id DESC LIMIT ?",
                    (result.inserted,),
                ).fetchall()
                for row in new_rows:
                    cat = categorize(conn, row["id"], row["merchant"], row["source_category"])
                    attribute_transaction(conn, row["id"], row["merchant"], row["account_id"], cat)
            st.success("Done — head to the Review tab.")
        conn.close()


# ─── Review Tab ───────────────────────────────────────────────────────────────

_SPLIT_OPTIONS = ["Sub", "Adsy", "50/50", "Custom"]


def _split_option(sub_r: float, adsy_r: float) -> str:
    if sub_r >= 0.99:                                          return "Sub"
    if adsy_r >= 0.99:                                         return "Adsy"
    if abs(sub_r - 0.5) < 0.02 and abs(adsy_r - 0.5) < 0.02: return "50/50"
    return "Custom"


def render_review():
    st.header("Review Transactions")
    accounts = load_accounts()
    from ingestion.normalizer import STANDARD_CATEGORIES
    cats = STANDARD_CATEGORIES + ["uncategorized"]

    # ── Filters ──────────────────────────────────────────────────────────────
    f1, f2, f3 = st.columns([3, 2, 1.4])
    with f1:
        acct_ids = _account_filter("rev", accounts)
    with f2:
        start, end = _date_range("rev", default="All Time")
    with f3:
        status_filter = st.selectbox("Status", ["Unreviewed", "Reviewed", "All"],
                                     key="rev_status")

    merchant_filter = st.text_input("Filter by merchant", placeholder="e.g. Uber, Doordash…",
                                    key="rev_merchant")

    # ── Query ─────────────────────────────────────────────────────────────────
    query = """SELECT id, date, merchant, amount, account_id, person,
               split_ratio, COALESCE(adsy_ratio, 1.0 - split_ratio) AS adsy_ratio,
               category, source_category, reviewed FROM transactions WHERE 1=1"""
    params: list = []

    if acct_ids:
        query += f" AND account_id IN ({','.join('?'*len(acct_ids))})"
        params.extend(acct_ids)
    if start:
        query += " AND date >= ? AND date < ?"
        params.extend([start, end])
    if status_filter == "Unreviewed":
        query += " AND reviewed = 0"
    elif status_filter == "Reviewed":
        query += " AND reviewed = 1"
    if merchant_filter:
        query += " AND merchant LIKE ?"
        params.append(f"%{merchant_filter.upper()}%")
    query += " ORDER BY account_id, date DESC"

    conn = get_connection()
    df   = pd.read_sql_query(query, conn, params=params)
    conn.close()

    if df.empty:
        st.info("No transactions match the selected filters.")
        return

    # ── Table grouped by account ──────────────────────────────────────────────
    COL_W = [0.9, 2.0, 1.6, 0.8, 2.2, 0.65, 0.65, 0.65, 1.1]
    HDR   = ["Date", "Merchant", "Category", "Amount",
             "Split", "Sub %", "Adsy %", "Other %", "Review Status"]

    for account_id, group in df.groupby("account_id", sort=False):
        display_name = accounts.get(account_id, {}).get("display_name", account_id)
        st.subheader(display_name)

        for col, lbl in zip(st.columns(COL_W), HDR):
            col.markdown(f"**{lbl}**")

        for _, row in group.iterrows():
            rid     = int(row["id"])
            sub_r   = float(row["split_ratio"])
            adsy_r  = float(row["adsy_ratio"])
            def_opt = _split_option(sub_r, adsy_r)

            c1, c2, c3, c4, c5, c6, c7, c8, c9 = st.columns(COL_W)

            c1.write(row["date"])
            c2.write(row["merchant"])

            cur_cat = row["category"] if row["category"] in cats else "uncategorized"
            c3.selectbox("", cats, index=cats.index(cur_cat),
                         key=f"cat_{rid}", label_visibility="collapsed")

            c4.write(f"${abs(row['amount']):,.2f}")

            split_sel = c5.segmented_control(
                "", _SPLIT_OPTIONS, default=def_opt,
                key=f"split_{rid}", label_visibility="collapsed",
            )
            # segmented_control returns None when required=False and nothing selected
            if split_sel is None:
                split_sel = def_opt

            if split_sel == "Sub":
                sub_pct, adsy_pct, other_pct = 100, 0, 0
                c6.write("100%"); c7.write("0%"); c8.write("0%")
            elif split_sel == "Adsy":
                sub_pct, adsy_pct, other_pct = 0, 100, 0
                c6.write("0%"); c7.write("100%"); c8.write("0%")
            elif split_sel == "50/50":
                sub_pct, adsy_pct, other_pct = 50, 50, 0
                c6.write("50%"); c7.write("50%"); c8.write("0%")
            else:  # Custom — all three editable, must sum to 100
                def_sub   = int(round(sub_r  * 100))
                def_adsy  = int(round(adsy_r * 100))
                def_other = max(0, 100 - def_sub - def_adsy)
                sub_pct   = c6.number_input("", 0, 100, def_sub,   5,
                                             key=f"sub_pct_{rid}",   label_visibility="collapsed")
                adsy_pct  = c7.number_input("", 0, 100, def_adsy,  5,
                                             key=f"adsy_pct_{rid}",  label_visibility="collapsed")
                other_pct = c8.number_input("", 0, 100, def_other, 5,
                                             key=f"other_pct_{rid}", label_visibility="collapsed")
                total_pct = sub_pct + adsy_pct + other_pct
                if total_pct != 100:
                    st.caption(f"⚠️ {sub_pct} + {adsy_pct} + {other_pct} = {total_pct}% (must be 100)")

            is_marked = st.session_state.get(f"chk_{rid}", False)
            if c9.button(
                "✓ Reviewed" if is_marked else "Mark Reviewed",
                key=f"rev_btn_{rid}",
                type="primary" if is_marked else "secondary",
            ):
                if split_sel == "Custom":
                    sp = st.session_state.get(f"sub_pct_{rid}",   sub_pct)
                    ap = st.session_state.get(f"adsy_pct_{rid}",  adsy_pct)
                    op = st.session_state.get(f"other_pct_{rid}", other_pct)
                    if sp + ap + op != 100:
                        st.session_state[f"pct_err_{rid}"] = True
                        st.rerun()
                        return
                st.session_state[f"pct_err_{rid}"] = False
                st.session_state[f"chk_{rid}"]     = not is_marked
                st.rerun()

            if st.session_state.get(f"pct_err_{rid}", False):
                st.error("⚠️ Percentages must sum to 100% before marking reviewed.")

        # Subtotal
        subtotal = group["amount"].abs().sum()
        t = st.columns(COL_W)
        t[1].markdown(f"**Subtotal — {len(group)} transactions**")
        t[3].markdown(f"**${subtotal:,.2f}**")
        st.divider()

    # Grand total
    if df["account_id"].nunique() > 1:
        grand = df["amount"].abs().sum()
        g = st.columns(COL_W)
        g[1].markdown(f"**Grand Total — {len(df)} transactions**")
        g[3].markdown(f"**${grand:,.2f}**")
        st.divider()

    # ── Action bar ────────────────────────────────────────────────────────────
    all_ids       = [int(r["id"]) for _, r in df.iterrows()]
    checked_ids   = [rid for rid in all_ids if st.session_state.get(f"chk_{rid}", False)]
    unreviewed_df = df[df["reviewed"] == 0]

    # Persistent success message after rerun
    if msg := st.session_state.pop("reapply_success", None):
        st.success(msg)

    ab1, ab2, ab3, ab4 = st.columns([3, 1, 1, 1])
    ab1.write(f"**{len(checked_ids)} / {len(all_ids)}** marked as reviewed")

    if ab2.button("Select All", key="select_all"):
        for rid in all_ids:
            st.session_state[f"chk_{rid}"] = True
        st.rerun()

    if ab3.button("↺ Re-apply Rules", disabled=len(unreviewed_df) == 0, key="reapply_btn"):
        conn = get_connection()
        with st.spinner(f"Re-applying to {len(unreviewed_df)} unreviewed transactions…"):
            for _, row in unreviewed_df.iterrows():
                cat = categorize(conn, int(row["id"]), row["merchant"], row["source_category"])
                attribute_transaction(conn, int(row["id"]), row["merchant"], row["account_id"], cat)
        conn.close()
        st.session_state["reapply_success"] = f"Rules re-applied to {len(unreviewed_df)} transactions."
        st.rerun()

    if st.button("✓ Confirm Reviewed", type="primary", disabled=not checked_ids):
        conn = get_connection()
        for _, row in df.iterrows():
            rid = int(row["id"])
            if rid not in checked_ids:
                continue

            new_cat   = st.session_state.get(f"cat_{rid}",   row["category"])
            split_sel = st.session_state.get(f"split_{rid}", "Sub") or "Sub"

            if split_sel == "Sub":
                new_sub, new_adsy = 1.0, 0.0
            elif split_sel == "Adsy":
                new_sub, new_adsy = 0.0, 1.0
            elif split_sel == "50/50":
                new_sub, new_adsy = 0.5, 0.5
            else:
                new_sub  = st.session_state.get(f"sub_pct_{rid}",  50) / 100.0
                new_adsy = st.session_state.get(f"adsy_pct_{rid}", 50) / 100.0

            is_group = 1 if (new_sub < 1.0 or new_adsy > 0.0) else 0
            # is_group=0 only when Sub=100%; update person so summary uses the right share column.
            new_person = "Sub" if is_group == 0 else row["person"]

            apply_correction(conn, rid, row["merchant"], new_cat)
            save_rule(row["merchant"], row["person"], new_cat, new_sub)
            conn.execute(
                """UPDATE transactions
                   SET category=?, split_ratio=?, adsy_ratio=?, is_group=?, person=?, reviewed=1
                   WHERE id=?""",
                (new_cat, new_sub, new_adsy, is_group, new_person, rid),
            )
        conn.commit()
        conn.close()
        st.success(f"Confirmed {len(checked_ids)} transactions.")
        st.rerun()


# ─── Summary Tab ──────────────────────────────────────────────────────────────

def render_summary():
    st.header("Summary")
    accounts = load_accounts()

    # ── Filters ──────────────────────────────────────────────────────────────
    f1, f2, f3 = st.columns([3, 2, 1.4])
    with f1:
        acct_ids = _account_filter("sum", accounts)
    with f2:
        start, end = _date_range("sum", default="This Month")
    with f3:
        status_filter = st.selectbox("Status", ["All", "Reviewed", "Unreviewed"],
                                     key="sum_status")

    if start is None:
        start, end = "2000-01-01", "2100-01-01"

    conn = get_connection()

    # Warn about unreviewed
    unreviewed = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE date >= ? AND date < ? AND reviewed = 0",
        (start, end),
    ).fetchone()[0]
    if unreviewed:
        st.warning(f"{unreviewed} unreviewed transaction(s) in this range — numbers may be incomplete.")

    summary = get_monthly_summary(conn, start, end,
                                  account_ids=acct_ids or None,
                                  review_status=status_filter)
    balance = get_balance(conn, start, end,
                          account_ids=acct_ids or None,
                          review_status=status_filter)
    conn.close()

    if summary.empty:
        st.info("No expense data for the selected filters.")
        return

    # ── Balance card ──────────────────────────────────────────────────────────
    b = balance["balance"]
    bc1, bc2, bc3 = st.columns(3)
    if b > 0:
        bc1.metric("💳 Adsy owes Sub", f"${b:,.2f}")
    elif b < 0:
        bc1.metric("💳 Sub owes Adsy", f"${abs(b):,.2f}")
    else:
        bc1.metric("💳 Settlement", "$0.00", delta="All square")
    bc2.metric("Sub covered for Adsy", f"${balance['sub_paid_for_adsy']:,.2f}")
    bc3.metric("Adsy covered for Sub", f"${balance['adsy_paid_for_sub']:,.2f}")

    st.divider()

    # ── Spending table with total row ─────────────────────────────────────────
    st.subheader("Spending by Category")

    total_row = pd.DataFrame([{
        "Category":     "Total",
        "Total Spent":  summary["Total Spent"].sum(),
        "Sub's Share":  summary["Sub's Share"].sum(),
        "Adsy's Share": summary["Adsy's Share"].sum(),
        "Other":        summary["Other"].sum(),
    }])
    display = pd.concat([summary, total_row], ignore_index=True)
    for col in ["Total Spent", "Sub's Share", "Adsy's Share", "Other"]:
        display[col] = display[col].map("${:,.2f}".format)

    st.dataframe(display, use_container_width=True, hide_index=True)


# ─── Rules Tab ────────────────────────────────────────────────────────────────

def render_rules():
    st.header("Attribution Rules")
    with open(_RULES_PATH) as f:
        data = yaml.safe_load(f) or {}
    rules = data.get("rules", {})

    if not rules:
        st.info("No rules yet. Corrections in the Review tab appear here.")
        return

    # Flatten nested structure: one row per merchant × card_owner pair
    rows = []
    for merchant, person_rules in rules.items():
        if isinstance(person_rules, dict):
            for card_owner, rule in person_rules.items():
                if isinstance(rule, dict):
                    sub_r = rule.get("split_ratio", 1.0)
                    rows.append({
                        "Merchant":   merchant,
                        "Card Owner": card_owner,
                        "Category":   rule.get("category", "—"),
                        "Sub %":  f"{int(round(sub_r * 100))}%",
                        "Adsy %": f"{int(round((1 - sub_r) * 100))}%",
                    })
    st.write(f"{len(rows)} rules across {len(rules)} merchants")
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Edit rules.yaml directly")
    raw    = yaml.dump(data, default_flow_style=False, allow_unicode=True)
    edited = st.text_area("YAML", raw, height=400)
    if st.button("Save rules"):
        try:
            parsed = yaml.safe_load(edited)
            with open(_RULES_PATH, "w") as f:
                yaml.dump(parsed, f, default_flow_style=False, allow_unicode=True)
            st.success("Rules saved.")
        except yaml.YAMLError as e:
            st.error(f"Invalid YAML: {e}")


# ─── Main ─────────────────────────────────────────────────────────────────────

tab1, tab2, tab3, tab4 = st.tabs(["Upload", "Review", "Summary", "Rules"])

with tab1:
    render_upload()
with tab2:
    render_review()
with tab3:
    render_summary()
with tab4:
    render_rules()
