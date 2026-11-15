"""Cairo-drawn charts and gauges."""

import math

from gi.repository import Adw, Gtk, Pango, PangoCairo

from . import palette


def _text_rgb(widget):
    c = widget.get_color()
    return c.red, c.green, c.blue


def _layout(widget, text, size=9, weight=Pango.Weight.NORMAL, features=True):
    layout = widget.create_pango_layout(text)
    desc = Pango.FontDescription()
    desc.set_size(int(size * Pango.SCALE))
    desc.set_weight(weight)
    layout.set_font_description(desc)
    if features:
        attrs = Pango.AttrList()
        attrs.insert(Pango.attr_font_features_new("tnum"))
        layout.set_attributes(attrs)
    return layout


def _rounded_rect(cr, x, y, w, h, r):
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def nice_max(v, ticks=4):
    if v <= 0:
        return 1.0, 0.25
    raw = v / ticks
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        step = m * mag
        if step >= raw:
            break
    return step * math.ceil(v / step), step


class _ThemedArea(Gtk.DrawingArea):
    def __init__(self):
        super().__init__()
        self._sm_handler = Adw.StyleManager.get_default().connect("notify::dark", lambda *a: self.queue_draw())
        self.connect("destroy", self._on_destroy)

    def _on_destroy(self, *_):
        Adw.StyleManager.get_default().disconnect(self._sm_handler)


class LineChart(_ThemedArea):
    """Multi-series line chart with a recessive grid and a hover crosshair.

    series: list of (label, slot, values). All series share one y-axis.
    """

    PAD_L, PAD_R, PAD_T, PAD_B = 58, 14, 12, 24

    def __init__(self, y_format, x_ticks=None, hover_label=None, height=200, min_max=0.0):
        super().__init__()
        self.y_format = y_format
        self.x_ticks = x_ticks or (lambda n: [])
        self.hover_label = hover_label or (lambda i, n: "")
        self.min_max = min_max
        self.series = []
        self.hover_x = None
        self.set_content_height(height)
        self.set_hexpand(True)
        self.set_draw_func(self._draw)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        self.add_controller(motion)

    def set_series(self, series):
        self.series = series
        self.queue_draw()

    def _on_motion(self, _c, x, _y):
        self.hover_x = x
        self.queue_draw()

    def _on_leave(self, *_):
        self.hover_x = None
        self.queue_draw()

    def _draw(self, _area, cr, width, height):
        ink = _text_rgb(self)
        surface = palette.surface()
        x0, x1 = self.PAD_L, width - self.PAD_R
        y0, y1 = self.PAD_T, height - self.PAD_B
        n = max((len(v) for _, _, v in self.series), default=0)
        vmax = max((x for _, _, v in self.series for x in v if x is not None), default=0)
        top, step = nice_max(max(vmax, self.min_max))

        def sx(i):
            return x0 + (x1 - x0) * (i / max(n - 1, 1))

        def sy(v):
            return y1 - (y1 - y0) * (v / top)

        # Grid + y labels
        cr.set_line_width(1)
        ticks = int(round(top / step))
        for t in range(ticks + 1):
            v = t * step
            y = round(sy(v)) + 0.5
            cr.set_source_rgba(*ink, 0.10 if t else 0.22)
            cr.move_to(x0, y)
            cr.line_to(x1, y)
            cr.stroke()
            lay = _layout(self, self.y_format(v), 8)
            tw, th = lay.get_pixel_size()
            cr.set_source_rgba(*ink, 0.6)
            cr.move_to(x0 - 8 - tw, y - th / 2)
            PangoCairo.show_layout(cr, lay)

        # X ticks
        for i, text in self.x_ticks(n):
            lay = _layout(self, text, 8)
            tw, _ = lay.get_pixel_size()
            x = min(max(sx(i) - tw / 2, x0 - 4), x1 - tw + 4)
            cr.set_source_rgba(*ink, 0.6)
            cr.move_to(x, y1 + 6)
            PangoCairo.show_layout(cr, lay)

        if n < 2:
            return

        # Series: 10% wash, 2px line, end dot with surface ring. None = no data yet.
        for _label, slot, values in self.series:
            rgb = palette.series(slot)
            for seg in _segments(values):
                (i0, v0), (il, _vl) = seg[0], seg[-1]
                cr.move_to(sx(i0), sy(v0))
                for i, v in seg[1:]:
                    cr.line_to(sx(i), sy(v))
                path = cr.copy_path()
                cr.line_to(sx(il), y1)
                cr.line_to(sx(i0), y1)
                cr.close_path()
                cr.set_source_rgba(*rgb, 0.10)
                cr.fill()
                cr.append_path(path)
                cr.set_source_rgb(*rgb)
                cr.set_line_width(2)
                cr.set_line_join(1)  # round
                cr.set_line_cap(1)
                cr.stroke()
            if self.hover_x is None and values and values[-1] is not None:
                self._dot(cr, sx(len(values) - 1), sy(values[-1]), rgb, surface)

        # Hover crosshair + tooltip
        if self.hover_x is not None and x0 <= self.hover_x <= x1 + 6:
            i = round((self.hover_x - x0) / (x1 - x0) * (n - 1))
            i = min(max(i, 0), n - 1)
            x = sx(i)
            cr.set_source_rgba(*ink, 0.35)
            cr.set_line_width(1)
            cr.move_to(round(x) + 0.5, y0)
            cr.line_to(round(x) + 0.5, y1)
            cr.stroke()
            lines = []
            for label, slot, values in self.series:
                if i < len(values) and values[i] is not None:
                    self._dot(cr, x, sy(values[i]), palette.series(slot), surface)
                    lines.append((label, slot, self.y_format(values[i], True)))
            if lines:
                self._tooltip(cr, x, y0, x0, x1, self.hover_label(i, n), lines, ink, surface)

    @staticmethod
    def _dot(cr, x, y, rgb, surface):
        cr.arc(x, y, 6, 0, 2 * math.pi)
        cr.set_source_rgb(*surface)
        cr.fill()
        cr.arc(x, y, 4, 0, 2 * math.pi)
        cr.set_source_rgb(*rgb)
        cr.fill()

    def _tooltip(self, cr, x, y, x0, x1, header, lines, ink, surface):
        pad, gap = 10, 4
        head = _layout(self, header, 8)
        rows = []
        for label, slot, value in lines:
            lay_l = _layout(self, label, 9)
            lay_v = _layout(self, value, 9, Pango.Weight.BOLD)
            rows.append((slot, lay_l, lay_v))
        hw, hh = head.get_pixel_size()
        row_h = max((max(l.get_pixel_size()[1], v.get_pixel_size()[1]) for _, l, v in rows), default=0)
        lw = max((l.get_pixel_size()[0] for _, l, _ in rows), default=0)
        vw = max((v.get_pixel_size()[0] for _, _, v in rows), default=0)
        w = max(hw, 14 + lw + 16 + vw) + pad * 2
        h = pad * 2 + hh + gap + len(rows) * (row_h + gap) - gap
        tx = x + 12 if x + 12 + w <= x1 else x - 12 - w
        tx = max(tx, x0)
        _rounded_rect(cr, tx, y, w, h, 8)
        cr.set_source_rgba(*surface, 0.97)
        cr.fill_preserve()
        cr.set_source_rgba(*ink, 0.15)
        cr.set_line_width(1)
        cr.stroke()
        cr.set_source_rgba(*ink, 0.6)
        cr.move_to(tx + pad, y + pad)
        PangoCairo.show_layout(cr, head)
        ry = y + pad + hh + gap
        for slot, lay_l, lay_v in rows:
            cr.set_source_rgb(*palette.series(slot))
            cr.arc(tx + pad + 4, ry + row_h / 2, 4, 0, 2 * math.pi)
            cr.fill()
            cr.set_source_rgba(*ink, 0.8)
            cr.move_to(tx + pad + 14, ry)
            PangoCairo.show_layout(cr, lay_l)
            cr.set_source_rgb(*ink)
            cr.move_to(tx + w - pad - lay_v.get_pixel_size()[0], ry)
            PangoCairo.show_layout(cr, lay_v)
            ry += row_h + gap


def _segments(values):
    """Split a series into runs of consecutive non-None points."""
    out, cur = [], []
    for i, v in enumerate(values):
        if v is None:
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append((i, v))
    if cur:
        out.append(cur)
    return [seg for seg in out if len(seg) > 1]


class HealthRing(_ThemedArea):
    def __init__(self, size=140):
        super().__init__()
        self.score = None
        self.status = "unknown"
        self.caption = "Health"
        self.set_size_request(size, size)
        self.set_draw_func(self._draw)

    def set_value(self, score, status, caption="Health"):
        self.score, self.status, self.caption = score, status, caption
        self.queue_draw()

    def _draw(self, _a, cr, w, h):
        ink = _text_rgb(self)
        rgb = palette.status(self.status)
        cx, cy = w / 2, h / 2
        thick = max(8, w * 0.075)
        r = min(w, h) / 2 - thick / 2 - 1
        cr.set_line_width(thick)
        cr.set_line_cap(1)
        cr.set_source_rgba(*rgb, 0.18)
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.stroke()
        if self.score is not None and self.score > 0:
            start = -math.pi / 2
            cr.set_source_rgb(*rgb)
            cr.arc(cx, cy, r, start, start + 2 * math.pi * self.score / 100)
            cr.stroke()
        text = "—" if self.score is None else f"{self.score}%"
        big = _layout(self, text, w * 0.15, Pango.Weight.BOLD)
        bw, bh = big.get_pixel_size()
        small = _layout(self, self.caption, max(8, w * 0.065), features=False)
        sw, sh = small.get_pixel_size()
        top = cy - (bh + sh) / 2
        cr.set_source_rgb(*ink)
        cr.move_to(cx - bw / 2, top)
        PangoCairo.show_layout(cr, big)
        cr.set_source_rgba(*ink, 0.65)
        cr.move_to(cx - sw / 2, top + bh)
        PangoCairo.show_layout(cr, small)


class Meter(_ThemedArea):
    """Thin horizontal meter. The fill carries severity; the track is a lighter step of it."""

    def __init__(self, height=8):
        super().__init__()
        self.fraction = 0.0
        self.status = "info"
        self.set_content_height(height)
        self.set_hexpand(True)
        self.set_valign(Gtk.Align.CENTER)
        self.set_draw_func(self._draw)

    def set_value(self, fraction, status="info"):
        self.fraction = max(0.0, min(1.0, fraction or 0.0))
        self.status = status
        self.queue_draw()

    def _draw(self, _a, cr, w, h):
        rgb = palette.status(self.status)
        _rounded_rect(cr, 0, 0, w, h, h / 2)
        cr.set_source_rgba(*rgb, 0.2)
        cr.fill()
        if self.fraction > 0:
            _rounded_rect(cr, 0, 0, max(h, w * self.fraction), h, h / 2)
            cr.set_source_rgb(*rgb)
            cr.fill()


class PartitionBar(_ThemedArea):
    """Partition layout across the disk; segments separated by a 2px surface gap."""

    def __init__(self):
        super().__init__()
        self.segments = []  # (fraction, slot or None for unallocated, tooltip)
        self.set_content_height(28)
        self.set_hexpand(True)
        self.set_has_tooltip(True)
        self.connect("query-tooltip", self._on_tooltip)
        self.set_draw_func(self._draw)

    def set_segments(self, segments):
        self.segments = segments
        self.queue_draw()

    def _geometry(self, w):
        x, out = 0.0, []
        for frac, slot, tip in self.segments:
            sw = w * frac
            out.append((x, sw, slot, tip))
            x += sw
        return out

    def _draw(self, _a, cr, w, h):
        ink = _text_rgb(self)
        _rounded_rect(cr, 0, 0, w, h, 6)
        cr.clip()
        for x, sw, slot, _ in self._geometry(w):
            if sw < 0.5:
                continue
            if slot is None:
                cr.set_source_rgba(*ink, 0.08)
            else:
                cr.set_source_rgb(*palette.series(slot))
            cr.rectangle(x + 1, 0, max(sw - 2, 1), h)
            cr.fill()

    def _on_tooltip(self, _w, x, _y, _kb, tooltip):
        for sx, sw, _slot, tip in self._geometry(self.get_width()):
            if sx <= x < sx + sw:
                tooltip.set_text(tip)
                return True
        return False


class Swatch(_ThemedArea):
    def __init__(self, slot, shape="dot"):
        super().__init__()
        self.slot = slot
        self.shape = shape
        self.set_content_width(14 if shape == "line" else 10)
        self.set_content_height(10)
        self.set_valign(Gtk.Align.CENTER)
        self.set_draw_func(self._draw)

    def _draw(self, _a, cr, w, h):
        cr.set_source_rgb(*palette.series(self.slot))
        if self.shape == "line":
            cr.set_line_width(2)
            cr.set_line_cap(1)
            cr.move_to(1, h / 2)
            cr.line_to(w - 1, h / 2)
            cr.stroke()
        else:
            cr.arc(w / 2, h / 2, min(w, h) / 2, 0, 2 * math.pi)
            cr.fill()


class StatusDot(_ThemedArea):
    def __init__(self, status="unknown", size=10):
        super().__init__()
        self.status = status
        self.set_content_width(size)
        self.set_content_height(size)
        self.set_valign(Gtk.Align.CENTER)
        self.set_draw_func(self._draw)

    def set_status(self, status):
        self.status = status
        self.queue_draw()

    def _draw(self, _a, cr, w, h):
        cr.set_source_rgb(*palette.status(self.status))
        cr.arc(w / 2, h / 2, min(w, h) / 2, 0, 2 * math.pi)
        cr.fill()
