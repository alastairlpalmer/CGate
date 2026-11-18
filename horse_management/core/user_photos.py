"""The signed-in person's profile photo, without a query on every page.

The sidebar and the phone's More sheet draw the avatar on every page. A
lookup there would add a database query to each page, so the photo's file
name is kept in the session instead: written at sign-in and whenever the
profile is saved (core.views.profile), read by a context processor.
A session from before this existed has no entry and shows the letter
until the next sign-in or profile save.
"""

SESSION_KEY = '_profile_photo'


def remember(request, user):
    """Store the user's photo file name ('' for none) in the session."""
    session = getattr(request, 'session', None)
    if session is None:
        return
    from .models import UserProfile
    name = (
        UserProfile.objects.filter(user=user)
        .values_list('photo', flat=True)
        .first()
    )
    session[SESSION_KEY] = name or ''


def photo_url(request):
    session = getattr(request, 'session', None)
    name = session.get(SESSION_KEY, '') if session is not None else ''
    if not name:
        return ''
    from .models import UserProfile
    return UserProfile._meta.get_field('photo').storage.url(name)


def context(request):
    """Context processor: ``my_photo_url`` for the avatar."""
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {}
    return {'my_photo_url': photo_url(request)}
