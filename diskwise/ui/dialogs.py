"""Dialogs for disk management: format options, typed confirmation, text and password prompts."""

from gi.repository import Adw, Gtk

from ..backend import manage
from . import fmt


def confirm_destructive(parent, heading, body, token, action_label, on_confirm):
    """Ask the person to type `token` before a destructive action becomes available."""
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.set_body_use_markup(True)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    hint = Gtk.Label(xalign=0, wrap=True)
    hint.set_markup(f"Type <b>{token}</b> to confirm")
    hint.add_css_class("caption")
    box.append(hint)
    entry = Gtk.Entry(placeholder_text=token, activates_default=True)
    box.append(entry)
    dialog.set_extra_child(box)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("go", action_label)
    dialog.set_response_appearance("go", Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_response_enabled("go", False)
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")
    entry.connect("changed", lambda e: dialog.set_response_enabled("go", e.get_text().strip() == token))

    def on_response(_d, response):
        if response == "go" and entry.get_text().strip() == token:
            on_confirm()

    dialog.connect("response", on_response)
    dialog.present(parent)
    entry.grab_focus()


def ask_text(parent, heading, body, initial, action_label, on_ok, max_len=None):
    dialog = Adw.AlertDialog(heading=heading, body=body)
    entry = Gtk.Entry(text=initial or "", activates_default=True)
    if max_len:
        entry.set_max_length(max_len)
    dialog.set_extra_child(entry)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("ok", action_label)
    dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("ok")
    dialog.set_close_response("cancel")
    dialog.connect("response", lambda _d, r: on_ok(entry.get_text().strip()) if r == "ok" else None)
    dialog.present(parent)


def ask_password(parent, heading, body, action_label, on_ok):
    dialog = Adw.AlertDialog(heading=heading, body=body)
    entry = Gtk.PasswordEntry(show_peek_icon=True, activates_default=True)
    dialog.set_extra_child(entry)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("ok", action_label)
    dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("ok")
    dialog.set_close_response("cancel")
    dialog.connect("response", lambda _d, r: on_ok(entry.get_text()) if r == "ok" and entry.get_text() else None)
    dialog.present(parent)
    entry.grab_focus()


class FormatDialog(Adw.Dialog):
    """Options for formatting a disk or partition, or creating a partition.

    mode: "disk", "partition" or "create". on_submit receives a dict with
    scheme, fstype (None = no partition), label, passphrase, erase and size.
    """

    def __init__(self, client, mode, target_title, target_size, on_submit, write_rate=None):
        super().__init__()
        self.client = client
        self.mode = mode
        self.target_size = target_size
        self.on_submit = on_submit
        self.write_rate = write_rate
        self._caps = {}
        titles = {"disk": "Format Disk", "partition": "Format Partition", "create": "Create Partition"}
        self.set_title(titles[mode])
        self.set_content_width(480)

        view = Adw.ToolbarView()
        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        header.pack_start(cancel)
        self.go = Gtk.Button(label="Create" if mode == "create" else "Format…")
        self.go.add_css_class("suggested-action" if mode == "create" else "destructive-action")
        self.go.connect("clicked", self._on_go)
        header.pack_end(self.go)
        view.add_top_bar(header)

        page = Adw.PreferencesPage()
        view.set_content(page)
        self.set_child(view)

        target = Adw.PreferencesGroup()
        row = Adw.ActionRow(title=target_title, subtitle=fmt.size(target_size))
        row.add_prefix(Gtk.Image.new_from_icon_name(
            "list-add-symbolic" if mode == "create" else "dialog-warning-symbolic"))
        if mode != "create":
            row.set_subtitle(f"{fmt.size(target_size)} · everything on it will be erased")
        target.add(row)
        page.add(target)

        # Layout (whole disk only)
        if mode == "disk":
            layout = Adw.PreferencesGroup(title="Layout")
            self.scheme_row = Adw.ComboRow(title="Partitioning")
            self.scheme_row.set_model(Gtk.StringList.new(["GPT", "MBR"]))
            self.scheme_row.set_subtitle("Recommended. Works with every computer from the last 15 years")
            self.scheme_row.connect("notify::selected", lambda *_: self._validate())
            layout.add(self.scheme_row)
            self.content_row = Adw.ComboRow(title="Content")
            self.content_row.set_model(Gtk.StringList.new(["One volume", "Empty"]))
            self.content_row.set_subtitle("A single volume using the whole disk, or no volumes yet")
            self.content_row.connect("notify::selected", lambda *_: self._sync())
            layout.add(self.content_row)
            page.add(layout)

        # Volume
        self.volume = Adw.PreferencesGroup(title="Volume")
        self.name_row = Adw.EntryRow(title="Name")
        self.name_row.connect("changed", lambda *_: self._validate())
        self.volume.add(self.name_row)
        self.fs_row = Adw.ComboRow(title="Type")
        self.fs_row.set_model(Gtk.StringList.new([f[1] for f in manage.FILESYSTEMS]))
        self.fs_row.connect("notify::selected", lambda *_: self._validate())
        self.volume.add(self.fs_row)
        if mode == "create":
            gb = target_size / 1e9
            adj = Gtk.Adjustment(lower=0.02, upper=gb, value=gb, step_increment=1, page_increment=10)
            self.size_row = Adw.SpinRow(title="Size", subtitle="Gigabytes", adjustment=adj, digits=2)
            self.volume.add(self.size_row)
        page.add(self.volume)

        # Security
        self.security = Adw.PreferencesGroup(title="Security")
        self.encrypt_row = Adw.SwitchRow(title="Password protect volume",
                                         subtitle="LUKS encryption. Readable on Linux only")
        self.encrypt_row.connect("notify::active", lambda *_: self._sync())
        self.security.add(self.encrypt_row)
        self.pass_row = Adw.PasswordEntryRow(title="Password")
        self.pass2_row = Adw.PasswordEntryRow(title="Confirm password")
        for r in (self.pass_row, self.pass2_row):
            r.connect("changed", lambda *_: self._validate())
            self.security.add(r)
        if mode != "create":
            self.erase_row = Adw.ComboRow(title="Erase")
            self.erase_row.set_model(Gtk.StringList.new(["Quick", "Zero fill"]))
            self.erase_row.connect("notify::selected", lambda *_: self._sync())
            self.security.add(self.erase_row)
        page.add(self.security)

        self._sync()

    # ----------------------------------------------------------------
    def _fs(self):
        return manage.FILESYSTEMS[self.fs_row.get_selected()]

    def _has_volume(self):
        return self.mode != "disk" or self.content_row.get_selected() == 0

    def _sync(self):
        vol = self._has_volume()
        self.volume.set_visible(vol)
        self.encrypt_row.set_visible(vol)
        enc = vol and self.encrypt_row.get_active()
        self.pass_row.set_visible(enc)
        self.pass2_row.set_visible(enc)
        if self.mode != "create":
            if self.erase_row.get_selected() == 1:
                rate = self.write_rate or 120e6
                secs = self.target_size / rate
                self.erase_row.set_subtitle(f"Overwrites everything so old data can't be recovered. "
                                            f"Takes about {_duration(secs)}")
            else:
                self.erase_row.set_subtitle("Fast. Old data could still be recovered with special tools")
        self._validate()

    def _validate(self):
        ok = True
        if self.mode == "disk":
            mbr = self.scheme_row.get_selected() == 1
            too_big = mbr and self.target_size > 2 * 1024 ** 4
            self.scheme_row.set_subtitle(
                "MBR cannot use more than 2 TiB of this disk" if too_big else
                "For very old computers and some devices" if mbr else
                "Recommended. Works with every computer from the last 15 years")
            ok &= not too_big
        if self._has_volume():
            fstype, _label, desc, max_len = self._fs()
            if fstype not in self._caps:
                self._caps[fstype] = manage.can_format(self.client, fstype)
            available, util = self._caps[fstype]
            self.fs_row.set_subtitle(desc if available else f"Not available: install {util}")
            ok &= available
            name = self.name_row.get_text()
            too_long = len(name.encode()) > max_len
            if too_long:
                self.name_row.add_css_class("error")
            else:
                self.name_row.remove_css_class("error")
            ok &= not too_long
            if self.encrypt_row.get_active():
                p1, p2 = self.pass_row.get_text(), self.pass2_row.get_text()
                mismatch = bool(p2) and p1 != p2
                if mismatch:
                    self.pass2_row.add_css_class("error")
                else:
                    self.pass2_row.remove_css_class("error")
                ok &= bool(p1) and p1 == p2
        self.go.set_sensitive(ok)

    def _on_go(self, *_):
        vol = self._has_volume()
        params = dict(
            scheme=("gpt", "dos")[self.scheme_row.get_selected()] if self.mode == "disk" else None,
            fstype=self._fs()[0] if vol else None,
            label=self.name_row.get_text().strip() if vol else "",
            passphrase=self.pass_row.get_text() if vol and self.encrypt_row.get_active() else None,
            erase=self.mode != "create" and self.erase_row.get_selected() == 1,
            size=0,
        )
        if self.mode == "create":
            gb = self.size_row.get_value()
            params["size"] = 0 if gb >= self.target_size / 1e9 - 0.005 else int(gb * 1e9)
        self.close()
        self.on_submit(params)


def _duration(secs):
    if secs < 90:
        return f"{secs:.0f} seconds"
    if secs < 5400:
        return f"{secs / 60:.0f} minutes"
    return f"{secs / 3600:.1f} hours"
