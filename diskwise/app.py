import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import __version__  # noqa: E402
from .backend.udisks import UDisksClient  # noqa: E402

APP_ID = "io.github.diskwise.Diskwise"
HERE = os.path.dirname(os.path.abspath(__file__))


def _data_dir():
    for candidate in (os.path.join(HERE, "data"), os.path.join(os.path.dirname(HERE), "data")):
        if os.path.isdir(candidate):
            return candidate
    return None


class DiskwiseApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.window = None
        for name, cb, accels in (
            ("refresh", self._on_refresh, ["<primary>r", "F5"]),
            ("about", self._on_about, []),
            ("quit", lambda *a: self.quit(), ["<primary>q"]),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", cb)
            self.add_action(action)
            if accels:
                self.set_accels_for_action(f"app.{name}", accels)

    def do_startup(self):
        Adw.Application.do_startup(self)
        display = Gdk.Display.get_default()
        data = _data_dir()
        if data:
            Gtk.IconTheme.get_for_display(display).add_search_path(os.path.join(data, "icons"))
        css = Gtk.CssProvider()
        css.load_from_path(os.path.join(HERE, "style.css"))
        Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self):
        if self.window:
            self.window.present()
            return
        client = UDisksClient()
        if not client.available():
            win = Adw.ApplicationWindow(application=self, title="Diskwise")
            win.set_default_size(600, 500)
            view = Adw.ToolbarView()
            view.add_top_bar(Adw.HeaderBar())
            view.set_content(Adw.StatusPage(
                icon_name="drive-harddisk-symbolic", title="UDisks Is Not Running",
                description="Diskwise reads drive information through the UDisks service.\n"
                            "Install and start it with:\n\n"
                            "<tt>sudo dnf install udisks2 &amp;&amp; sudo systemctl start udisks2</tt>"))
            win.set_content(view)
            win.present()
            return
        from .window import DiskwiseWindow
        self.window = DiskwiseWindow(self, client)
        self.window.present()

    def _on_refresh(self, *_):
        if self.window:
            self.window.reload()
            self.window.toast("Disk information refreshed")

    def _on_about(self, *_):
        about = Adw.AboutDialog(
            application_name="Diskwise",
            application_icon=APP_ID,
            version=__version__,
            developer_name="Diskwise contributors",
            comments="Know your drives: health, speed, power use and management in one place.",
            license_type=Gtk.License.GPL_3_0,
        )
        about.present(self.window)


def main():
    GLib.set_application_name("Diskwise")
    GLib.set_prgname(APP_ID)
    app = DiskwiseApp()
    return app.run(sys.argv)
