from django.apps import AppConfig


class CvetConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'cvet'
    verbose_name = 'C-vet'

    def ready(self):
        from . import signals  # noqa: F401
