"""Every horse on the system has its own pedigree record (plan §6.3).

Made when the horse is made, and kept in step (name, suffix, year, sex,
colour) on each save with one UPDATE. A horse that is deleted keeps its
record, without the link: its place in other horses' trees stays.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver

from core.models import Horse

from .models import PedigreeHorse
from .services import sync_from_horse, values_from_horse


@receiver(post_save, sender=Horse, dispatch_uid='pedigree_record_for_horse')
def keep_pedigree_record(sender, instance, created, raw=False, **kwargs):
    if raw:
        return
    if created:
        record = PedigreeHorse(horse=instance, **values_from_horse(instance))
        record.save()
        instance.pedigree = record
        return
    update_fields = kwargs.get('update_fields')
    watched = {'name', 'registered_name', 'country_of_birth', 'date_of_birth', 'sex', 'color'}
    if update_fields is not None and not watched & set(update_fields):
        return
    sync_from_horse(instance)
