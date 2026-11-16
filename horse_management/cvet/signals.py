from django.conf import settings
from django.contrib.auth.signals import user_logged_in
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import ClinicalFile


@receiver(post_delete, sender=ClinicalFile)
def delete_file(sender, instance, **kwargs):
    """Remove the stored photo or recording with its row.

    A delete of the note cascades to its files and sends this signal for
    each of them, so no file is left in storage without a record.
    """
    if instance.file:
        instance.file.delete(save=False)


@receiver(user_logged_in, dispatch_uid='cvet.long_sign_in')
def give_the_phone_a_long_sign_in(sender, request, user, **kwargs):
    """A Vet-role login stays signed in for CVET_SESSION_ABSOLUTE_AGE.

    A vet who is asked for a password at the yard, with no signal, cannot
    sign in at all. ``core.middleware.AbsoluteSessionLifetimeMiddleware``
    reads this from the session; ``cvet.middleware`` sets the idle window
    and takes both away if the login stops being a Vet login.
    """
    from core.middleware import SESSION_ABSOLUTE_AGE_KEY

    from .access import is_vet_only

    if request is None or not hasattr(request, 'session'):
        return
    if is_vet_only(user):
        request.session[SESSION_ABSOLUTE_AGE_KEY] = settings.CVET_SESSION_ABSOLUTE_AGE
