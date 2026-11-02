"""An "Undo" button on a success message.

A view that soft-deletes something adds ``extra_tags=undo_tag(url)`` to
its message. base.html shows an Undo button that POSTs to ``url``
(``core.templatetags.ui_extras.undo_url`` reads it back).
"""

UNDO_PREFIX = 'undo:'


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
