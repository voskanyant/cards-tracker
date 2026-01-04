from decimal import Decimal, InvalidOperation, ROUND_DOWN

from django import template

register = template.Library()


def _format_with_spaces(value: str) -> str:
    digits = value
    sign = ""
    if digits.startswith("-"):
        sign = "-"
        digits = digits[1:]
    if digits == "":
        digits = "0"
    int_part = digits
    frac_part = ""
    if "." in digits:
        int_part, frac_part = digits.split(".", 1)
    int_part = int_part or "0"

    # Insert spaces every three digits from the right
    groups = []
    while int_part:
        groups.append(int_part[-3:])
        int_part = int_part[:-3]
    spaced = " ".join(reversed(groups)) if groups else "0"

    frac_part = frac_part.rstrip("0")
    if frac_part:
        return f"{sign}{spaced}.{frac_part}"
    return f"{sign}{spaced}"


@register.filter
def spaced_number(value):
    """
    Format Decimal/float/int with spaces between thousands and hide trailing .00.
    """
    if value in (None, ""):
        return ""
    try:
        dec = Decimal(value)
    except (InvalidOperation, ValueError, TypeError):
        try:
            dec = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return value

    quantized = dec.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    raw = format(quantized, "f").rstrip("0").rstrip(".")
    if raw == "":
        raw = "0"
    return _format_with_spaces(raw)


@register.filter
def dict_get(mapping, key):
    if not mapping or key in (None, ""):
        return ""
    return mapping.get(str(key), mapping.get(key, ""))
