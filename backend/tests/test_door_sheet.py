"""The printable door scan sheet, built from the seed's items and codes (no AWS)."""

from core.codes import CodeKind, classify_code
from door_sheet import build_sheet, qr_code
from seed import CODES, ITEMS

SHEET = build_sheet("Test Café", ITEMS, CODES)
INVENTORY_CODES = [code.code for code in CODES if classify_code(code.code) is CodeKind.INVENTORY]


def test_every_inv_code_gets_one_qr_code() -> None:
    for code in INVENTORY_CODES:
        assert f"<span>{code}</span>" in SHEET
    assert SHEET.count("<svg") == len(INVENTORY_CODES)


def test_qr_codes_are_regular_not_micro() -> None:
    # Phone cameras can't read Micro QR, which segno would otherwise pick for short text.
    assert not qr_code("INV-LID12-1BAG").is_micro


def test_supplier_barcodes_are_listed_not_printed() -> None:
    assert "078255001097" not in SHEET
    assert "012345678905" not in SHEET
    assert "Scan the barcode on the package for: Oat milk, Producers whole milk." in SHEET


def test_test_items_are_left_out() -> None:
    assert "candle" not in SHEET


def test_labels_say_what_a_scan_does() -> None:
    assert "<strong>Take 1 bag</strong>" in SHEET
    assert "<strong>Take 12 bags</strong>" in SHEET
    assert "<strong>Restock +12 bags</strong>" in SHEET
    assert "<strong>Restock +4 gallons</strong>" in SHEET
