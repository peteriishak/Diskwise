import threading

from gi.repository import Adw, GLib, Gtk

from ..backend import power
from . import fmt
from .charts import LineChart
from .performance import time_hover, time_ticks
from .widgets import StatTile, clear, info_row, page_box, section, tile_grid


def watt_axis(v, precise=False):
    if precise:
        return fmt.watts(v)
    return f"{v:g} W" if v >= 1 or v == 0 else f"{v * 1000:.0f} mW"


class PowerPage:
    def __init__(self, ctx, toast):
        self.ctx = ctx
        self.toast = toast
        self.widget, box = page_box()

        self.t_state = StatTile("Power state")
        self.t_now = StatTile("Power draw now (est.)")
        self.t_session = StatTile("Energy this session")
        self.t_life = StatTile("Lifetime energy (est.)")
        box.append(tile_grid([self.t_state, self.t_now, self.t_session, self.t_life]))

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card.add_css_class("card")
        card.add_css_class("chart-card")
        card.append(section("Estimated power draw", "Last 2 minutes, from activity and power state"))
        self.chart = LineChart(watt_axis, time_ticks, time_hover, height=180,
                               min_max=max(0.5, ctx.power.profile.idle_w * 2))
        card.append(self.chart)
        box.append(card)

        # Profile
        self.profile_group = Adw.PreferencesGroup(title="Power profile")
        self._profile_rows = []
        if ctx.disk.kind == "nvme":
            self.ps_button = Gtk.Button(label="Read from Drive", valign=Gtk.Align.CENTER)
            self.ps_button.add_css_class("pill-small")
            self.ps_button.set_tooltip_text("Read the drive's power state table (requires administrator)")
            self.ps_button.connect("clicked", self._on_read_states)
            self.profile_group.set_header_suffix(self.ps_button)
        box.append(self.profile_group)

        self.states_group = Adw.PreferencesGroup(
            title="NVMe power states",
            description="States the drive supports. Non-operational states save the most power "
                        "but take longer to wake from.")
        self.states_group.set_visible(False)
        self._state_rows = []
        box.append(self.states_group)

        # Power management settings
        self.pm_group = Adw.PreferencesGroup(title="Power management")
        self._pm_rows = []
        box.append(self.pm_group)

        # System context
        self.sys_group = Adw.PreferencesGroup(title="System")
        self.sys_row = info_row("Whole system draw", "—")
        self.share_row = info_row("This disk's share", "—")
        self.sys_group.add(self.sys_row)
        self.sys_group.add(self.share_row)
        box.append(self.sys_group)

        note = Gtk.Label(
            label="Drives do not report how much power they draw. These figures are estimates "
                  "that combine the drive's power state and live activity with its power profile.",
            xalign=0, wrap=True)
        note.add_css_class("caption")
        note.add_css_class("dim-label")
        box.append(note)

        self.refresh()
        self.tick()

    def refresh(self):
        p = self.ctx.power.profile
        for row in self._profile_rows:
            self.profile_group.remove(row)
        self._profile_rows = [
            info_row("Active", fmt.watts(p.active_w), "Reading or writing"),
            info_row("Idle", fmt.watts(p.idle_w), "Ready, no transfers"),
            info_row("Standby", fmt.watts(p.standby_w),
                     "Spun down" if self.ctx.disk.kind == "hdd" else "Deepest low-power state"),
        ]
        src = Adw.ActionRow(title="Source", subtitle=p.source)
        src.add_prefix(Gtk.Image.new_from_icon_name(
            "da-check-symbolic" if p.measured else "dialog-information-symbolic"))
        self._profile_rows.append(src)
        for row in self._profile_rows:
            self.profile_group.add(row)

        for row in self._pm_rows:
            self.pm_group.remove(row)
        self._pm_rows = [info_row(t, v, d) for t, v, d in power.power_management(self.ctx.disk)]
        for row in self._pm_rows:
            self.pm_group.add(row)
        self.pm_group.set_visible(bool(self._pm_rows))
        self._refresh_states()

        life = self.ctx.power.lifetime_kwh(self.ctx.report)
        if life is not None:
            self.t_life.set(fmt.energy_wh(life * 1000),
                            f"Over {fmt.hours(self.ctx.report.power_on_hours)} powered on")
        else:
            self.t_life.set("—", "Power-on time not reported")

    def _refresh_states(self):
        pt = self.ctx.power
        for row in self._state_rows:
            self.states_group.remove(row)
        self._state_rows = []
        if not pt.nvme_states:
            self.states_group.set_visible(False)
            return
        self.states_group.set_visible(True)
        for s in pt.nvme_states:
            kind = "Operational" if s.operational else "Non-operational"
            sub = f"{kind} · entry {fmt.ms(s.entry_lat_us / 1000)} · exit {fmt.ms(s.exit_lat_us / 1000)}"
            if not s.entry_lat_us and not s.exit_lat_us:
                sub = kind
            row = info_row(f"PS{s.index}", f"up to {fmt.watts(s.max_w)}", sub)
            if pt.nvme_current_ps == s.index:
                tag = Gtk.Label(label="Current", valign=Gtk.Align.CENTER)
                tag.add_css_class("tag")
                row.add_suffix(tag)
            self.states_group.add(row)
            self._state_rows.append(row)

    def _on_read_states(self, btn):
        btn.set_sensitive(False)
        device = self.ctx.disk.device

        def work():
            try:
                states, current = power.read_nvme_power_states(device)
                GLib.idle_add(done, states, current, None)
            except Exception as err:
                GLib.idle_add(done, None, None, str(err))

        def done(states, current, err):
            btn.set_sensitive(True)
            if err:
                self.toast(f"Could not read power states: {err}")
            elif not states:
                self.toast("The drive did not report a power state table")
            else:
                self.ctx.power.set_nvme_states(states, current)
                self.chart.min_max = max(0.5, self.ctx.power.profile.idle_w * 2)
                self.refresh()
            return False

        threading.Thread(target=work, daemon=True).start()

    def tick(self):
        pt = self.ctx.power
        self.t_state.set(pt.state.label, pt.state.detail)
        self.t_now.set(fmt.watts(pt.current_w), f"Peak {fmt.watts(pt.peak_w)} this session")
        self.t_session.set(fmt.energy_wh(pt.energy_wh), "Since the app started")
        self.chart.set_series([("Power", 0, list(pt.history))])
        sys_w = power.battery_draw_w()
        if sys_w:
            self.sys_row.value_label.set_label(fmt.watts(sys_w))
            self.sys_row.set_subtitle("Measured by the battery while discharging")
            self.share_row.value_label.set_label(f"≈ {pt.current_w / sys_w * 100:.1f}%")
            self.share_row.set_visible(True)
        else:
            self.sys_row.value_label.set_label("—")
            self.sys_row.set_subtitle("Only measurable on battery power")
            self.share_row.set_visible(False)
