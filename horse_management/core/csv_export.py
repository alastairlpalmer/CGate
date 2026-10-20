"""CSV files that open cleanly in Excel and cannot carry a formula.

* UTF-8 with a byte-order mark, so Excel shows "O'Brien", "Zoë" and
  "Ålesund" correctly instead of guessing a Windows code page.
* Every text cell goes through the formula guard: a cell that starts with
  ``=``, ``+``, ``-``, ``@`` is prefixed with an apostrophe, so a name
  typed as ``=HYPERLINK(...)`` stays text. Numbers and dates are left as
  they are (a credit's -5.00 must stay a number).
"""

import csv
import io
from datetime import date, datetime

from django.http import HttpResponse
from core.downloads import attachment_header

from invoicing.utils import _csv_safe


def _cell(value):
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d %H:%M')
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return _csv_safe(value)
    return value


def csv_bytes(header, rows) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    for row in rows:
        writer.writerow([_cell(v) for v in row])
    return ('﻿' + buffer.getvalue()).encode('utf-8')


def csv_response(filename_stem, header, rows) -> HttpResponse:
    """A download of ``rows`` as ``<filename_stem>.csv``.

    core.downloads cleans the name, so quotes, slashes or accents can never
    break the Content-Disposition header.
    """
    response = HttpResponse(csv_bytes(header, rows), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = attachment_header(f'{filename_stem or "export"}.csv')
    return response
