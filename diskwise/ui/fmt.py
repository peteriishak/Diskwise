"""Human-friendly formatting helpers."""


def size(n, digits=1):
    """Decimal units, matching how drive capacities are labelled."""
    if n is None:
        return "—"
    n = float(n)
    for unit in ("B", "kB", "MB", "GB", "TB", "PB"):
        if abs(n) < 1000 or unit == "PB":
            if unit == "B":
                return f"{n:.0f} B"
            return f"{n:.{digits}f} {unit}" if abs(n) < 100 else f"{n:.0f} {unit}"
        n /= 1000


def rate(bps):
    if bps is None:
        return "—"
    if bps < 1000:
        return f"{bps:.0f} B/s"
    return size(bps) + "/s"


def compact(n):
    if n is None:
        return "—"
    n = float(n)
    if n >= 1_000_000:
        return f"{n / 1e6:.1f}M"
    if n >= 10_000:
        return f"{n / 1e3:.0f}K"
    if n >= 1000:
        return f"{n / 1e3:.1f}K"
    if n >= 10:
        return f"{n:.0f}"
    return f"{n:.1f}"


def hours(h):
    if h is None:
        return "—"
    if h < 48:
        return f"{h:,.0f} h"
    days = h / 24
    if days < 730:
        return f"{days:,.0f} days"
    return f"{days / 365.25:.1f} years"


def temp(c):
    return "—" if c is None else f"{c:.0f} °C"


def watts(w):
    if w is None:
        return "—"
    if w < 1:
        return f"{w * 1000:.0f} mW"
    return f"{w:.2f} W" if w < 10 else f"{w:.1f} W"


def energy_wh(wh):
    if wh is None:
        return "—"
    if wh < 1:
        return f"{wh * 1000:.1f} mWh"
    if wh < 1000:
        return f"{wh:.2f} Wh"
    return f"{wh / 1000:.1f} kWh"


def ms(v):
    if v is None:
        return "—"
    if v < 1:
        return f"{v * 1000:.0f} µs"
    return f"{v:.1f} ms" if v < 100 else f"{v:.0f} ms"


def percent(v):
    return "—" if v is None else f"{v:.0f}%"
