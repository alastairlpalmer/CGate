"""The horse page's "Weight and condition" card (health.models.HorseMeasurement).

summary(horse) answers the latest value of each measure with its date and
the change since the one before, the last rows for the list, and the
weight chart: one series, so no legend; a 2px line, 8px points with a
surface ring, a hover label on each point, the latest value labelled at
the end, the lowest and highest weights at the left and the first and
last dates under it, all in HTML text. The list under the chart is the
table view.
"""

from decimal import Decimal

from django.utils.dateformat import format as date_format

ROWS = 6
CHART_POINTS = 24
PLOT_H = 84          # the plot's height in px; text is HTML, so it never scales
PAD_Y = 6            # room for the 8px points at the top and bottom
PAD_X = 2.5          # % at each end, so the end points are not cut


def _latest(rows, field):
    """The newest row with the field filled in: value, date, and the change
    since the one before (more / less), or None."""
    filled = [r for r in rows if getattr(r, field) is not None]
    if not filled:
        return None
    value = getattr(filled[0], field)
    change = value - getattr(filled[1], field) if len(filled) > 1 else None
    return {'value': value, 'date': filled[0].date, 'change': change,
            'change_abs': abs(change) if change is not None else None,
            'since': filled[1].date if len(filled) > 1 else None}


def _num(value):
    """512.0 -> '512', 512.5 -> '512.5'."""
    text = f'{Decimal(value):f}'
    return text.rstrip('0').rstrip('.') if '.' in text else text


def weight_chart(rows):
    """The chart from rows newest first, or None with fewer than two
    weights. x is a % of the plot's width and y is px, so the line and the
    points keep their size at every width (SVG lines and circles take %)."""
    points = [r for r in rows if r.weight_kg is not None][:CHART_POINTS][::-1]
    if len(points) < 2:
        return None
    first, last = points[0].date, points[-1].date
    span_days = max((last - first).days, 1)
    weights = [float(p.weight_kg) for p in points]
    low, high = min(weights), max(weights)
    if high - low < 10:  # a flat line drawn as a flat line, not a cliff
        middle = (high + low) / 2
        low, high = middle - 5, middle + 5
    inner_h = PLOT_H - 2 * PAD_Y
    marks = []
    for p, kg in zip(points, weights):
        x = PAD_X + (100 - 2 * PAD_X) * (p.date - first).days / span_days
        y = PAD_Y + inner_h * (1 - (kg - low) / (high - low))
        marks.append({'x': round(x, 2), 'y': round(y, 1), 'kg': _num(p.weight_kg),
                      'label': f"{date_format(p.date, 'j M Y')}: {_num(p.weight_kg)} kg"})
    segments = [{'x1': a['x'], 'y1': a['y'], 'x2': b['x'], 'y2': b['y']} for a, b in zip(marks, marks[1:])]
    return {
        'height': PLOT_H, 'segments': segments, 'marks': marks, 'last': marks[-1],
        'low': round(low), 'high': round(high), 'y_low': PLOT_H - PAD_Y, 'y_high': PAD_Y,
        'first_date': date_format(first, 'j M Y'), 'last_date': date_format(last, 'j M Y'),
        'summary': (f"Weight from {date_format(first, 'j M Y')} to {date_format(last, 'j M Y')}: "
                    f"{_num(points[0].weight_kg)} kg to {_num(points[-1].weight_kg)} kg, "
                    f"{len(points)} measurements."),
    }


def summary(horse):
    rows = list(horse.measurements.all()[:max(ROWS, CHART_POINTS)])
    if not rows:
        return None
    return {
        'weight': _latest(rows, 'weight_kg'),
        'height': _latest(rows, 'height_inches'),
        'body_condition': _latest(rows, 'body_condition'),
        'girth': _latest(rows, 'girth_cm'),
        'rows': rows[:ROWS],
        'count': horse.measurements.count(),
        'chart': weight_chart(rows),
    }
