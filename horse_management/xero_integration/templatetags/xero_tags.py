from django import template

register = template.Library()


@register.simple_tag
def xero_is_connected():
    """Returns True if Xero is connected and active."""
    try:
        from xero_integration.models import XeroConnection
        conn = XeroConnection.get_connection()
        return conn.is_connected
    except Exception:
        return False
