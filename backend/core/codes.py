"""Recognise what kind of code was scanned."""

from enum import StrEnum

UNDO_CODE = "INV-UNDO"
INVENTORY_PREFIX = "INV-"
STAFF_PREFIX = "STAFF-"
# UPC-E / EAN-8, UPC-A and EAN-13 supplier barcodes.
UPC_LENGTHS = {8, 12, 13}


class CodeKind(StrEnum):
    UNDO = "undo"
    INVENTORY = "inventory"
    STAFF = "staff"
    UPC = "upc"
    IGNORED = "ignored"


def normalize_code(raw: str) -> str:
    """USB scanners type the code followed by Enter, so trim whitespace and fix case."""
    return raw.strip().upper()


def classify_code(raw: str) -> CodeKind:
    code = normalize_code(raw)
    if code == UNDO_CODE:
        return CodeKind.UNDO
    if code.startswith(INVENTORY_PREFIX) and len(code) > len(INVENTORY_PREFIX):
        return CodeKind.INVENTORY
    if code.startswith(STAFF_PREFIX) and len(code) > len(STAFF_PREFIX):
        return CodeKind.STAFF
    # isascii() rules out other digit characters like "²" that isdigit() accepts.
    if code.isascii() and code.isdigit() and len(code) in UPC_LENGTHS:
        return CodeKind.UPC
    return CodeKind.IGNORED
