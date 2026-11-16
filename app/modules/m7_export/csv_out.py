"""证据行导出：一件一行，原始行号 + 件号 + 逐件取大依据 + 整票账单，供人工复核。

逐件列（actual/volumetric/source/chargeable）来自 piece_lines，是该件自己的测量与
判定；整票列（billable/steps/金额）是该票的计算结果，同一票的各件行上一致。
"""

from __future__ import annotations

import csv
import io

from app import store
from app.reasons import label

HEADER = [
    "line_no", "waybill_no", "piece_seq", "carrier", "service", "zone", "pickup_utc",
    "actual_kg", "volumetric_kg", "source", "chargeable_kg", "billable_kg", "steps",
    "version_id",
    "base_amount", "step_amount", "remote_amount", "fuel_amount", "insurance_amount",
    "total_amount", "reason_codes", "reason_labels",
]


def _merge_codes(*groups) -> list[str]:
    """逐件与账单两段的原因码合并，保持首次出现顺序。"""
    merged: list[str] = []
    for group in groups:
        for code in (group or "").split(","):
            if code and code not in merged:
                merged.append(code)
    return merged


def rows(run_id: str) -> list[dict]:
    waybills = {row["line_no"]: row for row in store.waybills(run_id)}
    weights = {row["waybill_no"]: row for row in store.weight_lines(run_id)}
    charges = {row["waybill_no"]: row for row in store.charge_lines(run_id)}
    merged: list[dict] = []
    for piece in store.piece_lines(run_id):
        waybill_no = piece["waybill_no"]
        waybill = waybills.get(piece["line_no"], {})
        weight = weights.get(waybill_no, {})
        charge = charges.get(waybill_no, {})
        # VOLUMETRIC_APPLIED 由逐件行自己携带，账单侧的同款码不再并入，避免件级误标
        charge_codes = [
            code for code in (charge.get("codes") or "").split(",")
            if code and code != "VOLUMETRIC_APPLIED"
        ]
        codes = _merge_codes(piece.get("codes"), ",".join(charge_codes))
        merged.append({
            "line_no": piece.get("line_no", 0),
            "waybill_no": waybill_no,
            "piece_seq": piece.get("piece_seq", 1),
            "carrier": waybill.get("carrier", ""),
            "service": waybill.get("service", ""),
            "zone": waybill.get("zone", ""),
            "pickup_utc": waybill.get("pickup_utc", ""),
            "actual_kg": piece.get("actual_kg", ""),
            "volumetric_kg": piece.get("volumetric_kg", ""),
            "source": piece.get("source", ""),
            "chargeable_kg": piece.get("chargeable_kg", ""),
            "billable_kg": weight.get("billable_kg", ""),
            "steps": weight.get("steps", 0),
            "version_id": weight.get("version_id", ""),
            "base_amount": charge.get("base_amount", "0.00"),
            "step_amount": charge.get("step_amount", "0.00"),
            "remote_amount": charge.get("remote_amount", "0.00"),
            "fuel_amount": charge.get("fuel_amount", "0.00"),
            "insurance_amount": charge.get("insurance_amount", "0.00"),
            "total_amount": charge.get("total_amount", "0.00"),
            "reason_codes": " ".join(codes),
            "reason_labels": " ".join(label(c) for c in codes),
        })
    merged.sort(key=lambda row: (row["line_no"], row["piece_seq"]))
    return merged


def csv_text(run_id: str) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(HEADER)
    for row in rows(run_id):
        writer.writerow([row[key] for key in HEADER])
    return buffer.getvalue()
