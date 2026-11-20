"""The pedigree diagram, worked out on the server (plan §8.3).

A sideways tree on a fixed CSS grid. Column 2k+1 holds generation k; the
even columns are the gaps that hold the lines. There are 2^G equal rows
for G generations. The box at generation k with index i (S = 0, D = 1,
read from the horse outwards) starts at row i * R/2^k + 1 and spans R/2^k
rows, and sits in the middle of them, so its middle is at (i + 0.5)/2^k
of the height. Nothing is measured in the browser.

Each gap is one SVG with viewBox 0 0 100 100, stretched to fit; a line
runs from a box's middle (x 0) to each parent's middle (x 100).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .models import PedigreeHorse

KNOWN, UNKNOWN, MISSING = 'known', 'unknown', 'missing'
MAX_GENERATIONS = 3


def relation(code: str) -> str:
    """'' -> 'This horse'; 'SD' -> "Sire's dam"."""
    if not code:
        return 'This horse'
    words = ['sire' if c == 'S' else 'dam' for c in code]
    words[0] = words[0].capitalize()
    return "'s ".join(words)


def index_of(code: str) -> int:
    """'DS' -> 2: S is 0 and D is 1, read as a binary number."""
    return int(code.replace('S', '0').replace('D', '1'), 2) if code else 0


def middle(code: str) -> float:
    """The vertical middle of a box, 0 to 100."""
    return (index_of(code) + 0.5) / (2 ** len(code)) * 100


def curve(parent_code: str, code: str) -> str:
    y1, y2 = middle(parent_code), middle(code)
    return f'M0 {y1:.3f} C55 {y1:.3f} 45 {y2:.3f} 100 {y2:.3f}'


@dataclass
class Box:
    code: str
    state: str
    record: PedigreeHorse | None = None
    twin: str = ''

    @property
    def generation(self):
        return len(self.code)

    @property
    def relation(self):
        return relation(self.code)

    @property
    def column(self):
        return 2 * self.generation + 1


@dataclass
class Line:
    code: str
    path: str
    soft: bool


@dataclass
class Tree:
    root: PedigreeHorse
    generations: int
    boxes: list = field(default_factory=list)
    gaps: list = field(default_factory=list)  # one list of Line for each gap
    has_more: bool = False                    # a further generation is on record
    twins: dict = field(default_factory=dict)  # record pk -> '2×3'

    @property
    def rows(self):
        return 2 ** self.generations

    def box(self, code):
        return next((b for b in self.boxes if b.code == code), None)

    def placed(self):
        """Boxes with their grid place, in family order (for screen readers)."""
        out = []
        for box in sorted(self.boxes, key=lambda b: _family_order(b.code)):
            span = self.rows // (2 ** box.generation)
            out.append({'box': box, 'row': index_of(box.code) * span + 1, 'span': span})
        return out

    @property
    def missing(self):
        return [b for b in self.boxes if b.state == MISSING]

    @property
    def inbreeding(self):
        """[(record, '2×3')] for each record that appears more than once."""
        seen = {}
        for b in self.boxes:
            if b.state == KNOWN and b.record.pk in self.twins and b.record.pk not in seen:
                seen[b.record.pk] = (b.record, self.twins[b.record.pk])
        return list(seen.values())

    @property
    def sire_box(self):
        return self.box('S')

    @property
    def dam_box(self):
        return self.box('D')

    @property
    def dam_sire_box(self):
        return self.box('DS')

    @property
    def gap_list(self):
        """[(column, lines)] for the template: gap g is grid column 2g + 2."""
        return [(2 * g + 2, g, lines) for g, lines in enumerate(self.gaps)]


def _family_order(code: str):
    """Sire line before dam line, each box before its parents."""
    return code.replace('S', '1').replace('D', '2').ljust(MAX_GENERATIONS + 1, '0')


def build(root: PedigreeHorse, generations: int = 2) -> Tree:
    """The tree of ``root``: one query for each generation."""
    generations = max(1, min(int(generations), MAX_GENERATIONS))
    tree = Tree(root=root, generations=generations)
    level = {'': root}
    for k in range(1, generations + 1):
        ids = {pid for rec in level.values() for pid in (rec.sire_id, rec.dam_id) if pid}
        fetched = {r.pk: r for r in PedigreeHorse.objects.filter(pk__in=ids).select_related('horse')}
        next_level = {}
        for code, rec in level.items():
            for letter, parent_id, unknown in (('S', rec.sire_id, rec.sire_unknown),
                                               ('D', rec.dam_id, rec.dam_unknown)):
                child = code + letter
                if parent_id and parent_id in fetched:
                    tree.boxes.append(Box(child, KNOWN, fetched[parent_id]))
                    next_level[child] = fetched[parent_id]
                elif unknown:
                    tree.boxes.append(Box(child, UNKNOWN))
                else:
                    tree.boxes.append(Box(child, MISSING))
                # The parents of an Unknown or not-entered box are not drawn.
        level = next_level
    tree.has_more = generations < MAX_GENERATIONS and any(
        rec.sire_id or rec.dam_id for rec in level.values())

    counts = Counter(b.record.pk for b in tree.boxes if b.state == KNOWN)
    for pk, n in counts.items():
        if n > 1:
            gens = sorted(b.generation for b in tree.boxes if b.state == KNOWN and b.record.pk == pk)
            tree.twins[pk] = '×'.join(str(g) for g in gens)
    for b in tree.boxes:
        if b.state == KNOWN:
            b.twin = tree.twins.get(b.record.pk, '')

    tree.gaps = [[] for _ in range(generations)]
    for b in tree.boxes:
        tree.gaps[b.generation - 1].append(
            Line(b.code, curve(b.code[:-1], b.code), soft=b.state != KNOWN))
    return tree
