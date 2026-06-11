from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'
    verbose_name = 'Core'

    def ready(self):
        # Let Pillow decode HEIC/HEIF — the default photo format on iPhones.
        # Without this, ImageField rejects phone uploads as invalid images.
        # Uploads are converted to JPEG for browser display in
        # core.images.heic_to_jpeg.
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
        except ImportError:
            pass
