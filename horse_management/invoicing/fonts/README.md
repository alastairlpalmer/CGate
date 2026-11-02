# PDF fonts

DejaVu Sans (regular and bold), from the `fonts-dejavu-core` package. The
invoice, statement and credit note PDFs use it instead of Helvetica, which
has no accented capitals beyond Western Europe, no Greek, Cyrillic, Arabic
or Hebrew, and printed those letters as black boxes.

Free to use and redistribute: see `LICENSE` (Bitstream Vera licence, with
the DejaVu changes in the public domain).

It has no Chinese, Japanese or Korean letters, and only some emoji. `invoicing/pdf.py`
prints `?` for a character the font does not have.
