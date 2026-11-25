"""The day's lots as plain text, to send to the team
(docs/TRAINING_BOARD_REDESIGN_PLAN.md, 5.5).

Plain functions: the caller gives lot days whose ``rows`` are already read.
The text has no markup, so it reads the same in WhatsApp, Messages and Mail.
"""

from .models import Activity, LotDay


def _lot_head(lot_day):
    head = lot_day.lot.name
    if lot_day.start_time:
        head += f", {lot_day.start_time:%H:%M}"
    work = lot_day.work_type.name
    if lot_day.venue_id:
        work += f", {lot_day.venue.name}"
    line = f"{head}. {work}"
    if lot_day.status == LotDay.Status.CANCELLED:
        line += " (cancelled)"
    return line


def _row_line(number, row, lot_day):
    line = f"{number}. {row.horse.name}"
    if row.rider_label:
        line += f" - {row.rider_label}"
    if row.gear:
        line += f" ({row.gear})"
    extra = []
    if not row.follows_lot and row.work_type_id != lot_day.work_type_id:
        extra.append(row.work_type.name)        # the horse's own work
    if row.instructions.strip():
        extra.append(row.instructions.strip())
    if extra:
        line += '. ' + '. '.join(extra)
    return line


def lot_text(lot_day):
    """One lot: its time, work and venue, its note, then each horse that goes
    out with its rider, gear, own work and instruction. Horses that do not
    go out are on one line at the end, with the reason."""
    lines = [_lot_head(lot_day)]
    if lot_day.notes.strip():
        lines.append(lot_day.notes.strip())
    out, number = [], 0
    for row in lot_day.rows:
        if row.status == Activity.Status.NOT_DONE:
            out.append(f"{row.horse.name} ({row.get_reason_display().lower()})")
            continue
        number += 1
        lines.append(_row_line(number, row, lot_day))
    if not lot_day.rows:
        lines.append("No horses.")
    if out:
        lines.append("Not going out: " + ', '.join(out))
    return '\n'.join(lines)


def day_title(day):
    return f"Lots, {day:%a} {day.day} {day:%b}"


def board_text(day, lot_days, note=''):
    """The whole board for ``day``: the title, the note for the day, each lot."""
    head = day_title(day)
    if note and note.strip():
        head += '\n' + note.strip()
    return '\n\n'.join([head] + [lot_text(lot_day) for lot_day in lot_days])
