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

        # Pillow refuses to decode an image of more than twice this many
        # pixels anywhere in the app (a decompression bomb), and warns above it.
        from PIL import Image

        from core.uploads import MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

        # Connect the analytics receivers (sign in / sign out). They do
        # nothing unless POSTHOG_API_KEY is set.
        from core import signals  # noqa: F401
