"""交叉行断言：承运商与分区引用、多件运单与重复运单、时区写法混用。

采信模块只做单行解析，跨行与跨表的判定集中在这里，保证原因码只有一处产出。

同一运单号出现多行时按载荷分三种情况：
- 票级字段一致、逐件测量不同：同一票的多件，逐件保留并编件号（MULTI_PIECE）
- 票级字段与逐件测量都一致：重复记录，只留首条（DUPLICATE_WAYBILL）
- 票级字段冲突：无法并成一票，只留首条（DUP_CONFLICT）
"""

from __future__ import annotations

# 票级字段：同一票的所有件必须一致，不一致即冲突，无法并票
SHIPMENT_FIELDS = (
    "carrier", "service", "zone", "pickup_utc", "declared_value",
)

# 逐件字段：每件各自的测量值，多件运单按行各留一份证据
PIECE_FIELDS = (
    "weight_kg", "length_cm", "width_cm", "height_cm",
)


def _payload(draft: dict, fields: tuple[str, ...]) -> tuple:
    return tuple(str(draft.get(field)) for field in fields)


def shipment_payload(draft: dict) -> tuple:
    return _payload(draft, SHIPMENT_FIELDS)


def piece_payload(draft: dict) -> tuple:
    return _payload(draft, PIECE_FIELDS)


def payload(draft: dict) -> tuple:
    return shipment_payload(draft) + piece_payload(draft)


def check_reference(draft: dict, keys: set[tuple[str, str, str]],
                    known_carriers: set[str]) -> list[str]:
    """承运商与分区必须能在运价表里找到，否则计费无依据。"""
    carrier = draft.get("carrier") or ""
    service = draft.get("service") or ""
    zone = draft.get("zone") or ""
    if not carrier or not service or not zone:
        return []
    if carrier not in known_carriers:
        return ["UNKNOWN_CARRIER"]
    if (carrier, service, zone) not in keys:
        return ["RATE_MISSING"]
    return []


def dedupe(drafts: list[dict]) -> int:
    """就地追加重复/多件判定；返回被判重复的行数。

    先出现的保留。后出现的行：票级字段冲突记 DUP_CONFLICT，逐件测量也完全一致记
    DUPLICATE_WAYBILL，两种都只保留首条；票级一致而测量不同则是同一票的另一件，
    予以保留并在事后统一编件号、加 MULTI_PIECE 标记。
    已被其它原因阻断的行不参与登记，避免一条坏行污染后续好行。
    """
    registry: dict[str, dict] = {}
    hits = 0
    for draft in drafts:
        if draft["blocks"]:
            continue
        entry = registry.get(draft["waybill_no"])
        if entry is None:
            registry[draft["waybill_no"]] = {
                "shipment": shipment_payload(draft),
                "pieces": {piece_payload(draft)},
                "rows": [draft],
            }
            continue
        if entry["shipment"] != shipment_payload(draft):
            draft["blocks"].append("DUP_CONFLICT")
            hits += 1
            continue
        piece = piece_payload(draft)
        if piece in entry["pieces"]:
            draft["blocks"].append("DUPLICATE_WAYBILL")
            hits += 1
            continue
        entry["pieces"].add(piece)
        entry["rows"].append(draft)

    for entry in registry.values():
        rows = entry["rows"]
        multi = len(rows) > 1
        for seq, draft in enumerate(rows, start=1):
            draft["piece_seq"] = seq
            if multi:
                draft["codes"].append("MULTI_PIECE")
    return hits


def apply_tz_styles(drafts: list[dict], styles: set[str]) -> int:
    """同一列里既有 Z 又有 +hh:mm 时统一标记，不改变已归一的 UTC 值。"""
    if not {"z", "offset"} <= styles:
        return 0
    marked = 0
    for draft in drafts:
        if draft.get("pickup") is not None:
            draft["codes"].append("TZ_MIXED")
            marked += 1
    return marked
