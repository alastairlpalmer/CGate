from django.apps import AppConfig


class PedigreeConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'pedigree'
    verbose_name = 'Pedigree'

    def ready(self):
        from . import signals  # noqa: F401
