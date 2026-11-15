import re

from gi.repository import Adw, Gtk

from ..backend import health
from . import fmt
from .charts import HealthRing, Meter, PartitionBar, Swatch
from .widgets import StatTile, clear, info_row, page_box, section, status_icon, tile_grid


class OverviewPage:
    def __init__(self, ctx):
        self.ctx = ctx
        self.widget, box = page_box()

        # Hero
        hero = Gtk.Box(spacing=20)
        hero.add_css_class("card")
        hero.add_css_class("hero")
        icon = Gtk.Image.new_from_icon_name(ctx.disk.icon_name)
        icon.set_pixel_size(56)
        icon.add_css_class("hero-icon")
        icon.set_valign(Gtk.Align.CENTER)
        hero.append(icon)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True,
                       valign=Gtk.Align.CENTER)
        self.title = Gtk.Label(xalign=0, wrap=True, wrap_mode=2)  # WORD_CHAR
        self.title.add_css_class("hero-title")
        text.append(self.title)
        self.subtitle = Gtk.Label(xalign=0, wrap=True)
        self.subtitle.add_css_class("dim-label")
        text.append(self.subtitle)
        status_row = Gtk.Box(spacing=8, margin_top=6)
        self.status_icon_box = Gtk.Box()
        status_row.append(self.status_icon_box)
        self.status_label = Gtk.Label(xalign=0, wrap=True, hexpand=True)
        self.status_label.add_css_class("status-text")
        status_row.append(self.status_label)
        text.append(status_row)
        hero.append(text)
        self.ring = HealthRing(132)
        self.ring.set_valign(Gtk.Align.CENTER)
        hero.append(self.ring)
        box.append(hero)
        self.hero, self.hero_icon = hero, icon

        # Tiles
        self.t_temp = StatTile("Temperature", "temperature-symbolic")
        self.t_age = StatTile("Powered on", "da-clock-symbolic")
        self.t_data = StatTile("Data written", "da-write-symbolic")
        self.t_power = StatTile("Power draw (est.)", "da-power-symbolic")
        box.append(tile_grid([self.t_temp, self.t_age, self.t_data, self.t_power]))

        # Findings
        self.findings = Adw.PreferencesGroup(title="Findings")
        box.append(self.findings)
        self._finding_rows = []

        # Storage layout
        self.layout_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.append(self.layout_box)

        # Device information
        self.info = Adw.PreferencesGroup(title="Device information")
        box.append(self.info)
        self._info_rows = []

        self.refresh()
        self.tick()

    def set_compact(self, compact):
        """Narrow widths: drop the decorative icon and shrink the ring."""
        self.hero_icon.set_visible(not compact)
        self.hero.set_spacing(12 if compact else 20)
        self.ring.set_size_request(*((96, 96) if compact else (132, 132)))

    def refresh(self):
        d, r, det = self.ctx.disk, self.ctx.report, self.ctx.details
        self.title.set_label(d.title)
        bits = [fmt.size(d.size), d.kind_label]
        if det.get("link"):
            bits.append(re.sub(r" \([^)]*\)", "", det["link"]))
        self.subtitle.set_label(" · ".join(bits))

        clear(self.status_icon_box)
        self.status_icon_box.append(status_icon(r.status))
        self.status_label.set_label(f"{health.STATUS_LABEL[r.status]} — {r.summary}"
                                    if r.status != health.UNKNOWN else r.summary)
        caption = "Life left" if r.life_remaining is not None and r.score == r.life_remaining else "Health"
        self.ring.set_value(r.score, r.status, caption)

        # Age tile
        if r.power_on_hours is not None:
            sub = f"{r.power_on_hours:,.0f} hours"
            if r.power_cycles is not None:
                sub += f" · {r.power_cycles:,} power cycles"
            self.t_age.set(fmt.hours(r.power_on_hours), sub)
        else:
            self.t_age.set("—", "Not reported")

        self._refresh_data_tile()
        self._refresh_findings()
        self._refresh_layout()
        self._refresh_info()

    def _refresh_data_tile(self):
        r = self.ctx.report
        if r.data_written is not None:
            self.t_data.label.set_label("Data written")
            sub = f"Read {fmt.size(r.data_read)}" if r.data_read is not None else ""
            fills = r.data_written / self.ctx.disk.size if self.ctx.disk.size else 0
            if fills >= 1:
                sub += f" · {fills:,.0f}× capacity" if sub else f"{fills:,.0f}× capacity"
            self.t_data.set(fmt.size(r.data_written), sub)
        else:
            rd, wr = self.ctx.io.lifetime_counters()
            self.t_data.label.set_label("Written since boot")
            self.t_data.set(fmt.size(wr), f"Read {fmt.size(rd)} since boot" if rd is not None else "")

    def _refresh_findings(self):
        for row in self._finding_rows:
            self.findings.remove(row)
        self._finding_rows = []
        issues = self.ctx.report.issues
        if not issues:
            if self.ctx.report.status == health.UNKNOWN:
                row = Adw.ActionRow(title="Health data unavailable", subtitle=self.ctx.report.summary)
                row.add_prefix(status_icon("unknown"))
            else:
                row = Adw.ActionRow(title="No problems detected",
                                    subtitle="All SMART indicators are within normal range.")
                row.add_prefix(status_icon("good"))
            self.findings.add(row)
            self._finding_rows.append(row)
            return
        for issue in issues:
            row = Adw.ActionRow(title=issue.title, subtitle=issue.detail, subtitle_lines=0)
            row.add_prefix(status_icon(issue.severity))
            self.findings.add(row)
            self._finding_rows.append(row)

    def _refresh_layout(self):
        clear(self.layout_box)
        d = self.ctx.disk
        parts = d.partitions
        table = (d.partition_table or "").upper()
        self.layout_box.append(section(
            "Storage layout",
            f"{len(parts)} partition{'s' if len(parts) != 1 else ''}"
            + (f" · {table} partition table" if table else "")))
        if not parts:
            lbl = Gtk.Label(label="No partitions are visible on this disk.", xalign=0)
            lbl.add_css_class("dim-label")
            self.layout_box.append(lbl)
            return

        segs, cursor = [], 0
        group = Adw.PreferencesGroup()
        for i, p in enumerate(parts):
            if p.offset > cursor + 16 * 1024 * 1024:
                gap = p.offset - cursor
                segs.append((gap / d.size, None, f"Unallocated · {fmt.size(gap)}"))
            segs.append((p.size / d.size, i, f"{p.display_name} · {fmt.size(p.size)}"))
            cursor = p.offset + p.size

            title = p.display_name if p.display_name != p.name else f"Partition {p.number}"
            fs = (p.fstype or "Unknown").replace("crypto_LUKS", "LUKS")
            mounted = ", ".join(p.mountpoints) if p.mountpoints else "Not mounted"
            row = Adw.ActionRow(title=title, subtitle=f"{p.name} · {fs} · {mounted}")
            row.add_prefix(Swatch(i))
            suffix = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER)
            if p.used is not None and p.size:
                total = p.used + (p.free or 0)
                frac = p.used / total if total else 0
                lbl = Gtk.Label(label=f"{fmt.size(p.used)} of {fmt.size(total)} used", xalign=1)
                lbl.add_css_class("caption")
                lbl.add_css_class("numeric")
                suffix.append(lbl)
                meter = Meter(6)
                meter.set_size_request(150, -1)
                meter.set_value(frac, "critical" if frac > 0.95 else "warning" if frac > 0.85 else "info")
                suffix.append(meter)
            else:
                lbl = Gtk.Label(label=fmt.size(p.size), xalign=1)
                lbl.add_css_class("dim-label")
                lbl.add_css_class("numeric")
                suffix.append(lbl)
            row.add_suffix(suffix)
            group.add(row)
        if d.size - cursor > 16 * 1024 * 1024:
            segs.append(((d.size - cursor) / d.size, None, f"Unallocated · {fmt.size(d.size - cursor)}"))

        bar = PartitionBar()
        bar.set_segments(segs)
        self.layout_box.append(bar)
        self.layout_box.append(group)

    def _refresh_info(self):
        for row in self._info_rows:
            self.info.remove(row)
        d, det = self.ctx.disk, self.ctx.details
        rows = [
            ("Model", d.title),
            ("Serial number", d.serial or "—"),
            ("Firmware", d.firmware or "—"),
            ("Capacity", f"{fmt.size(d.size)} ({d.size:,} bytes)"),
            ("Type", d.kind_label + (f" · {d.rotation_rate:,} rpm" if d.rotation_rate > 0 else "")),
            ("Interface", det.get("link") or d.interface_label),
        ]
        if det.get("link_max") and det.get("link_max") != det.get("link"):
            rows.append(("Maximum link", det["link_max"]))
        if det.get("logical_block"):
            rows.append(("Sector size", f"{det['logical_block']} B logical / {det['physical_block']} B physical"))
        rows.append(("TRIM / discard", "Supported" if det.get("trim") else "Not supported"))
        if det.get("scheduler"):
            rows.append(("I/O scheduler", det["scheduler"]))
        if d.wwn:
            rows.append(("WWN", d.wwn))
        rows.append(("Device", d.device))
        self._info_rows = []
        for title, value in rows:
            row = info_row(title, value, copyable=True)
            self.info.add(row)
            self._info_rows.append(row)

    def tick(self):
        ctx, r = self.ctx, self.ctx.report
        t = ctx.temperature
        if t is not None:
            status = ("critical" if r.temp_critical and t >= r.temp_critical
                      else "warning" if r.temp_warning and t >= r.temp_warning else None)
            sub = f"Warning above {r.temp_warning:.0f} °C" if r.temp_warning else ""
            self.t_temp.set(fmt.temp(t), sub, status)
        else:
            self.t_temp.set("—", "No sensor")
        p = ctx.power
        self.t_power.set(fmt.watts(p.current_w), f"{p.state.label} · {fmt.energy_wh(p.energy_wh)} this session")
