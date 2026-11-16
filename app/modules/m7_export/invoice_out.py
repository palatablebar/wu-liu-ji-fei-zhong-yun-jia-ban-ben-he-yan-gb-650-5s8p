"""账单导出与运行清单：账单明细、按承运商汇总、可复算的清单 JSON。

账单按票出一行：整票一次首重、一次进位后的计费重与五个分项；件数列给出该票
的逐件行数，逐件证据在 evidence.csv。
"""

from __future__ import annotations

import csv
import io
import json
from decimal import Decimal

from app import config, manifest, store
from app.modules.m6_reconcile import service as reconcile_service

INVOICE_HEADER = [
    "waybill_no", "pieces", "carrier", "service", "zone", "pickup_utc", "billable_kg",
    "version_id", "base_amount", "step_amount", "remote_amount", "fuel_amount",
    "insurance_amount", "total_amount", "currency",
]


def currency_of() -> str:
    for card in store.rate_cards():
        return card["currency"]
    return "CNY"


def invoice_rows(run_id: str) -> list[dict]:
    currency = currency_of()
    weights = {row["waybill_no"]: row for row in store.weight_lines(run_id)}
    waybills: dict[str, dict] = {}
    for row in store.waybills(run_id):
        waybills.setdefault(row["waybill_no"], row)
    rows = []
    for charge in store.charge_lines(run_id):
        waybill_no = charge["waybill_no"]
        weight = weights.get(waybill_no, {})
        waybill = waybills.get(waybill_no, {})
        rows.append({
            "waybill_no": waybill_no,
            "pieces": int(weight.get("pieces") or 1),
            "carrier": waybill.get("carrier", ""),
            "service": waybill.get("service", ""),
            "zone": waybill.get("zone", ""),
            "pickup_utc": waybill.get("pickup_utc", ""),
            "billable_kg": weight.get("billable_kg", ""),
            "version_id": charge["version_id"],
            "base_amount": charge["base_amount"],
            "step_amount": charge["step_amount"],
            "remote_amount": charge["remote_amount"],
            "fuel_amount": charge["fuel_amount"],
            "insurance_amount": charge["insurance_amount"],
            "total_amount": charge["total_amount"],
            "currency": currency,
            "_line_no": waybill.get("line_no", 0),
        })
    rows.sort(key=lambda row: (row["_line_no"], row["waybill_no"]))
    for row in rows:
        row.pop("_line_no", None)
    return rows


def invoice_csv(run_id: str) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(INVOICE_HEADER)
    for row in invoice_rows(run_id):
        writer.writerow([row[key] for key in INVOICE_HEADER])
    return buffer.getvalue()


def carrier_summary(run_id: str) -> list[dict]:
    buckets: dict[str, dict] = {}
    for row in invoice_rows(run_id):
        bucket = buckets.setdefault(row["carrier"], {
            "carrier": row["carrier"],
            "count": 0,
            "total": Decimal("0.00"),
            "currency": row["currency"],
        })
        bucket["count"] += 1
        bucket["total"] += Decimal(row["total_amount"])
    summary = []
    for bucket in sorted(buckets.values(), key=lambda b: b["carrier"]):
        summary.append({
            "carrier": bucket["carrier"],
            "count": bucket["count"],
            "total": f"{bucket['total']:,.2f}",
            "currency": bucket["currency"],
        })
    return summary


def manifest_doc(run_id: str) -> dict:
    run = store.get_run(run_id) or {}
    report = reconcile_service.reconcile(run_id)
    invoices = invoice_rows(run_id)
    doc = manifest.build(run, {
        "rows": {
            "input": int(run.get("rows_in") or 0),
            "billed": len(invoices),
            "pieces": sum(row["pieces"] for row in invoices),
            "quarantined": int(run.get("rows_quarantined") or 0),
        },
        "amount": {
            "total": report["total"],
            "conserved": report["conserved"],
        },
        "reconcile": report["verdict"],
        "rate_set_version": store.max_rate_set_version(),
        "params": {"profile": config.PROFILE, "quant": "0.01", "step": "0.5"},
        "export": {"invoice_rows": len(invoices)},
    })
    return doc


def manifest_json(run_id: str) -> str:
    return json.dumps(manifest_doc(run_id), ensure_ascii=False, indent=2, sort_keys=True, default=str)
