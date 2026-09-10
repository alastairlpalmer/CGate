"""The app bar's page-action slot, across every way a page can change.

The slot sits outside #main-content, so nothing that swaps the main
content touches it. A page fills it by teleporting its own action into
it — the list filter toggle, the phone map's site pill — and base.html
has to empty it again, or the action from the page you left stays.

A boosted navigation always did (Yardway.afterMainSwap). Browser Back
and Forward did not: htmx puts the cached #main-content back itself, so
that handler never runs, the stale button stayed and the restored page
teleported a second one in beside it. Three Back presses left three
filter buttons in the bar, and the click went to the stale one — bound
to a scope that no longer exists, so the filter panel stopped opening.
"""

from pathlib import Path

from django.test import SimpleTestCase

BASE = (
    Path(__file__).resolve().parents[2] / 'templates' / 'base.html'
).read_text()


class AppBarSlotTests(SimpleTestCase):
    def test_a_boosted_swap_empties_the_slot(self):
        """The behaviour that already worked, so it is not lost."""
        after_swap = BASE.split('window.Yardway.afterMainSwap = function')[1]
        head = after_swap[:after_swap.index('if (responseText)')]
        self.assertIn("getElementById('app-bar-slot')", head)
        self.assertIn("innerHTML = ''", head)

    def test_browser_back_empties_the_slot_too(self):
        """htmx fires historyRestore where a boosted swap fires afterSwap."""
        self.assertIn("htmx:historyRestore", BASE)
        restore = BASE.split("addEventListener('htmx:historyRestore'")[1][:400]
        self.assertIn("app-bar-slot", restore)
        self.assertIn("innerHTML = ''", restore)


class TeleportedActionsTests(SimpleTestCase):
    """Everything that puts something in the slot, so the list stays known.

    Each of these is a page action that outlives its page unless the slot
    is emptied, which is what the two tests above are for.
    """

    def sources(self):
        root = Path(__file__).resolve().parents[2] / 'templates'
        return [
            path for path in root.rglob('*.html')
            if 'x-teleport="#app-bar-slot"' in path.read_text()
        ]

    def test_the_slot_is_filled_only_by_a_teleport(self):
        names = sorted(path.name for path in self.sources())
        self.assertEqual(names, ['_filter_toggle.html', '_phone_map.html'])
