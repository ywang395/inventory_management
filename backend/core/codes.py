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
    """One spelling per code, whatever scanned it.

    - USB scanners type the code followed by Enter, so trim whitespace and fix case.
    - A UPC-A barcode (12 digits) is read as EAN-13 by some scanners, iPhones included:
      the same number with a 0 in front. Drop that 0 so both match the same code.
    """
    code = raw.strip().upper()
    if len(code) == 13 and code.isascii() and code.isdigit() and code.startswith("0"):
        return code[1:]
    return code


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
