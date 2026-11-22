"""Finance, accounting and valuation engine.

Covers catalog categories 13-20: general ledger mechanics, statements, budgeting,
valuation, treasury/AR/AP mechanics, tax support and portfolio math.

Design rules enforced throughout:
  * every calculation states the accounting policy it assumed
  * control totals are computed and returned (balanced / imbalanced)
  * nothing is posted, filed or released automatically; outputs are review material
"""

from __future__ import annotations

import importlib.util
import math
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path


def _sdf_import(alias, filename):
    if alias in sys.modules:
        return sys.modules[alias]
    path = Path(__file__).resolve().parent / filename
    spec = importlib.util.spec_from_file_location(alias, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    return mod


C = _sdf_import("sdf_common", "tools-sdf-common.py")
SkillToolError = C.SkillToolError

NORMAL_BALANCE = {
    "asset": "debit",
    "expense": "debit",
    "liability": "credit",
    "equity": "credit",
    "revenue": "credit",
    "income": "credit",
    "contra_asset": "credit",
    "contra_revenue": "debit",
}


# ==========================================================================
# chart of accounts, journals, trial balance
# ==========================================================================


def validate_coa(accounts):
    """accounts: [{"code","name","type","parent"?, "reporting_map"?}]"""
    problems = []
    codes = {}
    for i, a in enumerate(accounts):
        code = str(a.get("code", "")).strip()
        name = str(a.get("name", "")).strip()
        typ = str(a.get("type", "")).strip().lower()
        if not code:
            problems.append({"row": i + 2, "issue": "missing_code"})
            continue
        if not name:
            problems.append({"row": i + 2, "issue": "missing_name", "code": code})
        if typ not in NORMAL_BALANCE:
            problems.append({"row": i + 2, "issue": "unknown_type", "code": code, "value": typ,
                             "allowed": sorted(NORMAL_BALANCE)})
        if code in codes:
            problems.append({"row": i + 2, "issue": "duplicate_code", "code": code,
                             "first_row": codes[code]})
        else:
            codes[code] = i + 2
    for i, a in enumerate(accounts):
        parent = str(a.get("parent", "") or "").strip()
        if parent and parent not in codes:
            problems.append({"row": i + 2, "issue": "unknown_parent", "code": a.get("code"), "parent": parent})
        if parent and parent == str(a.get("code")).strip():
            problems.append({"row": i + 2, "issue": "self_parent", "code": a.get("code")})
    hierarchy = {}
    for a in accounts:
        code = str(a.get("code", "")).strip()
        parts = re.split(r"[.\-]", code)
        hierarchy.setdefault(parts[0], []).append(code)
    return {
        "accounts": len(accounts),
        "problems": problems,
        "problem_count": len(problems),
        "by_type": _tally(accounts, "type"),
        "hierarchy_roots": len(hierarchy),
        "verdict": "valid" if not problems else "needs_review",
        "control_note": "Every account must have one type so the normal balance and statement mapping are unambiguous.",
    }


def _tally(rows, field):
    out = {}
    for r in rows:
        k = str(r.get(field, "")).lower()
        out[k] = out.get(k, 0) + 1
    return out


def journal_check(entries, tolerance=0.01):
    """entries: rows of {date, reference, account, description, debit, credit}."""
    groups = {}
    problems = []
    for i, e in enumerate(entries):
        ref = str(e.get("reference", "") or "").strip() or f"__row_{i + 2}"
        groups.setdefault(ref, []).append((i + 2, e))
    results = []
    for ref, rows in groups.items():
        debit = sum(C.to_number(e.get("debit")) or 0.0 for _, e in rows)
        credit = sum(C.to_number(e.get("credit")) or 0.0 for _, e in rows)
        diff = round(debit - credit, 6)
        balanced = abs(diff) <= tolerance
        if not balanced:
            problems.append({"reference": ref, "rows": [r for r, _ in rows], "debit": debit,
                             "credit": credit, "difference": diff, "issue": "unbalanced"})
        for r, e in rows:
            d = C.to_number(e.get("debit")) or 0.0
            c = C.to_number(e.get("credit")) or 0.0
            if d and c:
                problems.append({"row": r, "reference": ref, "issue": "both_debit_and_credit"})
            if not d and not c:
                problems.append({"row": r, "reference": ref, "issue": "zero_amount_line"})
            if (d or 0) < 0 or (c or 0) < 0:
                problems.append({"row": r, "reference": ref, "issue": "negative_amount_use_opposite_side"})
            if not C.parse_date(e.get("date")):
                problems.append({"row": r, "reference": ref, "issue": "unparseable_date", "value": e.get("date")})
            if not str(e.get("account", "")).strip():
                problems.append({"row": r, "reference": ref, "issue": "missing_account"})
        results.append({"reference": ref, "lines": len(rows), "debit": debit, "credit": credit,
                        "difference": diff, "balanced": balanced})
    total_debit = sum(r["debit"] for r in results)
    total_credit = sum(r["credit"] for r in results)
    return {
        "journal_count": len(results),
        "line_count": len(entries),
        "total_debit": total_debit,
        "total_credit": total_credit,
        "difference": round(total_debit - total_credit, 6),
        "balanced": abs(total_debit - total_credit) <= tolerance,
        "journals": results,
        "problems": problems,
        "problem_count": len(problems),
        "policy": "Double-entry check only. Approval, period locks and posting authority remain outside this tool.",
    }


def trial_balance(entries, accounts=None, as_of=None, account_column="account",
                  debit_column="debit", credit_column="credit", opening=None):
    cutoff = C.parse_date(as_of) if as_of else None
    opening = opening or {}
    bal = {}
    skipped = 0
    for e in entries:
        d = C.parse_date(e.get("date"))
        if cutoff and d and d > cutoff:
            continue
        acct = str(e.get(account_column, "")).strip()
        if not acct:
            skipped += 1
            continue
        dr = C.to_number(e.get(debit_column)) or 0.0
        cr = C.to_number(e.get(credit_column)) or 0.0
        rec = bal.setdefault(acct, {"debit": 0.0, "credit": 0.0})
        rec["debit"] += dr
        rec["credit"] += cr
    types = {}
    if accounts:
        for a in accounts:
            types[str(a.get("code", "")).strip()] = str(a.get("type", "")).lower()
    rows = []
    for acct in sorted(bal):
        opened = opening.get(acct, 0.0)
        debit = bal[acct]["debit"]
        credit = bal[acct]["credit"] + opened
        net = debit - credit
        typ = types.get(acct, "")
        normal = NORMAL_BALANCE.get(typ)
        rows.append({
            "account": acct,
            "type": typ,
            "opening": opened,
            "debit": round(debit, 2),
            "credit": round(credit, 2),
            "net_debit_positive": round(net, 2),
            "presentation": ("debit" if net > 0 else "credit" if net < 0 else "zero"),
            "normal_balance": normal,
            "abnormal_balance": bool(normal and ((net > 0 and normal == "credit") or (net < 0 and normal == "debit"))),
        })
    total_debit = sum(r["debit"] for r in rows)
    total_credit = sum(r["credit"] for r in rows)
    abnormal = [r["account"] for r in rows if r["abnormal_balance"]]
    return {
        "as_of": cutoff.isoformat() if cutoff else None,
        "accounts": len(rows),
        "rows": rows,
        "total_debit": round(total_debit, 2),
        "total_credit": round(total_credit, 2),
        "difference": round(total_debit - total_credit, 2),
        "balanced": abs(total_debit - total_credit) < 0.005,
        "abnormal_balances": abnormal,
        "skipped_lines": skipped,
        "control_note": "Trial balance must balance; open with the imbalance amount and trace it to the source journal.",
    }


def gl_summary(entries, group_by=("account",), amount_column="amount", date_column="date",
               sign_by_type=False, accounts=None):
    """Aggregate ledger detail into period/account summaries with sign normalisation."""
    types = {str(a.get("code", "")).strip(): str(a.get("type", "")).lower() for a in (accounts or [])}
    di = date_column
    buckets = {}
    for e in entries:
        d = C.parse_date(e.get(di))
        period = f"{d.year:04d}-{d.month:02d}" if d else "undated"
        key = tuple(str(e.get(g, "") or "") for g in group_by) + (period,)
        dr = C.to_number(e.get("debit")) or 0.0
        cr = C.to_number(e.get("credit")) or 0.0
        amt = C.to_number(e.get(amount_column))
        if amt is None:
            amt = dr - cr
        rec = buckets.setdefault(key, {"amount": 0.0, "lines": 0, "debit": 0.0, "credit": 0.0})
        rec["amount"] += float(amt)
        rec["debit"] += dr
        rec["credit"] += cr
        rec["lines"] += 1
    cols = list(group_by) + ["period", "amount", "debit", "credit", "lines"]
    rows = []
    for key, rec in sorted(buckets.items()):
        row = list(key) + [round(rec["amount"], 2), round(rec["debit"], 2), round(rec["credit"], 2), rec["lines"]]
        if sign_by_type:
            typ = types.get(key[0], "")
            if NORMAL_BALANCE.get(typ) == "credit":
                row[3] = round(-row[3], 2)
        rows.append(row)
    return {"table": C.make_table(cols, rows), "periods": sorted({r[3] for r in rows}) if rows else [],
            "sign_policy": "amount = debit - credit; credit-normal account types flipped when sign_by_type is set"}


# ==========================================================================
# statements
# ==========================================================================


def income_statement(tb_rows, mapping, headings=None):
    """tb_rows: trial balance rows; mapping: {account_prefix_or_code: line_item}."""
    grouped = _apply_mapping(tb_rows, mapping)
    lines = []
    for name, total in grouped["lines"].items():
        lines.append({"line_item": name, "group": grouped["groups"].get(name, ""), "amount": round(total, 2)})
    if headings:
        order = {h: i for i, h in enumerate(headings)}
        lines.sort(key=lambda r: (order.get(r["group"], 99), r["line_item"]))
    revenue = sum(r["amount"] for r in lines if r["group"] in {"revenue", "income"})
    cogs = sum(r["amount"] for r in lines if r["group"] in {"cost_of_sales", "cogs"})
    opex = sum(r["amount"] for r in lines if r["group"] in {"operating_expense", "opex"})
    other = sum(r["amount"] for r in lines if r["group"] in {"other_income", "other_expense"})
    tax = sum(r["amount"] for r in lines if r["group"] in {"tax", "income_tax"})
    gross = revenue - cogs
    operating = gross - opex
    pretax = operating + other
    net = pretax - tax
    return {
        "lines": lines,
        "subtotals": {
            "revenue": round(revenue, 2), "cost_of_sales": round(cogs, 2),
            "gross_profit": round(gross, 2), "operating_expenses": round(opex, 2),
            "operating_profit": round(operating, 2), "other_items": round(other, 2),
            "profit_before_tax": round(pretax, 2), "tax": round(tax, 2), "net_profit": round(net, 2),
            "gross_margin": C.safe_div(gross, revenue), "net_margin": C.safe_div(net, revenue),
        },
        "unmapped_accounts": grouped["unmapped"],
        "policy": "Sign convention: revenue positive, costs positive, net = revenue - costs - tax. Confirm mapping completeness.",
        "control_note": "unmapped_accounts must be empty before this statement is used for reporting.",
    }


def balance_sheet(tb_rows, mapping):
    grouped = _apply_mapping(tb_rows, mapping)
    lines = [{"line_item": k, "group": grouped["groups"].get(k, ""), "amount": round(v, 2)}
             for k, v in grouped["lines"].items()]
    assets = sum(r["amount"] for r in lines if r["group"] == "asset")
    liabilities = sum(r["amount"] for r in lines if r["group"] == "liability")
    equity = sum(r["amount"] for r in lines if r["group"] == "equity")
    diff = round(assets - (liabilities + equity), 2)
    return {
        "lines": lines,
        "subtotals": {"assets": round(assets, 2), "liabilities": round(liabilities, 2),
                      "equity": round(equity, 2), "liabilities_and_equity": round(liabilities + equity, 2),
                      "difference": diff},
        "balances": abs(diff) < 0.005,
        "unmapped_accounts": grouped["unmapped"],
        "policy": "Assets positive. Liabilities and equity positive as credit balances (flip debit-normal balances first).",
        "control_note": "A non-zero difference means the trial balance, mapping, or sign flip is wrong - resolve before release.",
    }


def cash_flow(indirect):
    """indirect: {net_profit, depreciation, working_capital_change, capex, financing, ...}"""
    net = C.to_number(indirect.get("net_profit"), 0.0)
    non_cash = C.to_number(indirect.get("depreciation_amortization"), 0.0)
    wc = C.to_number(indirect.get("working_capital_change"), 0.0)
    other_operating = C.to_number(indirect.get("other_operating"), 0.0)
    operating = net + non_cash + wc + other_operating
    capex = C.to_number(indirect.get("capex"), 0.0)
    other_investing = C.to_number(indirect.get("other_investing"), 0.0)
    investing = -abs(capex) + other_investing
    financing = sum(C.to_number(indirect.get(k), 0.0) for k in ("debt_issued", "debt_repaid", "equity_issued",
                                                               "dividends", "other_financing"))
    opening = C.to_number(indirect.get("opening_cash"), None)
    net_change = operating + investing + financing
    closing = opening + net_change if opening is not None else None
    reported = C.to_number(indirect.get("closing_cash_per_balance_sheet"), None)
    return {
        "operating": round(operating, 2),
        "investing": round(investing, 2),
        "financing": round(financing, 2),
        "net_change": round(net_change, 2),
        "opening_cash": opening,
        "derived_closing_cash": None if closing is None else round(closing, 2),
        "reported_closing_cash": reported,
        "difference": None if (reported is None or closing is None) else round(closing - reported, 2),
        "reconciles": (None if (reported is None or closing is None) else abs(closing - reported) < 0.005),
        "policy": "Sign convention: outflows negative. Capex is entered positive and subtracted.",
        "components": indirect,
    }


def _apply_mapping(tb_rows, mapping):
    """mapping: {"line item name": {"group": "...", "accounts": ["1000","1*"]}} or flat {account: line}"""
    lines = {}
    groups = {}
    unmapped = []
    flat = {}
    if mapping and all(isinstance(v, str) for v in mapping.values()):
        flat = {str(k): v for k, v in mapping.items()}
    else:
        for line_name, spec in (mapping or {}).items():
            groups[line_name] = spec.get("group", "")
            for acct in spec.get("accounts", []):
                flat[str(acct)] = line_name
    for r in tb_rows:
        acct = str(r.get("account") or r.get("code") or "").strip()
        amount = C.to_number(r.get("net_debit_positive"))
        if amount is None:
            amount = C.to_number(r.get("amount"), 0.0)
        target = flat.get(acct)
        if target is None:
            for pattern, line in flat.items():
                if pattern.endswith("*") and acct.startswith(pattern[:-1]):
                    target = line
                    break
                if pattern == acct:
                    target = line
                    break
        if target is None:
            unmapped.append(acct)
            continue
        # statement presentation: credit balances negative for revenue/liability/equity lines by group
        grp = groups.get(target, "")
        value = float(amount or 0.0)
        if grp in {"revenue", "income", "liability", "equity", "other_income"}:
            value = -value
        lines[target] = lines.get(target, 0.0) + value
    return {"lines": lines, "groups": groups, "unmapped": unmapped}


def variance(actual, budget, key="account", higher_is_better=True):
    """actual/budget: [{key, period?, amount}] -> variance table with flags."""
    a_map = {(r.get(key), r.get("period")): C.to_number(r.get("amount"), 0.0) for r in actual}
    b_map = {(r.get(key), r.get("period")): C.to_number(r.get("amount"), 0.0) for r in budget}
    keys = sorted(set(a_map) | set(b_map), key=lambda k: tuple(str(x) for x in k))
    rows = []
    for k in keys:
        a = a_map.get(k, 0.0)
        b = b_map.get(k, 0.0)
        var = a - b
        pct = C.safe_div(var, abs(b)) if b else None
        favourable = (var > 0) if higher_is_better else (var < 0)
        rows.append({key: k[0], "period": k[1], "actual": round(a, 2), "budget": round(b, 2),
                     "variance": round(var, 2), "variance_pct": pct,
                     "direction": "favourable" if favourable else "unfavourable",
                     "material": bool(pct is not None and abs(pct) >= 0.10)})
    return {"rows": rows,
            "totals": {"actual": round(sum(r["actual"] for r in rows), 2),
                       "budget": round(sum(r["budget"] for r in rows), 2),
                       "variance": round(sum(r["variance"] for r in rows), 2)},
            "materiality_policy": "Material = |variance| >= 10% of budget; change with --materiality.",
            "commentary_guidance": "Explain material variances by volume, price, mix, timing or one-offs - never by restating the number."}


# ==========================================================================
# budgeting / forecasting / valuation
# ==========================================================================


def driver_forecast(drivers, base_values=None, periods=12):
    """drivers: {"revenue": ["units","price"]} style multiplication chains.

    base_values: {"units": 100, "price": 10, "growth_units": 0.02, ...}
    """
    bv = dict(base_values or {})
    out = []
    for p in range(periods):
        row = {"period": p + 1}
        for name, chain in drivers.items():
            value = 1.0
            for factor in chain:
                rate = bv.get(f"{factor}_growth")
                base = bv.get(factor)
                if base is None:
                    raise SkillToolError("BAD_ARGUMENT", f"Missing base value for driver factor {factor!r}.",
                                         "Provide base_values for every factor used in drivers.")
                value *= float(base) * ((1 + float(rate)) ** p if rate is not None else 1.0)
            row[name] = round(value, 2)
        out.append(row)
    cols = ["period"] + list(drivers)
    return {"table": C.make_table(cols, [[r[c] for c in cols] for r in out]),
            "assumptions": bv, "policy": "Compounding growth per period; document every driver assumption in the register."}


def dcf(fcf, discount_rate, terminal_growth=None, terminal_multiple=None, mid_year=False, net_debt=0.0, shares=None):
    flows = C.nums(fcf)
    if not flows:
        raise SkillToolError("BAD_ARGUMENT", "Provide at least one free-cash-flow value.")
    if discount_rate is None or discount_rate <= -1:
        raise SkillToolError("BAD_ARGUMENT", "discount_rate must be greater than -1 (e.g. 0.10 for 10%).")
    if terminal_growth is not None and terminal_growth >= discount_rate:
        raise SkillToolError("BAD_ARGUMENT", "terminal_growth must be below the discount rate for a finite value.")
    pv = []
    for i, f in enumerate(flows):
        t = i + (0.5 if mid_year else 1)
        pv.append(f / (1 + discount_rate) ** t)
    pv_sum = sum(pv)
    n = len(flows)
    terminal = None
    terminal_basis = None
    if terminal_growth is not None:
        terminal = flows[-1] * (1 + terminal_growth) / (discount_rate - terminal_growth)
        terminal_basis = "gordon_growth"
    elif terminal_multiple is not None:
        terminal = flows[-1] * terminal_multiple
        terminal_basis = "exit_multiple"
    pv_terminal = None
    if terminal is not None:
        pv_terminal = terminal / (1 + discount_rate) ** (n if not mid_year else n - 0.5)
    ev = pv_sum + (pv_terminal or 0.0)
    equity = ev - float(net_debt or 0.0)
    return {
        "periods": n,
        "discount_rate": discount_rate,
        "pv_explicit": round(pv_sum, 2),
        "pv_by_period": [round(x, 2) for x in pv],
        "terminal_value": None if terminal is None else round(terminal, 2),
        "terminal_basis": terminal_basis,
        "pv_terminal": None if pv_terminal is None else round(pv_terminal, 2),
        "terminal_share_of_ev": None if (pv_terminal is None or not ev) else round(pv_terminal / ev, 4),
        "enterprise_value": round(ev, 2),
        "net_debt": net_debt,
        "equity_value": round(equity, 2),
        "value_per_share": None if not shares else round(equity / shares, 4),
        "mid_year_convention": mid_year,
        "caveats": [
            "Terminal value usually dominates (>60% of EV) - always show a sensitivity table.",
            "Discount rate, growth and FCF definitions are policy inputs; document their source and approver.",
        ],
    }


def loan_schedule(principal, annual_rate, periods, periods_per_year=12, payment=None, extra_payment=0.0,
                  start_date=None):
    if principal <= 0 or periods <= 0:
        raise SkillToolError("BAD_ARGUMENT", "principal and periods must be positive.")
    r = annual_rate / periods_per_year
    if payment is None:
        payment = abs(C.pmt(r, periods, principal)) if r else principal / periods
    else:
        payment = abs(payment)
    rows = []
    balance = float(principal)
    total_interest = 0.0
    start = C.parse_date(start_date) if start_date else None
    for p in range(1, int(periods) + 1):
        interest = balance * r
        pay = payment + extra_payment
        principal_paid = min(balance, pay - interest)
        if principal_paid <= 0:
            raise SkillToolError("BAD_ARGUMENT",
                                 f"Payment {pay:.2f} does not cover interest {interest:.2f} in period {p}.",
                                 "Increase the payment or reduce the principal.")
        balance = balance - principal_paid
        total_interest += interest
        due = None
        if start:
            due = (start + timedelta(days=int(round(30.4375 * p * 12 / periods_per_year)))).isoformat()
        rows.append({"period": p, "due_date": due, "opening_balance": round(balance + principal_paid, 2),
                     "payment": round(interest + principal_paid, 2), "interest": round(interest, 2),
                     "principal": round(principal_paid, 2), "closing_balance": round(balance, 2)})
        if balance <= 0.005:
            break
    cols = ["period", "due_date", "opening_balance", "payment", "interest", "principal", "closing_balance"]
    return {"table": C.make_table(cols, [[r[c] for c in cols] for r in rows]),
            "periodic_payment": round(payment, 2), "periods_per_year": periods_per_year,
            "total_interest": round(total_interest, 2),
            "total_paid": round(sum(r["payment"] for r in rows), 2),
            "final_balance": rows[-1]["closing_balance"] if rows else principal,
            "policy": "Payment = PMT(rate/periods_per_year, periods, -principal). Interest accrues on opening balance."}


def lease_schedule(payments, rate, initial_liability=None, start_date=None, periodicity="monthly"):
    """IFRS 16 / ASC 842 style amortisation of a lease liability from supplied payments."""
    if rate is None:
        raise SkillToolError("BAD_ARGUMENT", "Provide an incremental borrowing rate (--rate).")
    per_year = {"monthly": 12, "quarterly": 4, "annual": 1, "weekly": 52}.get(periodicity.lower(), 12)
    r = rate / per_year
    liability = initial_liability if initial_liability is not None else sum(payments)
    rows = []
    start = C.parse_date(start_date) if start_date else None
    total_interest = 0.0
    for i, pay in enumerate(payments, start=1):
        interest = liability * r
        principal = pay - interest
        closing = liability - principal
        total_interest += interest
        rows.append({"period": i, "opening_liability": round(liability, 2), "payment": round(pay, 2),
                     "interest": round(interest, 2), "principal": round(principal, 2),
                     "closing_liability": round(closing, 2),
                     "due_date": (start + timedelta(days=int(round(365 / per_year * i)))).isoformat() if start else None})
        liability = closing
    cols = ["period", "due_date", "opening_liability", "payment", "interest", "principal", "closing_liability"]
    return {"table": C.make_table(cols, [[x[c] for c in cols] for x in rows]),
            "initial_liability": round(initial_liability if initial_liability is not None else sum(payments), 2),
            "total_interest": round(total_interest, 2),
            "closing_liability": rows[-1]["closing_liability"] if rows else liability,
            "policy": "Liability amortisation at the supplied incremental borrowing rate; right-of-use asset and "
                      "modification accounting follow the entity's disclosed policy and are not automated here."}


def depreciation(cost, salvage=0.0, life_years=5, method="straight_line", periods_per_year=12,
                 rate=None, opening_book_value=None):
    cost = float(cost)
    salvage = float(salvage or 0.0)
    if cost <= 0 or life_years <= 0:
        raise SkillToolError("BAD_ARGUMENT", "cost and life_years must be positive.")
    n = int(round(life_years * periods_per_year))
    per = {"straight_line": 1.0 / n,
           "double_declining": 2.0 / n,
           "sum_of_years": None}.get(method.lower())
    if method.lower() not in {"straight_line", "double_declining", "sum_of_years", "macrs_placeholder", "declining_balance"}:
        raise SkillToolError("BAD_ARGUMENT", f"Unknown depreciation method {method!r}.",
                             "Use straight_line, double_declining, sum_of_years or declining_balance.")
    rows = []
    book = float(opening_book_value) if opening_book_value is not None else cost
    depreciable = cost - salvage
    if method.lower() == "straight_line":
        annual = depreciable / life_years
        for p in range(1, n + 1):
            amount = min(annual / periods_per_year, max(0.0, book - salvage))
            book -= amount
            rows.append({"period": p, "depreciation": round(amount, 2), "accumulated": round(cost - book, 2),
                         "book_value": round(book, 2)})
    elif method.lower() in {"double_declining", "declining_balance"}:
        factor = float(rate) if (method.lower() == "declining_balance" and rate) else 2.0
        for p in range(1, n + 1):
            annual = book * (factor / life_years)
            amount = max(0.0, min(annual / periods_per_year, book - salvage))
            book -= amount
            rows.append({"period": p, "depreciation": round(amount, 2), "accumulated": round(cost - book, 2),
                         "book_value": round(book, 2)})
    else:  # sum_of_years
        years = int(round(life_years))
        denom = years * (years + 1) / 2
        acc = 0.0
        for y in range(1, years + 1):
            amount = depreciable * (years - y + 1) / denom
            acc += amount
            rows.append({"period": y, "depreciation": round(amount, 2), "accumulated": round(acc, 2),
                         "book_value": round(cost - acc, 2)})
    return {"method": method, "charge_count": len(rows), "rows": rows,
            "total_depreciation": round(sum(r["depreciation"] for r in rows), 2),
            "policy": "Depreciation is a policy estimate. Confirm useful life, salvage value and start date against the fixed-asset register."}


# ==========================================================================
# AR / AP / treasury / tax support
# ==========================================================================


def aging(invoices, as_of=None, amount_column="amount", due_column="due_date",
          party_column="customer", bucket_days=(0, 30, 60, 90), paid_column="paid"):
    """Bucket open items by days overdue using explicit, auditable edges."""
    as_of_date = C.parse_date(as_of) if as_of else date.today()
    edges = sorted({int(b) for b in bucket_days}) or [0, 30, 60, 90]
    labels = ["not_yet_due"]
    for i in range(len(edges) - 1):
        labels.append(f"{edges[i] + 1}-{edges[i + 1]}")
    labels.append(f"{edges[-1]}+")
    party_totals = {}
    detail = []
    skipped = 0
    for inv in invoices:
        if paid_column and C.to_bool(inv.get(paid_column), False):
            continue
        due = C.parse_date(inv.get(due_column))
        amt = C.to_number(inv.get(amount_column))
        if due is None or amt is None:
            skipped += 1
            continue
        overdue = (as_of_date - due).days
        if overdue <= 0:
            label = labels[0]
        else:
            label = labels[-1]
            for i in range(len(edges) - 1):
                if edges[i] < overdue <= edges[i + 1]:
                    label = labels[i + 1]
                    break
        party = str(inv.get(party_column, "") or "")
        rec = party_totals.setdefault(party, {"party": party, "total": 0.0, **{l: 0.0 for l in labels}})
        rec["total"] += amt
        rec[label] += amt
        detail.append({"party": party, "invoice": inv.get("invoice_number") or inv.get("invoice"),
                       "due_date": due.isoformat(), "days_overdue": max(0, overdue),
                       "amount": round(amt, 2), "bucket": label})
    cols = ["party"] + labels + ["total"]
    rows = [[p["party"]] + [round(p[l], 2) for l in labels] + [round(p["total"], 2)]
            for p in sorted(party_totals.values(), key=lambda r: -r["total"])]
    totals = {l: round(sum(p[l] for p in party_totals.values()), 2) for l in labels}
    totals["total"] = round(sum(p["total"] for p in party_totals.values()), 2)
    return {"table": C.make_table(cols, rows), "buckets": labels, "totals": totals,
            "as_of": as_of_date.isoformat(), "skipped_rows": skipped,
            "detail_sample": detail[:200],
            "policy": "Buckets are configurable via --buckets. Provisioning percentages are a policy "
                      "input and are not applied by this tool."}


def duplicate_invoices(invoices, amount_column="amount", vendor_column="vendor", date_column="date",
                       number_column="invoice_number"):
    findings = []
    seen = {}
    for i, inv in enumerate(invoices):
        num = str(inv.get(number_column, "") or "").strip().lower()
        vendor = str(inv.get(vendor_column, "") or "").strip().lower()
        amt = C.to_number(inv.get(amount_column))
        d = C.parse_date(inv.get(date_column))
        if num:
            key = ("number", num, "vendor", vendor)
            if key in seen:
                findings.append({"rule": "same_invoice_number_and_vendor", "rows": [seen[key], i + 2],
                                 "invoice_number": num, "vendor": vendor})
            else:
                seen[key] = i + 2
        if amt is not None and vendor and d:
            key = ("amount", round(amt, 2), "vendor", vendor, "date", d.isoformat())
            if key in seen:
                findings.append({"rule": "same_amount_vendor_date", "rows": [seen[key], i + 2],
                                 "amount": amt, "vendor": vendor, "date": d.isoformat()})
            else:
                seen[key] = i + 2
    return {"findings": findings[:500], "finding_count": len(findings),
            "policy": "Matches are candidates for human review, never auto-rejections.",
            "next_step": "Confirm against the purchase order, receipt and vendor master before payment action."}


def po_match(pos, invoices, receipts=None, price_tolerance=0.02, qty_tolerance=0.0):
    """Two-way (PO vs invoice) or three-way (with receipts) matching."""
    po_map = {str(p.get("po_number")): p for p in pos}
    rc_map = {str(r.get("po_number")): r for r in (receipts or [])}
    results = []
    for inv in invoices:
        po = po_map.get(str(inv.get("po_number")))
        issues = []
        status = "matched"
        if not po:
            results.append({"invoice": inv.get("invoice_number"), "po_number": inv.get("po_number"),
                            "status": "no_purchase_order", "issues": ["PO not found"]})
            continue
        po_amount = C.to_number(po.get("amount"))
        inv_amount = C.to_number(inv.get("amount"))
        if po_amount and inv_amount:
            diff_pct = (inv_amount - po_amount) / abs(po_amount)
            if abs(diff_pct) > price_tolerance:
                issues.append(f"invoice {diff_pct:+.2%} vs PO amount (tolerance {price_tolerance:.0%})")
        if receipts is not None:
            rc = rc_map.get(str(inv.get("po_number")))
            if not rc:
                issues.append("no receipt recorded")
            else:
                received = C.to_number(rc.get("quantity_received"), 0.0)
                invoiced = C.to_number(inv.get("quantity"), 0.0)
                if received < invoiced * (1 - qty_tolerance):
                    issues.append(f"invoiced quantity {invoiced} exceeds received {received}")
        if issues:
            status = "exception"
        results.append({"invoice": inv.get("invoice_number"), "po_number": inv.get("po_number"),
                        "status": status, "issues": issues,
                        "po_amount": po_amount, "invoice_amount": inv_amount})
    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"results": results, "status_counts": counts,
            "tolerances": {"price": price_tolerance, "quantity": qty_tolerance},
            "policy": "Exceptions require approval before payment. Tolerances are configuration, not defaults of fact."}


def vat_reconciliation(output_tax, input_tax, ledger_tax=None, sales_amount=None, rate=None, tolerance=0.01):
    expected_output = None
    if sales_amount is not None and rate is not None:
        expected_output = float(sales_amount) * float(rate)
    net_due = float(output_tax) - float(input_tax)
    diff = None
    if ledger_tax is not None:
        diff = net_due - float(ledger_tax)
    checks = []
    if expected_output is not None:
        delta = float(output_tax) - expected_output
        checks.append({"check": "output_tax_vs_rate", "expected": round(expected_output, 2),
                       "reported": round(float(output_tax), 2), "difference": round(delta, 2),
                       "passes": abs(delta) <= tolerance * max(1.0, abs(expected_output))})
    if diff is not None:
        checks.append({"check": "net_due_vs_ledger", "ledger": round(float(ledger_tax), 2),
                       "computed": round(net_due, 2), "difference": round(diff, 2),
                       "passes": abs(diff) <= tolerance * max(1.0, abs(net_due))})
    return {"output_tax": round(float(output_tax), 2), "input_tax": round(float(input_tax), 2),
            "net_tax_due": round(net_due, 2), "checks": checks,
            "all_checks_pass": all(c["passes"] for c in checks) if checks else None,
            "policy": "Jurisdiction rules, exemptions and rates are supplied inputs. Filing remains a human responsibility."}


def bank_reconciliation(bank_lines, ledger_lines, amount_column="amount", date_column="date",
                        ref_column="reference", date_window_days=3):
    def key(l, use_date):
        ref = str(l.get(ref_column, "") or "").strip().lower()
        amt = round(float(C.to_number(l.get(amount_column), 0.0)), 2)
        d = C.parse_date(l.get(date_column))
        return (ref, amt, d if use_date else None)

    bank_keys = {key(l, True): i for i, l in enumerate(bank_lines)}
    matched = []
    unmatched_bank = []
    used_ledger = set()
    for l in ledger_lines:
        k_exact = key(l, True)
        if k_exact in bank_keys and bank_keys[k_exact] not in used_ledger:
            matched.append({"bank_line": bank_keys[k_exact], "ledger_line": ledger_lines.index(l),
                            "rule": "reference_amount_date"})
            used_ledger.add(bank_keys[k_exact])
            continue
        found = None
        amt = round(float(C.to_number(l.get(amount_column), 0.0)), 2)
        d = C.parse_date(l.get(date_column))
        for bk, idx in bank_keys.items():
            if idx in used_ledger:
                continue
            if bk[1] == amt:
                bd = bk[2]
                if d is None or bd is None or abs((bd - d).days) <= date_window_days:
                    found = (idx, "amount_and_date_window" if bk[2] else "amount_only")
                    break
        if found:
            matched.append({"bank_line": found[0], "ledger_line": ledger_lines.index(l), "rule": found[1]})
            used_ledger.add(found[0])
        else:
            unmatched_bank = unmatched_bank
    unmatched_bank = [{"index": i, **bank_lines[i]} for i in range(len(bank_lines)) if i not in used_ledger]
    unmatched_ledger = [{"index": ledger_lines.index(l), **l} for l in ledger_lines
                        if not any(m["ledger_line"] == ledger_lines.index(l) for m in matched)]
    bank_total = sum(C.to_number(l.get(amount_column), 0.0) for l in bank_lines)
    ledger_total = sum(C.to_number(l.get(amount_column), 0.0) for l in ledger_lines)
    reconciling = sum(C.to_number(l.get(amount_column), 0.0) for l in unmatched_bank) - \
                  sum(C.to_number(l.get(amount_column), 0.0) for l in unmatched_ledger)
    return {
        "matched_count": len(matched),
        "matched": matched[:500],
        "unmatched_bank": unmatched_bank[:200],
        "unmatched_ledger": unmatched_ledger[:200],
        "bank_total": round(bank_total, 2),
        "ledger_total": round(ledger_total, 2),
        "difference": round(bank_total - ledger_total, 2),
        "reconciling_difference": round(reconciling, 2),
        "reconciles": abs(bank_total - ledger_total - reconciling) < 0.005,
        "policy": "Matching is reference-first then amount+date-window. Unmatched items stay open until a human resolves them.",
    }


# ==========================================================================
# portfolio / investment
# ==========================================================================


def portfolio_performance(periods, cash_flows=None, benchmark=None):
    """periods: list of portfolio values (ending), cash_flows: net contribution at start of each period."""
    values = C.nums(periods)
    if len(values) < 2:
        raise SkillToolError("INSUFFICIENT_DATA", "Need at least two period values.")
    flows = C.nums(cash_flows or [0.0] * len(values))
    if len(flows) < len(values):
        flows = flows + [0.0] * (len(values) - len(flows))
    twr = 1.0
    for i in range(1, len(values)):
        start = values[i - 1] + flows[i]
        if not start:
            continue
        twr *= (values[i] / start)
    twr_total = twr - 1.0
    # money-weighted return via IRR of cash flows including terminal value
    cf = [-values[0]] + [-flows[i] for i in range(1, len(values) - 1)] + [values[-1] - flows[-1]]
    mwr = _irr_safe(cf)
    result = {
        "periods": len(values),
        "total_return_time_weighted": twr_total,
        "annualised_time_weighted": (1 + twr_total) ** (12 / max(1, len(values) - 1)) - 1 if len(values) > 2 else twr_total,
        "money_weighted_irr": mwr,
        "start_value": values[0],
        "end_value": values[-1],
        "net_contributions": sum(flows[1:]),
    }
    if benchmark:
        bench = C.nums(benchmark)
        if len(bench) == len(values):
            excess = twr_total - (bench[-1] / bench[0] - 1)
            result["benchmark_total_return"] = bench[-1] / bench[0] - 1
            result["excess_return_vs_benchmark"] = excess
    return result


def _irr_safe(cf):
    def npv(r):
        return sum(v / (1 + r) ** i for i, v in enumerate(cf))

    lo, hi = -0.9999, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(300):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if f_lo * f_mid <= 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


def risk_metrics(returns, risk_free=0.0, periods_per_year=12, benchmark_returns=None):
    r = C.nums(returns)
    if len(r) < 3:
        raise SkillToolError("INSUFFICIENT_DATA", "Need at least 3 return observations.")
    sd = C.stdev(r)
    annual_vol = sd * math.sqrt(periods_per_year) if sd else None
    mean_r = C.mean(r)
    ann_mean = (1 + mean_r) ** periods_per_year - 1 if mean_r is not None else None
    downside = [x for x in r if x < 0]
    downside_dev = math.sqrt(sum(x * x for x in downside) / len(r)) * math.sqrt(periods_per_year) if downside else 0.0
    peak, mdd = -1e18, 0.0
    equity = 1.0
    for x in r:
        equity *= (1 + x)
        peak = max(peak, equity)
        if peak > 0:
            mdd = min(mdd, equity / peak - 1)
    out = {
        "observations": len(r),
        "mean_period_return": mean_r,
        "annualised_return": ann_mean,
        "annualised_volatility": annual_vol,
        "sharpe": C.safe_div(ann_mean - risk_free, annual_vol) if annual_vol else None,
        "sortino": C.safe_div(ann_mean - risk_free, downside_dev) if downside_dev else None,
        "max_drawdown": mdd,
        "positive_periods": sum(1 for x in r if x > 0),
        "negative_periods": len(downside),
        "periods_per_year": periods_per_year,
    }
    if benchmark_returns:
        b = C.nums(benchmark_returns)
        if len(b) == len(r):
            mx, my = C.mean(r), C.mean(b)
            cov = sum((x - mx) * (y - my) for x, y in zip(r, b)) / (len(r) - 1)
            var_b = C.variance(b)
            beta = C.safe_div(cov, var_b)
            out.update({"beta": beta, "alpha_annualised": None if beta is None else (ann_mean - risk_free) - beta * ((1 + my) ** periods_per_year - 1 - risk_free)})
    return out


def debt_schedule(principal, rate, periods, periods_per_year=12, amortising=True, start_period=1, grace_periods=0):
    r = rate / periods_per_year
    rows = []
    balance = float(principal)
    payment = abs(C.pmt(r, periods, principal)) if (amortising and r) else (principal / periods if amortising else principal * r)
    for p in range(1, int(periods) + 1):
        interest = balance * r
        if p <= grace_periods:
            principal_paid = 0.0
            cash = interest
        elif amortising:
            principal_paid = min(balance, payment - interest)
            cash = interest + principal_paid
        else:
            principal_paid = 0.0
            cash = interest
        closing = balance - principal_paid
        rows.append({"period": p, "opening": round(balance, 2), "interest": round(interest, 2),
                     "principal": round(principal_paid, 2), "cash_due": round(cash, 2),
                     "closing": round(closing, 2)})
        balance = closing
    if not amortising:
        rows[-1]["principal"] = round(balance, 2)
        rows[-1]["cash_due"] = round(rows[-1]["interest"] + balance, 2)
        rows[-1]["closing"] = 0.0
    return {"rows": rows, "periodic_payment": round(payment, 2), "total_interest": round(sum(r["interest"] for r in rows), 2),
            "total_cash": round(sum(r["cash_due"] for r in rows), 2), "final_balance": rows[-1]["closing"],
            "policy": "Amortising (or interest-only bullet) schedule at the supplied rate; covenant tests are separate."}


def financial_ratios(income, balance, periods_per_year=None):
    """income/balance: dicts of statement line values."""
    def g(d, *names, default=0.0):
        for n in names:
            if n in d and d[n] is not None:
                return float(d[n])
        return default

    revenue = g(income, "revenue", "sales")
    gross = g(income, "gross_profit")
    net = g(income, "net_profit", "net_income")
    ebit = g(income, "operating_profit", "ebit")
    interest = g(income, "interest_expense")
    assets = g(balance, "assets", "total_assets")
    current_assets = g(balance, "current_assets")
    inventory = g(balance, "inventory")
    current_liabilities = g(balance, "current_liabilities")
    equity = g(balance, "equity", "total_equity")
    liabilities = g(balance, "liabilities", "total_liabilities")
    receivables = g(balance, "receivables")
    ratios = {
        "gross_margin": C.safe_div(gross, revenue),
        "net_margin": C.safe_div(net, revenue),
        "return_on_assets": C.safe_div(net, assets),
        "return_on_equity": C.safe_div(net, equity),
        "current_ratio": C.safe_div(current_assets, current_liabilities),
        "quick_ratio": C.safe_div(current_assets - inventory, current_liabilities),
        "debt_to_equity": C.safe_div(liabilities, equity),
        "interest_cover": C.safe_div(ebit, interest) if interest else None,
        "asset_turnover": C.safe_div(revenue, assets),
        "receivable_days": C.safe_div(receivables * 365, revenue),
    }
    warnings = []
    if not revenue:
        warnings.append("Revenue missing or zero: margin and turnover ratios are not meaningful.")
    if not equity:
        warnings.append("Equity missing or zero: returns and leverage ratios are undefined.")
    return {"ratios": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in ratios.items()},
            "inputs_used": {"revenue": revenue, "net_profit": net, "assets": assets, "equity": equity,
                            "liabilities": liabilities, "current_assets": current_assets,
                            "current_liabilities": current_liabilities},
            "warnings": warnings,
            "note": "Ratio definitions vary by convention; the formula used is listed in knowledge-readme.md."}


def kpi_evaluate(actual, target, tolerance=None, direction="higher_is_better", name="kpi"):
    a = C.to_number(actual)
    t = C.to_number(target)
    if a is None or t is None:
        raise SkillToolError("BAD_ARGUMENT", "actual and target must be numeric.")
    delta = a - t
    pct = C.safe_div(delta, abs(t)) if t else None
    better = delta > 0 if direction == "higher_is_better" else delta < 0
    tol = float(tolerance) if tolerance is not None else 0.0
    if abs(delta) <= tol:
        status = "on_target"
    else:
        status = "ahead" if better else "behind"
    return {"name": name, "actual": a, "target": t, "variance": round(delta, 4),
            "variance_pct": pct, "direction": direction, "tolerance": tol, "status": status,
            "definition_note": "The KPI formula and grain must be documented before this status is published."}


def revenue_recognition(total_consideration, periods, method="straight_line", milestones=None, start=None):
    total = float(total_consideration)
    if total <= 0 or periods <= 0:
        raise SkillToolError("BAD_ARGUMENT", "total_consideration and periods must be positive.")
    rows = []
    if method == "straight_line":
        per = round(total / periods, 2)
        cum = 0.0
        for p in range(1, int(periods) + 1):
            amount = per if p < periods else round(total - cum, 2)
            cum += amount
            rows.append({"period": p, "recognised": amount, "cumulative": round(cum, 2),
                         "remaining": round(total - cum, 2)})
    elif method == "milestones":
        if not milestones:
            raise SkillToolError("BAD_ARGUMENT", "milestones method requires --milestones weights per period.")
        weights = C.nums(milestones)
        if len(weights) != periods:
            raise SkillToolError("BAD_ARGUMENT", f"Expected {periods} milestone weights, got {len(weights)}.")
        s = sum(weights) or 1.0
        cum = 0.0
        for p, w in enumerate(weights, start=1):
            amount = round(total * w / s, 2)
            if p == periods:
                amount = round(total - cum, 2)
            cum += amount
            rows.append({"period": p, "recognised": amount, "cumulative": round(cum, 2),
                         "remaining": round(total - cum, 2)})
    else:
        raise SkillToolError("BAD_ARGUMENT", f"Unknown recognition method {method!r}.",
                             "Use straight_line or milestones.")
    return {"method": method, "rows": rows, "total": round(sum(r["recognised"] for r in rows), 2),
            "policy": "Allocation follows the supplied policy. Performance obligations and stand-alone selling "
                      "prices must be documented per the applicable standard (IFRS 15 / ASC 606)."}
