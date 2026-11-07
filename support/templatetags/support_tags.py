from django import template

register = template.Library()


@register.filter
def pct(value, digits=0):
    if value is None:
        return "—"
    return f"{value * 100:.{int(digits)}f}%"


@register.filter
def signed_pct(value):
    if value is None:
        return ""
    return f"{value * 100:+.0f}%"


@register.filter
def duration(seconds):
    if seconds is None:
        return "—"
    seconds = int(seconds)
    return f"{seconds // 60}m {seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"
