"""Colors for charts and status, with separate steps for light and dark."""

from gi.repository import Adw

# Categorical slots in fixed order (validated for colour-vision deficiency).
CATEGORICAL = {
    "light": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"],
    "dark": ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"],
}

# Reserved for status; never used for series.
STATUS = {
    "light": {"good": "#26a269", "warning": "#c88800", "critical": "#c01c28",
              "info": "#3584e4", "unknown": "#77767b"},
    "dark": {"good": "#33d17a", "warning": "#f5c211", "critical": "#ff7b63",
             "info": "#78aeed", "unknown": "#9a9996"},
}

# Approximate libadwaita card surfaces, used for the 2px separating rings/gaps.
SURFACE = {"light": "#ffffff", "dark": "#2e2e32"}


def mode():
    return "dark" if Adw.StyleManager.get_default().get_dark() else "light"


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def series(slot):
    return hex_rgb(CATEGORICAL[mode()][slot % 8])


def status(name):
    return hex_rgb(STATUS[mode()].get(name, STATUS[mode()]["unknown"]))


def surface():
    return hex_rgb(SURFACE[mode()])
