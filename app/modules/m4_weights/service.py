"""M4 编排：按票分组，逐件取大后累加，整票一次进位，落库逐件行与计费重行。

计费口径：每件各自 max(实重, 体积重)，逐件取大值累加为整票计费重量，再按版本
台阶整票向上进位一次。首重与续重段数都由整票计费重决定，整票只收一次首重。
逐件的实重、体积重、来源与取大值落 piece_lines，仓库可逐件追溯。
"""

from __future__ import annotations

from decimal import Decimal

from app import manifest, store
from app.errors import DomainError
from app.modules.m2_rates import service as rates
from app.modules.m2_rates import version as rateversion
from app.modules.m4_weights import billable, rounding, volumetric

ZERO3 = Decimal("0.000")


def _group_by_shipment(rows: list[dict]) -> dict[str, list[dict]]:
    """同一运单号的逐件行归成一票，保持原始行号顺序。"""
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["waybill_no"], []).append(row)
    return groups


def compute_weights(run_id: str) -> dict:
    run = store.get_run(run_id)
    if not run:
        raise DomainError("RUN_NOT_FOUND", f"运行不存在：{run_id}", 404)
    rows = store.waybills(run_id)
    if not rows:
        raise DomainError("NO_ROWS", "该运行没有可计费的运单", 409)

    index_map = rates.index()
    lines: list[dict] = []
    piece_rows: list[dict] = []
    unbilled = 0
    for waybill_no, group in _group_by_shipment(rows).items():
        head = group[0]
        multi = len(group) > 1
        pickup = rateversion.parse_utc(head["pickup_utc"])
        card, codes = rates.select_card(index_map, head["carrier"], head["service"], head["zone"], pickup)
        if card is None:
            unbilled += 1
            actual_total = sum((Decimal(row["weight_kg"]) for row in group), ZERO3)
            lines.append({
                "waybill_no": waybill_no,
                "version_id": "",
                "pieces": len(group),
                "actual_kg": str(actual_total),
                "volumetric_kg": "0.000",
                "chargeable_kg": "0.000",
                "source": billable.UNBILLED,
                "billable_kg": "0.000",
                "steps": 0,
                "codes": ",".join(codes),
            })
            for row in group:
                piece_rows.append({
                    "waybill_no": waybill_no,
                    "piece_seq": row["piece_seq"],
                    "line_no": row["line_no"],
                    "actual_kg": row["weight_kg"],
                    "volumetric_kg": "0.000",
                    "source": billable.UNBILLED,
                    "chargeable_kg": "0.000",
                    "codes": ",".join(codes),
                })
            continue

        divisor = int(card["volume_divisor"])
        step = Decimal(card["step_weight_kg"])
        sources: list[str] = []
        actual_total = ZERO3
        volume_total = ZERO3
        chargeable_total = ZERO3
        for row in group:
            actual = Decimal(row["weight_kg"])
            volume = volumetric.volumetric_kg(
                Decimal(row["length_cm"]),
                Decimal(row["width_cm"]),
                Decimal(row["height_cm"]),
                divisor,
            )
            chargeable, source = billable.pick(actual, volume)
            sources.append(source)
            actual_total += actual
            volume_total += volume
            chargeable_total += chargeable
            piece_codes = billable.source_codes(source)
            if multi:
                piece_codes.append("MULTI_PIECE")
            piece_rows.append({
                "waybill_no": waybill_no,
                "piece_seq": row["piece_seq"],
                "line_no": row["line_no"],
                "actual_kg": str(actual),
                "volumetric_kg": str(volume),
                "source": source,
                "chargeable_kg": str(chargeable),
                "codes": ",".join(piece_codes),
            })

        billable_kg = rounding.ceil_to_step(chargeable_total, step)
        steps = rounding.steps_above(billable_kg, Decimal(card["first_weight_kg"]), step)
        line_codes = billable.shipment_codes(sources)
        if multi:
            line_codes.append("MULTI_PIECE")
        lines.append({
            "waybill_no": waybill_no,
            "version_id": card["version_id"],
            "pieces": len(group),
            "actual_kg": str(actual_total),
            "volumetric_kg": str(volume_total),
            "chargeable_kg": str(chargeable_total),
            "source": billable.shipment_source(sources),
            "billable_kg": str(billable_kg),
            "steps": steps,
            "codes": ",".join(line_codes),
        })

    store.replace_weight_lines(run_id, lines)
    store.replace_piece_lines(run_id, piece_rows)
    now = manifest.utc_now_iso()
    store.add_stage(run_id, "weights", now)
    return {
        "run_id": run_id,
        "billed": len(lines) - unbilled,
        "unbilled": unbilled,
        "pieces": len(piece_rows),
        "volumetric_hits": sum(1 for line in lines if line["source"] == billable.VOLUMETRIC),
        "mixed_hits": sum(1 for line in lines if line["source"] == billable.MIXED),
        "steps_total": sum(int(line["steps"]) for line in lines),
        "rate_digest": rates.rate_digest(),
    }


def lines(run_id: str) -> list[dict]:
    return store.weight_lines(run_id)


def pieces(run_id: str) -> list[dict]:
    return store.piece_lines(run_id)


def summary(run_id: str) -> dict:
    items = lines(run_id)
    piece_items = pieces(run_id) if items else []
    billed_pieces = [p for p in piece_items if p["source"] != billable.UNBILLED]
    return {
        "count": len(items),
        "billed": sum(1 for i in items if i["source"] != billable.UNBILLED),
        "unbilled": sum(1 for i in items if i["source"] == billable.UNBILLED),
        "volumetric_hits": sum(1 for i in items if i["source"] == billable.VOLUMETRIC),
        "actual_hits": sum(1 for i in items if i["source"] in (billable.ACTUAL, billable.TIE)),
        "mixed_hits": sum(1 for i in items if i["source"] == billable.MIXED),
        "pieces": len(billed_pieces),
        "piece_volumetric": sum(1 for p in billed_pieces if p["source"] == billable.VOLUMETRIC),
        "piece_actual": sum(1 for p in billed_pieces if p["source"] in (billable.ACTUAL, billable.TIE)),
        "billable_total": str(sum((Decimal(i["billable_kg"]) for i in items), Decimal("0.000"))),
    }
