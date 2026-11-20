"""What the pedigree card and its diagram need (plan §8.3)."""

from .models import PedigreeHorse
from .services import family, pedigree_for
from .tree import build


def record_for_page(horse) -> PedigreeHorse:
    """The horse's own record, with its parents and their horses loaded."""
    record = (PedigreeHorse.objects.select_related('sire__horse', 'dam__horse', 'horse')
              .filter(horse=horse).first())
    return record or pedigree_for(horse)


def diagram_context(root: PedigreeHorse, *, page_horse, generations=2, back=None) -> dict:
    tree = build(root, generations)
    return {
        'tree': tree,
        'root': root,
        'page_horse': page_horse,
        # "+ Add" boxes open the page horse's pedigree form, so they show
        # only on the page horse's own tree, not on an ancestor's.
        'is_page_horse': root.horse_id == page_horse.pk,
        'back': back,
        'family': family(root),
        'generations': tree.generations,
    }
