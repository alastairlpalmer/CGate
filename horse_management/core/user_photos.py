"""The signed-in person's profile photo, without a query on every page.

The sidebar and the phone's More sheet draw the avatar on every page. A
lookup there would add a database query to each page, so the photo's file
name is kept in the session instead: written at sign-in and whenever the
profile is saved (core.views.profile), read by a context processor.
A session from before this existed has no entry and shows the letter
until the next sign-in or profile save.
"""

SESSION_KEY = '_profile_photo'
SIZE_KEY = '_display_size'
SIZES = ('compact', 'medium', 'large')
DEFAULT_SIZE = 'medium'
# The size of everything on a phone or a tablet, kept the same way.
PHONE_KEY = '_phone_text_size'
PHONE_SIZES = ('standard', 'large')
DEFAULT_PHONE_SIZE = 'standard'


def remember(request, user):
    """Store the user's photo file name ('' for none) in the session."""
    session = getattr(request, 'session', None)
    if session is None:
        return
    from .models import UserProfile
    row = (
        UserProfile.objects.filter(user=user)
        .values_list('photo', 'display_size', 'phone_text_size')
        .first()
    )
    name, size, phone = row if row else ('', DEFAULT_SIZE, DEFAULT_PHONE_SIZE)
    session[SESSION_KEY] = name or ''
    session[SIZE_KEY] = size if size in SIZES else DEFAULT_SIZE
    session[PHONE_KEY] = phone if phone in PHONE_SIZES else DEFAULT_PHONE_SIZE


def photo_url(request):
    session = getattr(request, 'session', None)
    name = session.get(SESSION_KEY, '') if session is not None else ''
    if not name:
        return ''
    from .models import UserProfile
    return UserProfile._meta.get_field('photo').storage.url(name)


def display_size(request):
    session = getattr(request, 'session', None)
    size = session.get(SIZE_KEY, DEFAULT_SIZE) if session is not None else DEFAULT_SIZE
    return size if size in SIZES else DEFAULT_SIZE


def phone_text_size(request):
    session = getattr(request, 'session', None)
    size = session.get(PHONE_KEY, DEFAULT_PHONE_SIZE) if session is not None else DEFAULT_PHONE_SIZE
    return size if size in PHONE_SIZES else DEFAULT_PHONE_SIZE


def context(request):
    """Context processor: ``my_photo_url`` for the avatar, and for <html>
    ``display_size`` (compact, medium or large, on a computer) and
    ``phone_text_size`` (standard or large, on a phone)."""
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {'display_size': DEFAULT_SIZE, 'phone_text_size': DEFAULT_PHONE_SIZE}
    return {
        'my_photo_url': photo_url(request),
        'display_size': display_size(request),
        'phone_text_size': phone_text_size(request),
    }
