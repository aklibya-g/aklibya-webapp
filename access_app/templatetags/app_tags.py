import builtins

from django import template

register = template.Library()


@register.filter(name="getattr")
def getattr_filter(obj, attr):
    return builtins.getattr(obj, attr, None)


@register.filter(name="fmt")
def fmt_amount(value):
    try:
        return f"{float(value):,.3f}"
    except (ValueError, TypeError):
        return value or "-"


@register.filter(name="split")
def split_filter(value, sep=","):
    if not value:
        return []
    return str(value).split(sep)
