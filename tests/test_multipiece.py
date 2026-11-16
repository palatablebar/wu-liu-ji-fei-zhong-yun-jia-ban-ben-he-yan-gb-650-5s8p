"""多件运单端到端：逐件取大后累加的计费口径、整票一次首重、逐件证据、幂等重跑。

场景：同一票两件，一件实重偏高（10 kg 小件），一件体积偏大（1 kg 但 1 m³）。
逐件各自取大后累加 = 10.000 + 166.667 = 176.667 → 进位 177.0 kg；
若整票合并后再比较只有 max(11.000, 166.834) = 166.834 → 167.0 kg。
本系统采用前者（与承运商逐件计费的口径一致），这些测试把口径钉死。
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal

import pytest
from fixtures import samples

from app import store
from app.modules.m1_waybill import service as waybills
from app.modules.m4_weights import billable
from app.modules.m4_weights import service as weights
from app.modules.m5_charges import service as charges
from app.modules.m6_reconcile import service as reconcile
from app.modules.m7_export import service as export

MIXED_ROWS = [
    # 件 1：实重 10 kg，体积重 0.167 kg → 按实重
    samples.row("WB-5001", weight="10", length="10", width="10", height="10", pieces="2"),
    # 件 2：实重 1 kg，体积重 166.667 kg → 按体积重
    samples.row("WB-5001", weight="1", length="100", width="100", height="100", pieces="2"),
]

# 逐件取大后累加：10.000 + 166.667 = 176.667 → 177.000 kg，352 段续重
BILLABLE_KG = "177.000"
STEPS = 352
# 首重 13.00 + 续重 352 × 2.60 = 915.20；燃油 (13.00 + 915.20) × 0.085 = 78.90
TOTAL = "1007.10"


def _run_flow(rows: list[str], name: str) -> str:
    ingest = waybills.import_bytes(samples.csv_bytes(rows), name)
    run_id = ingest["run_id"]
    weights.compute_weights(run_id)
    charges.compute_charges(run_id)
    return run_id


def _read(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


@pytest.fixture(scope="module")
def mixed_run(database):
    return _run_flow(MIXED_ROWS, "multipiece_mixed.csv")


def test_ingest_keeps_both_pieces(mixed_run):
    rows = store.waybills(mixed_run)
    assert len(rows) == 2
    assert {row["waybill_no"] for row in rows} == {"WB-5001"}
    assert [row["piece_seq"] for row in rows] == [1, 2]
    assert all("MULTI_PIECE" in row["codes"] for row in rows)
    assert store.quarantine(mixed_run) == []


def test_caliber_is_piece_max_then_sum(mixed_run):
    """口径：逐件取大后累加，而不是整票合并后再比较。"""
    line = store.weight_lines(mixed_run)[0]
    assert line["pieces"] == 2
    assert line["actual_kg"] == "11.000"
    assert line["volumetric_kg"] == "166.834"
    assert line["chargeable_kg"] == "176.667"
    assert line["billable_kg"] == BILLABLE_KG
    assert int(line["steps"]) == STEPS
    assert line["source"] == billable.MIXED
    # 反证：整票合并再比较会得到 167.0 kg，与落库值不同
    merged = max(Decimal(line["actual_kg"]), Decimal(line["volumetric_kg"]))
    assert str(merged) == "166.834"
    assert Decimal(line["billable_kg"]) > merged

    pieces = store.piece_lines(mixed_run)
    assert [(p["piece_seq"], p["source"], p["chargeable_kg"]) for p in pieces] == [
        (1, billable.ACTUAL, "10.000"),
        (2, billable.VOLUMETRIC, "166.667"),
    ]


def test_first_weight_charged_once_per_shipment(mixed_run):
    """财务口径：整票只收一次首重，只有一行账单。"""
    lines = store.charge_lines(mixed_run)
    assert len(lines) == 1
    line = lines[0]
    assert line["base_amount"] == "13.00"
    assert line["step_amount"] == "915.20"
    assert line["fuel_amount"] == "78.90"
    assert line["total_amount"] == TOTAL


def test_views_and_exports_read_the_same_result(mixed_run):
    """运单、计费重、账单、对账与导出读到同一结果。"""
    invoice = _read(export.invoice_csv(mixed_run))
    assert len(invoice) == 1
    assert invoice[0]["waybill_no"] == "WB-5001"
    assert invoice[0]["pieces"] == "2"
    assert invoice[0]["billable_kg"] == BILLABLE_KG
    assert invoice[0]["total_amount"] == TOTAL

    rows = _read(export.evidence_csv(mixed_run))
    assert len(rows) == 2
    assert [row["chargeable_kg"] for row in rows] == ["10.000", "166.667"]
    assert {row["billable_kg"] for row in rows} == {BILLABLE_KG}
    assert {row["total_amount"] for row in rows} == {TOTAL}

    report = reconcile.reconcile(mixed_run)
    assert report["verdict"]["balanced"] is True
    assert report["total"] == TOTAL


def test_rerun_same_batch_adds_nothing_and_changes_nothing(mixed_run):
    """重跑同一批数据：同一个 run_id，不新增账单，金额不变。"""
    before = {
        "charges": store.charge_lines(mixed_run),
        "weights": store.weight_lines(mixed_run),
        "pieces": store.piece_lines(mixed_run),
        "amount_total": (store.get_run(mixed_run) or {})["amount_total"],
    }

    again = waybills.import_bytes(samples.csv_bytes(MIXED_ROWS), "multipiece_mixed.csv")
    assert again["run_id"] == mixed_run
    assert again["reused"] is True
    weights.compute_weights(mixed_run)
    charges.compute_charges(mixed_run)

    assert store.charge_lines(mixed_run) == before["charges"]
    assert store.weight_lines(mixed_run) == before["weights"]
    assert store.piece_lines(mixed_run) == before["pieces"]
    assert (store.get_run(mixed_run) or {})["amount_total"] == before["amount_total"]
    assert len(store.charge_lines(mixed_run)) == 1


def test_single_piece_caliber_unchanged(database):
    """单件票退化为原口径：取大后整票进位，结果与逐件累加一致。"""
    run_id = _run_flow([samples.row("WB-5002", weight="2", length="40", width="30", height="25")],
                       "single_piece.csv")
    line = store.weight_lines(run_id)[0]
    assert line["pieces"] == 1
    assert line["chargeable_kg"] == "5.000"
    assert line["billable_kg"] == "5.000"
    piece = store.piece_lines(run_id)[0]
    assert piece["chargeable_kg"] == line["chargeable_kg"]
    assert "MULTI_PIECE" not in (piece["codes"] or "")


def test_multi_piece_unbilled_keeps_piece_evidence(database):
    """两件都落在版本窗口外：整票未计费，但逐件证据保留且四账仍平。"""
    rows = [
        samples.row("WB-5003", pickup="2025-12-01T10:00:00+08:00", weight="2", pieces="2"),
        samples.row("WB-5003", pickup="2025-12-01T10:00:00+08:00", weight="3", pieces="2"),
    ]
    run_id = _run_flow(rows, "multipiece_unbilled.csv")
    line = store.weight_lines(run_id)[0]
    assert line["source"] == billable.UNBILLED
    assert line["pieces"] == 2
    assert len(store.piece_lines(run_id)) == 2
    assert store.charge_lines(run_id) == []

    report = reconcile.reconcile(run_id)
    assert report["verdict"]["balanced"] is True
    accounts = {account["key"]: account for account in report["accounts"]}
    row_parts = {part["label"]: part["value"] for part in accounts["rows"]["parts"]}
    assert row_parts == {"计费": 0, "隔离": 0, "未计费": 2}
