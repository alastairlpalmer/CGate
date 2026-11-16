from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import PrototypeEntry


@receiver(post_delete, sender=PrototypeEntry)
def delete_files(sender, instance, **kwargs):
    """Remove the photo and the recording with the row.

    The admin's bulk delete does not call ``Model.delete``, but it does
    send this signal, so "delete selected" clears the files too.
    """
    for field in (instance.photo, instance.audio):
        if field:
            field.delete(save=False)
