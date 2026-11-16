"""四账：运单行账、金额账、版本账、计费重账。每账给出左右两侧与差额。

四本账口径各异但指向同一批数据：行账按采信行（逐件行），版本账按票，
计费重账按件，金额账按分。任一环节丢件、丢票或改数都会让某本账不平。
"""

from __future__ import annotations

from decimal import Decimal

from app import store
from app.modules.m4_weights import billable
from app.modules.m6_reconcile import totals


def _account(key: str, title: str, left_label: str, left_value, parts: list[dict],
             unit: str) -> dict:
    right = Decimal("0") if unit == "元" else 0
    for part in parts:
        right = right + part["value"]
    delta = left_value - right
    return {
        "key": key,
        "title": title,
        "left_label": left_label,
        "left_value": left_value,
        "parts": parts,
        "right_value": right,
        "delta": delta,
        "balanced": delta == 0,
        "unit": unit,
    }


def row_account(run: dict, quarantine_rows: list[dict], weight_rows: list[dict]) -> dict:
    """采信行（逐件行）= 计费行 + 隔离行（有未计费行时单列第三项）。"""
    quarantined = len({row["line_no"] for row in quarantine_rows})
    accepted = int(run.get("rows_ok") or 0)
    unbilled = sum(
        int(row["pieces"]) for row in weight_rows if row["source"] == billable.UNBILLED
    )
    parts = [
        {"label": "计费", "value": accepted - unbilled},
        {"label": "隔离", "value": quarantined},
    ]
    if unbilled:
        parts.append({"label": "未计费", "value": unbilled})
    return _account(
        "rows",
        "运单行账",
        "采信记录",
        int(run.get("rows_in") or 0),
        parts,
        "行",
    )


def amount_account(charge_rows: list[dict]) -> dict:
    parts_map = totals.parts_of(charge_rows)
    labels = totals.part_labels()
    parts = [
        {"label": labels[key], "value": value}
        for key, value in parts_map.items()
    ]
    return _account(
        "amount",
        "金额账",
        "应收合计",
        totals.grand_total(charge_rows),
        parts,
        "元",
    )


def version_account(run: dict, weight_rows: list[dict]) -> dict:
    """版本账按票：每票必须落到单一版本，多件不重复计票。"""
    total = len(weight_rows)
    hit = sum(1 for row in weight_rows if row["version_id"])
    miss = sum(1 for row in weight_rows if not row["version_id"])
    parts = [{"label": "单选命中", "value": hit}]
    if miss:
        parts.append({"label": "无命中或重叠", "value": miss})
    return _account("version", "版本账", "可计费运单", total, parts, "票")


def weight_account(piece_rows: list[dict]) -> dict:
    """计费重账按件：逐件取大的来源分类必须闭合。"""
    billed = [row for row in piece_rows if row["source"] != billable.UNBILLED]
    actual = sum(1 for row in billed if row["source"] in (billable.ACTUAL, billable.TIE))
    volumetric = sum(1 for row in billed if row["source"] == billable.VOLUMETRIC)
    parts = [
        {"label": "实重择用", "value": actual},
        {"label": "体积重择用", "value": volumetric},
    ]
    return _account("weight", "计费重账", "计费件数", len(billed), parts, "件")


def accounts(run: dict, quarantine_rows: list[dict], weight_rows: list[dict],
             charge_rows: list[dict], piece_rows: list[dict]) -> list[dict]:
    return [
        row_account(run, quarantine_rows, weight_rows),
        amount_account(charge_rows),
        version_account(run, weight_rows),
        weight_account(piece_rows),
    ]


def run_of(run_id: str) -> dict:
    return store.get_run(run_id) or {}
