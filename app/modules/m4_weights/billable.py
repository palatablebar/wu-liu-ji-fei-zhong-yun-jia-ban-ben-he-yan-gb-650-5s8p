"""计费重取大：逐件比大小，整票定来源，并留下依据来源。

计费口径（可向承运商解释）：每件各自取实重与体积重的较大值，再把各件的取大值
累加成整票计费重量。一票里可能有的件按实重、有的件按体积重，此时整票来源记
混合（mixed），逐件来源仍在 piece_lines 里可追。
"""

from __future__ import annotations

from decimal import Decimal

ACTUAL = "actual"
VOLUMETRIC = "volumetric"
TIE = "tie"
MIXED = "mixed"
UNBILLED = "unbilled"

SOURCE_LABEL = {
    ACTUAL: "实重",
    VOLUMETRIC: "体积重",
    TIE: "实重",
    MIXED: "混合取大",
    UNBILLED: "未计费",
}


def pick(actual_kg: Decimal, volumetric_kg: Decimal) -> tuple[Decimal, str]:
    if volumetric_kg > actual_kg:
        return volumetric_kg, VOLUMETRIC
    if volumetric_kg == actual_kg:
        return actual_kg, TIE
    return actual_kg, ACTUAL


def shipment_source(sources) -> str:
    """整票来源：全部体积重→体积重；全部实重/持平→实重；两者都有→混合。"""
    picked = [source for source in sources if source != UNBILLED]
    if not picked:
        return UNBILLED
    if all(source == VOLUMETRIC for source in picked):
        return VOLUMETRIC
    if all(source in (ACTUAL, TIE) for source in picked):
        return ACTUAL if ACTUAL in picked else TIE
    return MIXED


def source_codes(source: str) -> list[str]:
    return ["VOLUMETRIC_APPLIED"] if source == VOLUMETRIC else []


def shipment_codes(sources) -> list[str]:
    """整票标记：任一件用了体积重即记 VOLUMETRIC_APPLIED。"""
    return ["VOLUMETRIC_APPLIED"] if VOLUMETRIC in sources else []


def label(source: str) -> str:
    return SOURCE_LABEL.get(source, source)
