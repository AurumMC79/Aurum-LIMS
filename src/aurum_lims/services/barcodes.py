"""Reading scanned barcodes into the Sample Intaking form.

USB and Bluetooth barcode scanners normally behave like a keyboard: they type
the code and press Enter. The intake form's Scan box receives that text and the
laboratory's scan settings (saved with the form) decide which fields it fills:

* **GS1** – the standard on most supplier containers (GS1-128, GS1 DataMatrix,
  GS1 QR). One code carries several values, each introduced by an
  *application identifier*, e.g. ``(01)`` product number (GTIN), ``(10)`` lot,
  ``(17)`` expiry date. Both the printed form with brackets and the raw form
  (values separated by the GS character, ASCII 29) are understood. Some
  scanners cannot type GS and send a substitute character instead (configurable).
* **Separated values** – e.g. ``LACTOSE|L-2210|500``: position 1, 2, 3 … each
  fill a chosen field.
* **Single value** – the whole code fills one field.
* **Automatic** – GS1 when the code looks like GS1, otherwise separated values
  when the separator is present, otherwise the single-value field.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum

GS = "\x1d"
AIM_PREFIX = re.compile(r"^\][A-Za-z][0-9A-Za-z]")  # symbology identifier some scanners add, e.g. ]d2, ]C1, ]Q3
GS1_SYMBOLOGIES = ("]C1", "]e0", "]d2", "]Q3", "]J1")


class ScanMode(StrEnum):
    AUTO = "auto"
    GS1 = "gs1"
    DELIMITED = "delimited"
    SINGLE = "single"

    @property
    def label(self) -> str:
        return {"auto": "Automatic (GS1, then separated values, then single value)", "gs1": "GS1 supplier barcodes",
                "delimited": "Values split by a separator", "single": "Single value"}[self.value]


@dataclass(frozen=True)
class AI:
    code: str
    title: str
    length: int | None  # fixed length of the data, or None for variable (up to max_length)
    max_length: int = 0
    kind: str = "text"  # text | date | number


_AIS = [
    AI("00", "Serial shipping container code (SSCC)", 18), AI("01", "Product number (GTIN)", 14),
    AI("02", "GTIN of contained items", 14), AI("10", "Batch / lot number", None, 20),
    AI("11", "Production date", 6, kind="date"), AI("13", "Packaging date", 6, kind="date"),
    AI("15", "Best before date", 6, kind="date"), AI("16", "Sell by date", 6, kind="date"),
    AI("17", "Expiry date", 6, kind="date"), AI("20", "Product variant", 2),
    AI("21", "Serial number", None, 20), AI("22", "Consumer product variant", None, 20),
    AI("30", "Variable count of items", None, 8, "number"), AI("37", "Count of trade items", None, 8, "number"),
    AI("240", "Additional product identification", None, 30), AI("241", "Customer part number", None, 30),
    AI("250", "Secondary serial number", None, 30), AI("251", "Reference to source entity", None, 30),
    AI("400", "Customer's purchase order number", None, 30), AI("401", "Consignment number", None, 30),
    AI("403", "Routing code", None, 30), AI("7003", "Expiration date and time", 10),
    *[AI(f"9{n}", f"Company internal information ({n})", None, 90) for n in range(10)],
]
# Measures: 310n net weight (kg), 311n length … 4-digit AI, last digit = decimal places, 6 digits of data.
_MEASURES = {"310": "Net weight (kg)", "311": "Length (m)", "315": "Net volume (L)", "316": "Net volume (m³)",
             "330": "Gross weight (kg)", "356": "Net weight (troy oz)"}
AIS: dict[str, AI] = {a.code: a for a in _AIS}
for prefix, title in _MEASURES.items():
    for decimals in range(6):
        AIS[f"{prefix}{decimals}"] = AI(f"{prefix}{decimals}", title, 6, kind="number")

# The identifiers offered in the scan settings (the rest are still read and shown).
COMMON_AIS = ["01", "10", "17", "11", "15", "21", "30", "37", "240", "400", "3100", "3150"]


def ai_title(code: str) -> str:
    return AIS[code].title if code in AIS else f"AI ({code})"


@dataclass
class ScanSettings:
    enabled: bool = False
    mode: str = ScanMode.AUTO
    gs1_fields: dict[str, str] = field(default_factory=dict)  # AI → form field key
    separator: str = "|"
    positions: list[str] = field(default_factory=list)  # position 1, 2 … → form field key ("" = skip)
    single_field: str = ""
    gs_substitute: str = ""  # character the scanner sends instead of GS (ASCII 29), if any

    def used_keys(self) -> set[str]:
        return {k for k in (*self.gs1_fields.values(), *self.positions, self.single_field) if k}


# ---------------------------------------------------------------------------
# GS1
# ---------------------------------------------------------------------------


def _gs1_date(text: str) -> date | None:
    """GS1 dates are YYMMDD; day 00 means the last day of the month."""
    if not re.fullmatch(r"\d{6}", text):
        return None
    yy, mm, dd = int(text[:2]), int(text[2:4]), int(text[4:])
    year = 2000 + yy if yy < 51 else 1900 + yy  # GS1 sliding window, simplified
    if not 1 <= mm <= 12:
        return None
    if dd == 0:
        dd = calendar.monthrange(year, mm)[1]
    try:
        return date(year, mm, dd)
    except ValueError:
        return None


def _measure(code: str, digits: str) -> str:
    decimals = int(code[-1])
    if not digits.isdigit():
        return digits
    if decimals == 0:
        return str(int(digits))
    whole, fraction = digits[:-decimals], digits[-decimals:]
    return f"{int(whole or '0')}.{fraction}"


def parse_gs1(text: str) -> dict[str, str] | None:
    """The application identifiers and their data, or None if the text is not GS1."""
    text = text.strip()
    if text.startswith(GS1_SYMBOLOGIES):
        text = text[3:]
    if text.startswith("("):
        pairs = re.findall(r"\((\d{2,4})\)([^(]*)", text)
        if not pairs or "".join(f"({a}){v}" for a, v in pairs) != text:
            return None
        result = {a: v.strip() for a, v in pairs}
        for ai, value in result.items():
            spec = AIS.get(ai)
            if spec is not None and spec.length is not None and len(value) != spec.length:
                return None
        return result
    result: dict[str, str] = {}
    position = 0
    text = text.lstrip(GS)
    while position < len(text):
        ai = next((text[position:position + n] for n in (2, 3, 4) if text[position:position + n] in AIS), None)
        if ai is None:
            return None
        position += len(ai)
        spec = AIS[ai]
        if spec.length is not None:
            value = text[position:position + spec.length]
            if len(value) != spec.length:
                return None
            position += spec.length
            if position < len(text) and text[position] == GS:
                position += 1
        else:
            end = text.find(GS, position)
            end = len(text) if end < 0 else end
            value = text[position:end]
            if not value or len(value) > spec.max_length:
                return None
            position = end + 1
        result[ai] = value
    return result or None


def gs1_value(ai: str, raw: str):
    """A GS1 value as a Python value: dates as date, measures with their decimal point, others as text."""
    spec = AIS.get(ai)
    if spec is None:
        return raw
    if spec.kind == "date":
        return _gs1_date(raw) or raw
    if spec.kind == "number" and len(ai) == 4:
        return _measure(ai, raw)
    if spec.kind == "number":
        return raw.lstrip("0") or "0"
    if ai == "7003" and re.fullmatch(r"\d{10}", raw):
        day = _gs1_date(raw[:6])
        return datetime(day.year, day.month, day.day, int(raw[6:8]), int(raw[8:])) if day else raw
    return raw


# ---------------------------------------------------------------------------
# reading a scan with the settings
# ---------------------------------------------------------------------------


@dataclass
class ScanResult:
    code: str
    kind: str  # "GS1", "separated values", "single value" or "not recognised"
    values: dict[str, object] = field(default_factory=dict)  # form field key → value to fill in
    found: list[tuple[str, str]] = field(default_factory=list)  # (what, value) for showing, incl. unused parts
    unused: list[str] = field(default_factory=list)  # parts of the code no field takes

    @property
    def readable_code(self) -> str:
        return self.code.replace(GS, "<GS>")


def _clean(code: str, settings: ScanSettings) -> str:
    code = code.strip("\r\n")
    if settings.gs_substitute:
        code = code.replace(settings.gs_substitute, GS)
    if AIM_PREFIX.match(code) and not code.startswith(GS1_SYMBOLOGIES):
        code = code[3:]
    return code


def read_scan(settings: ScanSettings, code: str) -> ScanResult:
    code = _clean(code, settings)
    mode = ScanMode(settings.mode)
    gs1 = parse_gs1(code) if mode in (ScanMode.AUTO, ScanMode.GS1) else None
    if gs1 is not None:
        result = ScanResult(code, "GS1")
        for ai, raw in gs1.items():
            value = gs1_value(ai, raw)
            shown = value.isoformat() if isinstance(value, date) else str(value)
            result.found.append((f"({ai}) {ai_title(ai)}", shown))
            key = settings.gs1_fields.get(ai)
            if key:
                result.values[key] = value
            else:
                result.unused.append(f"({ai}) {ai_title(ai)}")
        return result
    if mode is ScanMode.GS1:
        return ScanResult(code, "not recognised", unused=["The code is not a GS1 barcode."])
    separator = settings.separator or "|"
    if mode is ScanMode.DELIMITED or (mode is ScanMode.AUTO and separator in code and settings.positions):
        result = ScanResult(code, "separated values")
        for index, part in enumerate(code.split(separator)):
            part = part.strip()
            result.found.append((f"Position {index + 1}", part))
            key = settings.positions[index] if index < len(settings.positions) else ""
            if key and part:
                result.values[key] = part
            elif part:
                result.unused.append(f"Position {index + 1}")
        return result
    result = ScanResult(code, "single value", found=[("Whole code", code.strip())])
    if settings.single_field:
        result.values[settings.single_field] = code.strip()
    else:
        result.kind = "not recognised"
        result.unused.append("No field is set to receive a single-value code.")
    return result


def parse_date_text(text: str) -> date | None:
    """Dates found in separated-value codes: 2026-12-31, 31.12.2026, 31/12/2026, 20261231 or GS1-style 261231."""
    text = text.strip()
    if text.isdigit():  # digits only: the length decides (YYYYMMDD or YYMMDD)
        if len(text) == 8:
            try:
                return datetime.strptime(text, "%Y%m%d").date()
            except ValueError:
                return None
        return _gs1_date(text) if len(text) == 6 else None
    for pattern in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None
