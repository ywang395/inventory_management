import pytest

from core.codes import CodeKind, classify_code, normalize_code


@pytest.mark.parametrize(
    "raw, kind",
    [
        ("INV-LID12-1BAG", CodeKind.INVENTORY),
        ("INV-LID12-RESTOCK-BOX", CodeKind.INVENTORY),
        ("INV-UNDO", CodeKind.UNDO),
        ("STAFF-001", CodeKind.STAFF),
        ("96385074", CodeKind.UPC),  # 8 digits (UPC-E / EAN-8)
        ("012345678905", CodeKind.UPC),  # 12 digits (UPC-A)
        ("4006381333931", CodeKind.UPC),  # 13 digits (EAN-13)
    ],
)
def test_known_codes(raw: str, kind: CodeKind) -> None:
    assert classify_code(raw) is kind


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "INV-",  # prefix with nothing after it
        "STAFF-",
        "hello",
        "12345",  # too short for a UPC
        "01234567890",  # 11 digits
        "https://example.com/menu",  # a customer's QR code
        "０１２３４５６７８９０５",  # full-width digits, not a real barcode scan
    ],
)
def test_other_codes_are_ignored(raw: str) -> None:
    assert classify_code(raw) is CodeKind.IGNORED


def test_scanner_newline_and_lowercase_are_tolerated() -> None:
    assert normalize_code("  inv-lid12-1bag\n") == "INV-LID12-1BAG"
    assert classify_code("inv-undo\r\n") is CodeKind.UNDO
