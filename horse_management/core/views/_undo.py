"""An "Undo" button on a success message.

A view that soft-deletes something adds ``extra_tags=undo_tag(url)`` to
its message. base.html shows an Undo button that POSTs to ``url``
(``core.templatetags.ui_extras.undo_url`` reads it back).

A move (of one horse, or of a group) has no row of its own to name in a
URL, so its Undo link carries a signed list of the stays the move made
(``group_move_undo_url``, read back by ``read_group_move_token``).
"""

from urllib.parse import urlencode

from django.core import signing
from django.urls import reverse

UNDO_PREFIX = 'undo:'

GROUP_MOVE_SALT = 'core.group-move-undo'
# The button is on the message of the move, and that goes with the next
# page. This limit is for a message left open on a phone in a pocket:
# after a working day the yard has moved on, and each stay is edited on
# the horse's page instead.
GROUP_MOVE_UNDO_SECONDS = 12 * 60 * 60
# A link longer than this is not offered: a server can refuse a long
# request line (gunicorn stops at 4094 bytes). About 700 horses.
GROUP_MOVE_UNDO_MAX_URL = 3000


def undo_tag(url):
    return f'{UNDO_PREFIX}{url}'


def undo_url_from_tags(extra_tags):
    """The restore URL in a message's extra tags, or ''."""
    for tag in (extra_tags or '').split():
        if tag.startswith(UNDO_PREFIX):
            url = tag[len(UNDO_PREFIX):]
            # Only ever a path this app built; refuse anything else.
            if url.startswith('/') and not url.startswith('//'):
                return url
    return ''


def group_move_undo_url(user, moved):
    """The Undo link for a move, or '' if none can be offered.

    ``moved``: the stays ``PlacementService.move_group`` returned, or the
    one stay of ``move_horse``. Only a horse that came from another stay
    can be put back; one that had no location before the move is left
    out. All stays of one move have the same location and the same start
    date.

    The move of one horse can give it to another owner. The link then
    names the owner its share had, so that Undo gives the share back.
    """
    back = [placement for placement in moved if getattr(placement, 'previous_placement', None)]
    if not back:
        return ''
    data = {
        'u': user.pk,
        'to': back[0].location_id,
        'd': back[0].start_date.isoformat(),
        'p': [placement.pk for placement in back],
    }
    share_owner = getattr(back[0], 'share_owner_was', None) if len(back) == 1 else None
    if share_owner is not None:
        data['o'] = share_owner.pk
    token = signing.dumps(data, salt=GROUP_MOVE_SALT, compress=True)
    url = reverse('group_move_undo') + '?' + urlencode({'t': token})
    return url if len(url) <= GROUP_MOVE_UNDO_MAX_URL else ''


def read_group_move_token(token, user):
    """What ``group_move_undo_url`` signed, for the same person, or None:
    not ours, changed, too old, or made for somebody else."""
    from datetime import date

    try:
        data = signing.loads(token or '', salt=GROUP_MOVE_SALT, max_age=GROUP_MOVE_UNDO_SECONDS)
        if data['u'] != user.pk:
            return None
        return {
            'destination_id': int(data['to']),
            'move_date': date.fromisoformat(data['d']),
            'placement_ids': [int(pk) for pk in data['p']],
            'share_owner_id': int(data['o']) if data.get('o') else None,
        }
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        return None
