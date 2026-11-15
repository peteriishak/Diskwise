from gi.repository import Adw, GLib, Gtk

from ..backend import health
from ..backend import udisks as ud
from . import fmt
from .charts import Meter
from .widgets import clear, info_row, page_box, status_icon

NVME_FIELDS = [
    ("avail_spare", "Available spare", lambda v: f"{v}%"),
    ("spare_thresh", "Available spare threshold", lambda v: f"{v}%"),
    ("percent_used", "Percentage used", lambda v: f"{v}%"),
    ("total_data_read", "Data read", fmt.size),
    ("total_data_written", "Data written", fmt.size),
    ("ctrl_busy_time", "Controller busy time", lambda v: f"{v:,} min ({v / 60:,.0f} h)"),
    ("power_cycles", "Power cycles", lambda v: f"{v:,}"),
    ("unsafe_shutdowns", "Unsafe shutdowns", lambda v: f"{v:,}"),
    ("media_errors", "Media and data integrity errors", lambda v: f"{v:,}"),
    ("num_err_log_entries", "Error log entries", lambda v: f"{v:,}"),
    ("wctemp", "Warning temperature threshold", lambda v: f"{v - 273.15:.0f} °C"),
    ("cctemp", "Critical temperature threshold", lambda v: f"{v - 273.15:.0f} °C"),
    ("warning_temp_time", "Time above warning temperature", lambda v: f"{v:,} min"),
    ("critical_temp_time", "Time above critical temperature", lambda v: f"{v:,} min"),
]


class HealthPage:
    def __init__(self, ctx, toast):
        self.ctx = ctx
        self.toast = toast
        self.compact = False
        self._secondary = []
        self.widget, box = page_box()

        # Status banner
        banner = Gtk.Box(spacing=16)
        banner.add_css_class("card")
        banner.add_css_class("hero")
        self.banner_icon = Gtk.Box(valign=Gtk.Align.CENTER)
        banner.append(self.banner_icon)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        self.banner_title = Gtk.Label(xalign=0)
        self.banner_title.add_css_class("title-2")
        col.append(self.banner_title)
        self.banner_text = Gtk.Label(xalign=0, wrap=True)
        col.append(self.banner_text)
        self.banner_updated = Gtk.Label(xalign=0)
        self.banner_updated.add_css_class("caption")
        self.banner_updated.add_css_class("dim-label")
        col.append(self.banner_updated)
        banner.append(col)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", valign=Gtk.Align.CENTER,
                             tooltip_text="Ask the drive for fresh SMART data")
        refresh.add_css_class("circular")
        refresh.connect("clicked", self._on_smart_update)
        banner.append(refresh)
        box.append(banner)

        # Temperature
        self.temp_group = Adw.PreferencesGroup(title="Temperature")
        temp_row = Adw.ActionRow(title="Current temperature")
        self.temp_value = Gtk.Label()
        self.temp_value.add_css_class("numeric")
        self.temp_value.add_css_class("heading")
        self.temp_meter = Meter(8)
        self.temp_meter.set_size_request(160, -1)
        temp_row.add_suffix(self.temp_meter)
        temp_row.add_suffix(self.temp_value)
        self.temp_row = temp_row
        self.temp_group.add(temp_row)
        box.append(self.temp_group)

        # Key indicators
        self.indicators = Adw.PreferencesGroup(title="Key indicators",
                                               description="The values that best predict drive failure")
        self._indicator_rows = []
        box.append(self.indicators)

        # Self-test
        self.selftest = Adw.PreferencesGroup(title="Self-test",
                                             description="The drive checks itself while staying usable")
        buttons = Gtk.Box(spacing=6)
        self.btn_short = Gtk.Button(label="Short Test")
        self.btn_short.connect("clicked", self._on_selftest, "short")
        self.btn_long = Gtk.Button(label="Extended Test")
        self.btn_long.connect("clicked", self._on_selftest, "extended")
        self.btn_abort = Gtk.Button(label="Abort")
        self.btn_abort.add_css_class("destructive-action")
        self.btn_abort.connect("clicked", self._on_abort)
        for b in (self.btn_short, self.btn_long, self.btn_abort):
            b.add_css_class("pill-small")
            buttons.append(b)
        self.selftest.set_header_suffix(buttons)
        self.st_row = Adw.ActionRow(title="Last result")
        self.st_value = Gtk.Label()
        self.st_value.add_css_class("dim-label")
        self.st_row.add_suffix(self.st_value)
        self.selftest.add(self.st_row)
        self.st_progress_row = Adw.ActionRow(title="Progress")
        self.st_progress = Gtk.ProgressBar(valign=Gtk.Align.CENTER, hexpand=True)
        self.st_progress.set_size_request(200, -1)
        self.st_progress_row.add_suffix(self.st_progress)
        self.selftest.add(self.st_progress_row)
        box.append(self.selftest)

        # Raw data
        self.raw_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.append(self.raw_box)

        self.refresh()

    # ------------------------------------------------------------ refresh
    def refresh(self):
        ctx, r, d = self.ctx, self.ctx.report, self.ctx.disk
        clear(self.banner_icon)
        icon = status_icon(r.status)
        icon.set_pixel_size(48)
        self.banner_icon.append(icon)
        self.banner_title.set_label(health.STATUS_LABEL[r.status] if r.status != health.UNKNOWN
                                    else "Health unknown")
        self.banner_text.set_label(r.summary)
        self.banner_updated.set_label(f"SMART data updated {health.ago(r.smart_updated)}"
                                      if r.smart_updated else "")

        for row in self._indicator_rows:
            self.indicators.remove(row)
        self._indicator_rows = []
        for label, value, sev, desc in r.indicators:
            row = info_row(label, value, desc)
            row.add_prefix(status_icon(sev if sev != health.INFO else "info"))
            self.indicators.add(row)
            self._indicator_rows.append(row)
        self.indicators.set_visible(bool(r.indicators))

        has_smart = d.has_ata or d.has_nvme
        self.selftest.set_visible(has_smart and r.status != health.UNKNOWN)
        running = r.selftest_status == "inprogress"
        self.st_value.set_label(health.selftest_label(r.selftest_status, r.selftest_remaining))
        self.st_progress_row.set_visible(running)
        if running and r.selftest_remaining >= 0:
            self.st_progress.set_fraction((100 - r.selftest_remaining) / 100)
        self.btn_short.set_sensitive(not running)
        self.btn_long.set_sensitive(not running)
        self.btn_abort.set_visible(running)

        self._refresh_raw()
        self.tick()

    def _refresh_raw(self):
        clear(self.raw_box)
        d = self.ctx.disk
        if d.has_ata and d.smart_attrs:
            self.raw_box.append(self._ata_table())
        elif d.has_nvme and isinstance(d.smart_attrs, dict):
            group = Adw.PreferencesGroup(title="NVMe health log")
            a = d.smart_attrs
            for key, label, f in NVME_FIELDS:
                if key in a:
                    group.add(info_row(label, f(a[key])))
            sensors = [t for t in a.get("temp_sensors", []) if t]
            for i, t in enumerate(sensors, 1):
                group.add(info_row(f"Temperature sensor {i}", f"{t - 273.15:.0f} °C"))
            self.raw_box.append(group)

    def _ata_table(self):
        group = Adw.PreferencesGroup(title="SMART attributes",
                                     description="Normalized values count down; a value at or below its threshold means failure")
        lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        lb.add_css_class("boxed-list")
        groups = [Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL) for _ in range(5)]
        secondary = []  # Worst / Threshold cells, hidden when narrow
        id_group = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)

        def make_row(cells, header=False, status=None, description=""):
            row = Gtk.Box(spacing=12, margin_start=12, margin_end=12, margin_top=8, margin_bottom=8)
            if header:
                row.add_css_class("attr-header")
                spacer = Gtk.Box()
                spacer.set_size_request(16, -1)
                row.append(spacer)
            else:
                row.append(status_icon(status))
            id_lbl = Gtk.Label(label=cells[0], xalign=1, width_chars=3)
            id_lbl.add_css_class("numeric")
            id_lbl.add_css_class("dim-label")
            id_group.add_widget(id_lbl)
            row.append(id_lbl)
            name_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            name = Gtk.Label(label=cells[1], xalign=0, ellipsize=3)
            name_box.append(name)
            if description:
                name.set_tooltip_text(description)
                desc = Gtk.Label(label=description, xalign=0, ellipsize=3)
                desc.add_css_class("caption")
                desc.add_css_class("dim-label")
                name_box.append(desc)
            row.append(name_box)
            for i, text in enumerate(cells[2:]):
                lbl = Gtk.Label(label=text, xalign=1)
                lbl.add_css_class("numeric")
                if i == 3:
                    lbl.set_xalign(1)
                groups[i].add_widget(lbl)
                if i in (1, 2):
                    secondary.append(lbl)
                row.append(lbl)
            for lbl in (id_lbl, name):
                if header:
                    lbl.add_css_class("caption-heading")
            if header:
                for c in list(_children(row))[3:]:
                    c.add_css_class("caption-heading")
            return row

        lb.append(make_row(["ID", "Attribute", "Value", "Worst", "Threshold", "Raw value"], header=True))
        for a in health.attribute_rows(self.ctx.disk):
            lb.append(make_row([str(a["id"]), a["name"], str(a["value"]), str(a["worst"]),
                                str(a["threshold"]), a["raw"]],
                               status=a["status"], description=a["description"]))
        self._secondary = secondary
        self.set_compact(self.compact)
        group.add(lb)
        return group

    def set_compact(self, compact):
        self.compact = compact
        for lbl in self._secondary:
            lbl.set_visible(not compact)

    def tick(self):
        r = self.ctx.report
        t = self.ctx.temperature
        if t is None:
            self.temp_group.set_visible(False)
            return
        self.temp_group.set_visible(True)
        crit = r.temp_critical or 80
        warn = r.temp_warning or 70
        status = "critical" if t >= crit else "warning" if t >= warn else "good"
        self.temp_value.set_label(fmt.temp(t))
        self.temp_meter.set_value(t / crit, status)
        self.temp_row.set_subtitle(f"Warning at {warn:.0f} °C · critical at {crit:.0f} °C")

    # ------------------------------------------------------------ actions
    def _iface(self):
        return ud.IFACE_ATA if self.ctx.disk.has_ata else ud.IFACE_NVME

    def _on_selftest(self, _btn, kind):
        params = GLib.Variant("(sa{sv})", (kind, {}))

        def done(_res, err):
            if err:
                self.toast(f"Could not start self-test: {self.ctx.client.error_message(err)}")
            else:
                self.toast(f"{kind.capitalize()} self-test started")
                GLib.timeout_add_seconds(2, self._reload)

        self.ctx.client.call_async(self.ctx.disk.drive_path, self._iface(), "SmartSelftestStart",
                                   params, done)

    def _on_abort(self, _btn):
        def done(_res, err):
            if err:
                self.toast(f"Could not abort: {self.ctx.client.error_message(err)}")
            else:
                GLib.timeout_add_seconds(1, self._reload)

        self.ctx.client.call_async(self.ctx.disk.drive_path, self._iface(), "SmartSelftestAbort",
                                   callback=done)

    def _on_smart_update(self, btn):
        btn.set_sensitive(False)

        def done(_res, err):
            btn.set_sensitive(True)
            if err:
                self.toast(f"SMART update failed: {self.ctx.client.error_message(err)}")
            self._reload()

        params = GLib.Variant("(a{sv})", ({"nowakeup": GLib.Variant("b", True)},)) \
            if self.ctx.disk.has_ata else ud.NO_OPTIONS
        self.ctx.client.call_async(self.ctx.disk.drive_path, self._iface(), "SmartUpdate",
                                   params, done)

    def _reload(self):
        self.ctx.refresh_smart()
        for cb in self.ctx.listeners:
            cb()
        return False


def _children(widget):
    c = widget.get_first_child()
    while c:
        yield c
        c = c.get_next_sibling()
