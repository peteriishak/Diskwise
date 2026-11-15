"""Small reusable widgets."""

from gi.repository import Adw, Gtk

from .charts import Swatch

STATUS_ICON = {
    "good": "da-check-symbolic",
    "warning": "dialog-warning-symbolic",
    "critical": "dialog-error-symbolic",
    "info": "dialog-information-symbolic",
    "unknown": "dialog-question-symbolic",
}


def status_icon(status):
    img = Gtk.Image.new_from_icon_name(STATUS_ICON.get(status, STATUS_ICON["unknown"]))
    img.add_css_class(f"status-{status}")
    return img


class StatTile(Gtk.Box):
    """label · value · sub-line, inside a card."""

    def __init__(self, label, icon=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.add_css_class("card")
        self.add_css_class("stat-tile")
        head = Gtk.Box(spacing=6)
        if icon:
            img = Gtk.Image.new_from_icon_name(icon)
            img.add_css_class("dim-label")
            head.append(img)
        self.label = Gtk.Label(label=label, xalign=0, ellipsize=3)
        self.label.add_css_class("caption-heading")
        self.label.add_css_class("dim-label")
        head.append(self.label)
        self.append(head)
        self.value = Gtk.Label(label="—", xalign=0)
        self.value.add_css_class("stat-value")
        self.value.add_css_class("numeric")
        self.value.set_ellipsize(3)
        self.append(self.value)
        self.sub = Gtk.Label(label="", xalign=0, wrap=True, max_width_chars=16)
        self.sub.add_css_class("caption")
        self.sub.add_css_class("dim-label")
        self.append(self.sub)
        self._status = None

    def set(self, value, sub=None, status=None):
        self.value.set_label(value)
        if sub is not None:
            self.sub.set_label(sub)
            self.sub.set_visible(bool(sub))
        if self._status:
            self.value.remove_css_class(f"status-{self._status}")
        self._status = status if status in ("warning", "critical") else None
        if self._status:
            self.value.add_css_class(f"status-{self._status}")


def tile_grid(tiles, min_per_line=2, max_per_line=4):
    box = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                      min_children_per_line=min_per_line, max_children_per_line=max_per_line,
                      column_spacing=12, row_spacing=12)
    box.add_css_class("tile-grid")
    for t in tiles:
        box.append(t)
        child = t.get_parent()
        child.set_focusable(False)
    return box


def section(title, description=None, suffix=None):
    """A heading for a free-form section (outside PreferencesGroups)."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    row = Gtk.Box(spacing=12)
    lbl = Gtk.Label(label=title, xalign=0, hexpand=True)
    lbl.add_css_class("heading")
    row.append(lbl)
    if suffix:
        row.append(suffix)
    box.append(row)
    if description:
        d = Gtk.Label(label=description, xalign=0, wrap=True)
        d.add_css_class("dim-label")
        d.add_css_class("caption")
        box.append(d)
    return box


def legend(items):
    """items: [(label, slot)]."""
    box = Gtk.Box(spacing=16)
    for label, slot in items:
        item = Gtk.Box(spacing=6)
        item.append(Swatch(slot, "line"))
        lbl = Gtk.Label(label=label)
        lbl.add_css_class("caption")
        item.append(lbl)
        box.append(item)
    return box


def info_row(title, value, subtitle=None, copyable=False):
    row = Adw.ActionRow(title=title)
    if subtitle:
        row.set_subtitle(subtitle)
    lbl = Gtk.Label(label=value, selectable=copyable, xalign=1)
    if len(value) > 32:
        lbl.set_wrap(True)
        lbl.set_max_width_chars(40)
    lbl.add_css_class("dim-label")
    lbl.add_css_class("numeric")
    row.add_suffix(lbl)
    row.value_label = lbl
    return row


def page_box():
    """Scrolled, clamped vertical page container. Returns (scroller, content box)."""
    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
    content.set_margin_top(24)
    content.set_margin_bottom(32)
    content.set_margin_start(16)
    content.set_margin_end(16)
    clamp = Adw.Clamp(maximum_size=1040, tightening_threshold=700, child=content)
    scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=clamp, vexpand=True)
    return scroller, content


def clear(box):
    child = box.get_first_child()
    while child:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt
