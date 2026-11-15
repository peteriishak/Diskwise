from gi.repository import Adw, GLib, Gtk

from .backend import devices, health
from .context import DiskContext
from .ui import fmt
from .ui.charts import StatusDot
from .ui.health_page import HealthPage
from .ui.manage_page import ManagePage
from .ui.overview import OverviewPage
from .ui.performance import PerformancePage
from .ui.power_page import PowerPage

SMART_REFRESH_S = 60


class DiskView:
    """The four pages for one disk."""

    def __init__(self, ctx, all_disks, toast, request_reload):
        self.ctx = ctx
        self.stack = Adw.ViewStack()
        self.overview = OverviewPage(ctx)
        self.health = HealthPage(ctx, toast)
        self.performance = PerformancePage(ctx, all_disks, toast)
        self.power = PowerPage(ctx, toast)
        self.manage = ManagePage(ctx, toast, request_reload)
        self.stack.add_titled_with_icon(self.overview.widget, "overview", "Overview", "view-grid-symbolic")
        self.stack.add_titled_with_icon(self.health.widget, "health", "Health", "da-health-symbolic")
        self.stack.add_titled_with_icon(self.performance.widget, "performance", "Performance",
                                        "da-speed-symbolic")
        self.stack.add_titled_with_icon(self.power.widget, "power", "Power", "da-power-symbolic")
        self.stack.add_titled_with_icon(self.manage.widget, "manage", "Manage", "da-manage-symbolic")
        page = self.stack.get_page(self.health.widget)
        page.set_needs_attention(ctx.report.status in (health.WARNING, health.CRITICAL))

    def pages(self):
        return (self.overview, self.health, self.performance, self.power, self.manage)

    def set_compact(self, compact):
        for p in self.pages():
            if hasattr(p, "set_compact"):
                p.set_compact(compact)

    def refresh(self):
        for p in self.pages():
            p.refresh() if hasattr(p, "refresh") else None
        page = self.stack.get_page(self.health.widget)
        page.set_needs_attention(self.ctx.report.status in (health.WARNING, health.CRITICAL))

    def tick(self):
        visible = self.stack.get_visible_child()
        for p in self.pages():
            if p.widget is visible:
                p.tick()


class SidebarRow(Gtk.ListBoxRow):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        box = Gtk.Box(spacing=12, margin_top=10, margin_bottom=10, margin_start=6, margin_end=6)
        self.icon = Gtk.Image.new_from_icon_name(ctx.disk.icon_name)
        self.icon.set_pixel_size(24)
        box.append(self.icon)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        self.title = Gtk.Label(xalign=0, ellipsize=3)
        col.append(self.title)
        self.subtitle = Gtk.Label(xalign=0, ellipsize=3)
        self.subtitle.add_css_class("caption")
        self.subtitle.add_css_class("dim-label")
        col.append(self.subtitle)
        box.append(col)
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
        self.dot = StatusDot()
        self.dot.set_halign(Gtk.Align.END)
        right.append(self.dot)
        self.temp = Gtk.Label(xalign=1)
        self.temp.add_css_class("caption")
        self.temp.add_css_class("numeric")
        self.temp.add_css_class("dim-label")
        right.append(self.temp)
        box.append(right)
        self.set_child(box)
        self.update()

    def update(self):
        d, r = self.ctx.disk, self.ctx.report
        self.title.set_label(d.title)
        self.subtitle.set_label(f"{fmt.size(d.size)} · {d.kind_label}")
        self.dot.set_status(r.status)
        self.dot.set_tooltip_text(health.STATUS_LABEL[r.status])
        self.temp.set_label(fmt.temp(self.ctx.temperature) if self.ctx.temperature is not None else "")


class DiskwiseWindow(Adw.ApplicationWindow):
    def __init__(self, app, client):
        super().__init__(application=app, title="Diskwise")
        self.set_default_size(1200, 820)
        self.set_size_request(360, 400)
        self.client = client
        self.contexts = {}   # drive_path -> DiskContext
        self.views = {}      # drive_path -> DiskView
        self.rows = {}
        self.current = None
        self.tick_count = 0

        self.toasts = Adw.ToastOverlay()
        self.split = Adw.NavigationSplitView(min_sidebar_width=250, max_sidebar_width=320,
                                             sidebar_width_fraction=0.25)
        self.toasts.set_child(self.split)
        self.set_content(self.toasts)

        # Sidebar
        sb_view = Adw.ToolbarView()
        sb_header = Adw.HeaderBar()
        sb_header.set_title_widget(Adw.WindowTitle(title="Diskwise"))
        menu = Gtk.MenuButton(icon_name="open-menu-symbolic", tooltip_text="Main Menu")
        model = Gtk.Builder.new_from_string("""
            <interface><menu id="m"><section>
              <item><attribute name="label">Refresh</attribute><attribute name="action">app.refresh</attribute></item>
              <item><attribute name="label">About Diskwise</attribute><attribute name="action">app.about</attribute></item>
            </section></menu></interface>""", -1).get_object("m")
        menu.set_menu_model(model)
        menu.set_primary(True)
        sb_header.pack_end(menu)
        sb_view.add_top_bar(sb_header)
        self.listbox = Gtk.ListBox()
        self.listbox.add_css_class("navigation-sidebar")
        self.listbox.connect("row-activated", self._on_row_activated)
        sb_view.set_content(Gtk.ScrolledWindow(child=self.listbox, vexpand=True,
                                               hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.sidebar_summary = Gtk.Label(xalign=0, margin_start=18, margin_end=18,
                                         margin_top=8, margin_bottom=10, wrap=True)
        self.sidebar_summary.add_css_class("caption")
        self.sidebar_summary.add_css_class("dim-label")
        sb_view.add_bottom_bar(self.sidebar_summary)
        self.split.set_sidebar(Adw.NavigationPage(title="Disks", child=sb_view))

        # Content
        self.content_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.switcher = Adw.ViewSwitcher(policy=Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(self.switcher)
        self.content_view.add_top_bar(header)
        self.switcher_bar = Adw.ViewSwitcherBar()
        self.content_view.add_bottom_bar(self.switcher_bar)
        self.content_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.empty = Adw.StatusPage(icon_name="drive-harddisk-symbolic", title="No Disks Found",
                                    description="No drives were reported by UDisks.")
        self.content_stack.add_named(self.empty, "empty")
        self.content_view.set_content(self.content_stack)
        self.content_page = Adw.NavigationPage(title="Disk", child=self.content_view)
        self.split.set_content(self.content_page)

        # Adaptive layout
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 940sp"))
        bp.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(bp)
        bp2 = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 640sp"))
        bp2.add_setter(self.split, "collapsed", True)
        bp2.add_setter(self.switcher_bar, "reveal", True)
        bp2.add_setter(self.switcher, "visible", False)
        bp2.connect("apply", lambda *a: self._set_compact(True))
        bp2.connect("unapply", lambda *a: self._set_compact(False))
        self.add_breakpoint(bp2)
        self.compact = False

        self.reload()
        client.watch_objects(self._on_hotplug)
        GLib.timeout_add_seconds(1, self._on_tick)
        GLib.timeout_add_seconds(SMART_REFRESH_S, self._on_smart_timer)

    # --------------------------------------------------------------- data
    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=5))

    def reload(self):
        try:
            disks = devices.discover(self.client)
        except GLib.Error as err:
            self.toast(f"Could not list disks: {self.client.error_message(err)}")
            return
        seen = set()
        for d in disks:
            seen.add(d.drive_path)
            ctx = self.contexts.get(d.drive_path)
            if ctx:
                ctx.replace_disk(d)
            else:
                ctx = DiskContext(self.client, d)
                ctx.listeners.append(lambda c=ctx: self._on_ctx_changed(c))
                self.contexts[d.drive_path] = ctx
            ctx.refresh_smart()
        for key in list(self.contexts):
            if key not in seen:
                self._remove(key)
        self.all_disks = disks

        self.listbox.remove_all()
        self.rows = {}
        for d in disks:
            row = SidebarRow(self.contexts[d.drive_path])
            self.rows[d.drive_path] = row
            self.listbox.append(row)
        for key, view in self.views.items():
            view.refresh()
        self._update_summary()

        if not disks:
            self.content_stack.set_visible_child_name("empty")
            self.current = None
            return
        if self.current not in self.contexts:
            self.current = disks[0].drive_path
        self._show(self.current)
        self.listbox.select_row(self.rows[self.current])

    def _remove(self, key):
        ctx = self.contexts.pop(key)
        if ctx.runner:
            ctx.runner.cancel()
        view = self.views.pop(key, None)
        if view:
            self.content_stack.remove(view.stack)
        if self.current == key:
            self.current = None

    def _update_summary(self):
        counts = {}
        for ctx in self.contexts.values():
            counts[ctx.report.status] = counts.get(ctx.report.status, 0) + 1
        n = len(self.contexts)
        bad = counts.get(health.WARNING, 0) + counts.get(health.CRITICAL, 0)
        text = f"{n} disk{'s' if n != 1 else ''}"
        text += f" · {bad} need{'s' if bad == 1 else ''} attention" if bad else " · all healthy" \
            if counts.get(health.GOOD) == n else ""
        self.sidebar_summary.set_label(text)

    def _set_compact(self, compact):
        self.compact = compact
        for view in self.views.values():
            view.set_compact(compact)

    def _show(self, key):
        ctx = self.contexts[key]
        view = self.views.get(key)
        if view is None:
            view = DiskView(ctx, self.all_disks, self.toast, self.reload)
            view.set_compact(self.compact)
            self.views[key] = view
            self.content_stack.add_named(view.stack, key)
        self.current = key
        self.content_stack.set_visible_child(view.stack)
        self.switcher.set_stack(view.stack)
        self.switcher_bar.set_stack(view.stack)
        self.content_page.set_title(ctx.disk.title)

    # ------------------------------------------------------------- events
    def _on_row_activated(self, _lb, row):
        self._show(row.ctx.key)
        self.split.set_show_content(True)

    def _on_ctx_changed(self, ctx):
        view = self.views.get(ctx.key)
        if view:
            view.refresh()
        row = self.rows.get(ctx.key)
        if row:
            row.update()
        self._update_summary()

    def _on_hotplug(self):
        if getattr(self, "_hotplug_pending", False):
            return
        self._hotplug_pending = True

        def run():
            self._hotplug_pending = False
            self.reload()
            return False

        GLib.timeout_add(1500, run)

    def _on_tick(self):
        self.tick_count += 1
        for ctx in self.contexts.values():
            ctx.tick(self.tick_count)
        if self.current in self.views:
            self.views[self.current].tick()
        if self.tick_count % 2 == 0:
            for row in self.rows.values():
                row.update()
        # Poll faster while a self-test is running.
        if self.tick_count % 15 == 0:
            for ctx in self.contexts.values():
                if ctx.report.selftest_status == "inprogress":
                    ctx.refresh_smart()
                    self._on_ctx_changed(ctx)
        return True

    def _on_smart_timer(self):
        for ctx in self.contexts.values():
            ctx.refresh_smart()
            self._on_ctx_changed(ctx)
        return True
