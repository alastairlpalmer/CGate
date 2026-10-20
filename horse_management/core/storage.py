"""The media storage used when uploads are in an S3-compatible bucket."""

from storages.backends.s3 import S3Storage

from .uploads import content_disposition


class MediaStorage(S3Storage):
    """S3Storage that marks each object as open-in-browser or download.

    PDFs and photos open in a new tab, as staff expect. Anything else
    (Word files, HEIC photos) is stored with ``Content-Disposition:
    attachment``, so the bucket's signed link downloads it and the browser
    never renders it as a page.
    """

    def get_object_parameters(self, name):
        params = super().get_object_parameters(name)
        params.setdefault('ContentDisposition', content_disposition(name))
        return params
