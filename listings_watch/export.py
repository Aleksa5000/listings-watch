"""CSV and Excel output. Scraped text is untrusted, so spreadsheet formulas are neutralised."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Font

from listings_watch.models import Listing

COLUMNS = [
    "source",
    "external_id",
    "title",
    "price",
    "currency",
    "url",
    "status",
    "first_seen",
    "last_seen",
    "duplicate_of",
    "duplicate_score",
    "extra",
]


def _cell(value):
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        # stops Excel/Sheets from running a scraped "=HYPERLINK(...)" as a formula
        return "'" + value
    return value


def _rows(listings: list[Listing]):
    for item in listings:
        yield [_cell(getattr(item, column)) for column in COLUMNS]


def to_csv(listings: list[Listing]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    writer.writerows(_rows(listings))
    return buffer.getvalue()


def to_xlsx(listings: list[Listing]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "listings"
    sheet.append(COLUMNS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in _rows(listings):
        sheet.append(row)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for column in sheet.columns:
        width = max(len(str(c.value)) if c.value is not None else 0 for c in column[:200])
        sheet.column_dimensions[column[0].column_letter].width = min(max(width + 2, 10), 60)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
