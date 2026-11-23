"""Template tags for the installed app (docs/MOBILE_APP_PLAN.md, phase 4)."""

from django import template
from django.templatetags.static import static
from django.utils.html import format_html_join

from core import start_images

register = template.Library()


@register.simple_tag
def start_image_links():
    """One <link> for each iPhone and iPad screen size: the picture the
    installed app shows while it starts (core/start_images.py)."""
    return format_html_join(
        '\n    ',
        '<link rel="apple-touch-startup-image" media="{}" href="{}">',
        ((item['media'], static(item['file'])) for item in start_images.links()),
    )
