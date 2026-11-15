"""Manage page: volumes, formatting, partitioning, power control and drive settings."""

import threading
import time

from gi.repository import Adw, Gio, GLib, Gtk

from ..backend import manage
from . import fmt
from .charts import Swatch
from .dialogs import FormatDialog, ask_password, ask_text, confirm_destructive
from .widgets import page_box


class ManagePage:
    def __init__(self, ctx, toast, request_reload):
        self.ctx = ctx
        self.toast = toast
        self.request_reload = request_reload
        self.busy = False
        self._poll_id = None
        self._job = None
        self._loading = False
        self._fs_caps = {}
        self.widget, box = page_box()

        # Protection notice
        self.protect = Gtk.Box(spacing=14)
        self.protect.add_css_class("card")
        self.protect.add_css_class("notice")
        icon = Gtk.Image.new_from_icon_name("security-high-symbolic")
        icon.set_pixel_size(32)
        icon.add_css_class("status-info")
        self.protect.append(icon)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True,
                      valign=Gtk.Align.CENTER)
        t = Gtk.Label(label="System disk protected", xalign=0)
        t.add_css_class("heading")
        col.append(t)
        self.protect_text = Gtk.Label(xalign=0, wrap=True)
        self.protect_text.add_css_class("dim-label")
        col.append(self.protect_text)
        self.protect.append(col)
        box.append(self.protect)

        # Running operation
        self.op = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.op.add_css_class("card")
        self.op.add_css_class("notice")
        head = Gtk.Box(spacing=10)
        self.op_spinner = Adw.Spinner()
        head.append(self.op_spinner)
        self.op_title = Gtk.Label(xalign=0, hexpand=True)
        self.op_title.add_css_class("heading")
        head.append(self.op_title)
        self.op_cancel = Gtk.Button(label="Cancel", valign=Gtk.Align.CENTER)
        self.op_cancel.add_css_class("pill-small")
        self.op_cancel.connect("clicked", self._on_cancel_job)
        head.append(self.op_cancel)
        self.op.append(head)
        self.op_bar = Gtk.ProgressBar()
        self.op.append(self.op_bar)
        self.op_detail = Gtk.Label(xalign=0)
        self.op_detail.add_css_class("caption")
        self.op_detail.add_css_class("dim-label")
        self.op.append(self.op_detail)
        self.op.set_visible(False)
        box.append(self.op)

        self.volumes = Adw.PreferencesGroup(title="Volumes")
        box.append(self.volumes)
        self.disk_group = Adw.PreferencesGroup(title="Disk")
        box.append(self.disk_group)
        self.settings = Adw.PreferencesGroup(
            title="Drive settings",
            description="Saved by UDisks and applied every time the drive is connected")
        box.append(self.settings)
        self._rows = {self.volumes: [], self.disk_group: [], self.settings: []}

        self.refresh()

    # ------------------------------------------------------------ helpers
    def _parent(self):
        return self.widget.get_root()

    def _clear(self, group):
        for row in self._rows[group]:
            group.remove(row)
        self._rows[group] = []

    def _add(self, group, row):
        group.add(row)
        self._rows[group].append(row)

    def _caps(self, fstype):
        if fstype not in self._fs_caps:
            self._fs_caps[fstype] = manage.can_check_repair(self.ctx.client, fstype)
        return self._fs_caps[fstype]

    # ------------------------------------------------------------ refresh
    def _signature(self):
        d = self.ctx.disk
        return (d.partition_table, d.whole_fstype, tuple(d.whole_mountpoints), self.busy,
                self.ctx.power.state.standby, self.ctx.runner is not None,
                repr(d.drive_props.get("Configuration")),
                tuple((p.name, p.size, p.fstype, p.label, tuple(p.all_mountpoints), p.cleartext_path,
                       p.swap_active) for p in d.partitions))

    def refresh(self):
        sig = self._signature()
        if sig == getattr(self, "_last_sig", None):
            return
        self._last_sig = sig
        d = self.ctx.disk
        self.disk_reason = manage.disk_busy_reason(d, self.ctx)
        system = self.disk_reason and "running system" in self.disk_reason
        self.protect.set_visible(bool(system))
        if system:
            self.protect_text.set_label(
                f"{self.disk_reason}. Formatting the disk and changing system partitions are "
                "disabled. Other partitions can still be managed.")
        self._build_volumes()
        self._build_disk_actions()
        self._build_settings()
        for g in (self.volumes, self.disk_group, self.settings):
            g.set_sensitive(not self.busy)

    def _build_volumes(self):
        self._clear(self.volumes)
        d = self.ctx.disk
        items = [("part", p.offset, p) for p in d.partitions]
        items += [("free", off, (off, size)) for off, size in manage.free_regions(d)]
        items.sort(key=lambda x: x[1])
        if d.partition_table:
            self.volumes.set_description(f"{d.partition_table.upper()} partition table")
        elif d.whole_fstype:
            self.volumes.set_description(f"{d.whole_fstype} filesystem on the whole disk, no partitions")
        else:
            self.volumes.set_description("No partition table. Format the disk to start using it.")
        if not items:
            row = Adw.ActionRow(title="No volumes")
            self._add(self.volumes, row)
            return
        part_index = {id(p): i for i, p in enumerate(d.partitions)}
        for kind, _off, item in items:
            if kind == "part":
                self._add(self.volumes, self._partition_row(item, part_index[id(item)]))
            else:
                self._add(self.volumes, self._free_row(*item))

    def _partition_row(self, p, index):
        reason = manage.partition_busy_reason(p)
        title = p.label or p.part_name or f"Partition {p.number}"
        fstype = p.cleartext_fstype if p.encrypted and p.cleartext_path else p.fstype
        bits = [p.name, fmt.size(p.size)]
        if p.encrypted:
            bits.append(f"Encrypted, {fstype or 'unlocked'}" if p.cleartext_path else "Encrypted, locked")
        elif fstype:
            bits.append(fstype.replace("swap", "Swap"))
        else:
            bits.append("No filesystem")
        mounts = p.all_mountpoints
        if mounts:
            bits.append("Mounted at " + ", ".join(mounts))
        elif p.swap_active:
            bits.append("Active swap")
        row = Adw.ActionRow(title=title, subtitle=" · ".join(bits), subtitle_lines=2)
        row.add_prefix(Swatch(index))

        if reason:
            tag = Gtk.Label(label="System", valign=Gtk.Align.CENTER, tooltip_text=reason)
            tag.add_css_class("tag")
            tag.add_css_class("tag-neutral")
            row.add_suffix(tag)

        fs_path = manage.fs_path(p)
        has_fs = p.has_filesystem or (p.encrypted and p.cleartext_path and p.cleartext_fstype)
        primary = None
        if p.encrypted and not p.cleartext_path:
            primary = ("Unlock", lambda: self._unlock(p))
        elif has_fs and not mounts:
            primary = ("Mount", lambda: self._run(f"Mounting {title}",
                                                  lambda: manage.mount(self.ctx.client, fs_path),
                                                  lambda mp: f"Mounted at {mp}" if mp else f"{title} mounted"))
        elif has_fs and mounts and not reason:
            primary = ("Unmount", lambda: self._run(f"Unmounting {title}",
                                                    lambda: manage.unmount(self.ctx.client, fs_path),
                                                    f"{title} unmounted"))
        if primary:
            btn = Gtk.Button(label=primary[0], valign=Gtk.Align.CENTER)
            btn.add_css_class("pill-small")
            btn.connect("clicked", lambda *_a, f=primary[1]: f())
            row.add_suffix(btn)

        # Menu
        group = Gio.SimpleActionGroup()
        menu = Gio.Menu()
        section1, section2 = Gio.Menu(), Gio.Menu()

        def action(name, label, cb, enabled=True, section=section1):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda *_: cb())
            a.set_enabled(bool(enabled))
            group.add_action(a)
            section.append(label, f"part.{name}")

        if mounts:
            action("open", "Open in Files", lambda: self._open(mounts[0]))
        if has_fs:
            action("label", "Rename…", lambda: self._rename(p, fs_path, title), not reason)
            can_check, can_repair = self._caps(fstype)
            action("check", "Check Filesystem", lambda: self._check(p, fs_path, title, repair=False),
                   can_check and not mounts)
            action("repair", "Repair Filesystem…", lambda: self._check(p, fs_path, title, repair=True),
                   can_repair and not mounts and not reason)
        if p.encrypted and p.cleartext_path:
            action("lock", "Lock", lambda: self._run(f"Locking {title}", lambda: manage.lock(self.ctx.client, p),
                                                     f"{title} locked"), not reason)
        action("format", "Format Partition…", lambda: self._format_partition(p, title), not reason,
               section2)
        action("delete", "Delete Partition…", lambda: self._delete_partition(p, title), not reason,
               section2)
        menu.append_section(None, section1)
        menu.append_section(None, section2)
        mbtn = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu, valign=Gtk.Align.CENTER,
                              tooltip_text="More actions")
        mbtn.add_css_class("flat")
        mbtn.insert_action_group("part", group)
        row.add_suffix(mbtn)
        return row

    def _free_row(self, offset, size):
        row = Adw.ActionRow(title="Free space", subtitle=f"{fmt.size(size)} unallocated")
        icon = Gtk.Image.new_from_icon_name("list-add-symbolic")
        icon.add_css_class("dim-label")
        row.add_prefix(icon)
        btn = Gtk.Button(label="Create Partition…", valign=Gtk.Align.CENTER)
        btn.add_css_class("pill-small")
        btn.connect("clicked", lambda *_: self._create_partition(offset, size))
        row.add_suffix(btn)
        return row

    def _build_disk_actions(self):
        self._clear(self.disk_group)
        d = self.ctx.disk
        row = Adw.ActionRow(title="Format disk",
                            subtitle=self.disk_reason or "Erase everything and start with a new partition table")
        btn = Gtk.Button(label="Format Disk…", valign=Gtk.Align.CENTER, sensitive=not self.disk_reason)
        btn.add_css_class("pill-small")
        btn.add_css_class("destructive-action")
        btn.connect("clicked", lambda *_: self._format_disk())
        row.add_suffix(btn)
        self._add(self.disk_group, row)

        if d.drive_props.get("CanPowerOff"):
            self._add(self.disk_group, self._action_row(
                "Power off", "Unmounts everything and powers the drive down so it can be unplugged safely",
                "Power Off", "system-shutdown-symbolic", self._power_off, not self.disk_reason))
        if d.drive_props.get("Ejectable"):
            self._add(self.disk_group, self._action_row(
                "Eject", "Unmounts everything and ejects the media", "Eject", "media-eject-symbolic",
                lambda: self._run("Ejecting", lambda: manage.eject(self.ctx.client, d), "Ejected"),
                not self.disk_reason))
        if d.has_ata and d.ata_props.get("PmSupported") and d.kind == "hdd":
            standby = self.ctx.power.state.standby
            if standby:
                self._add(self.disk_group, self._action_row(
                    "Drive is spun down", "Spin it up again now instead of on next access",
                    "Wake Up", None,
                    lambda: self._run("Waking up drive", lambda: manage.wakeup(self.ctx.client, d), "Drive is awake")))
            else:
                self._add(self.disk_group, self._action_row(
                    "Spin down now", "Stops the platters to save power and noise. The drive wakes on next access",
                    "Standby", None,
                    lambda: self._run("Spinning down", lambda: manage.standby(self.ctx.client, d),
                                      "Drive is in standby"),
                    not (self.disk_reason and "running system" in self.disk_reason)))

    def _action_row(self, title, subtitle, label, icon, cb, enabled=True):
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        if icon:
            row.add_prefix(Gtk.Image.new_from_icon_name(icon))
        btn = Gtk.Button(label=label, valign=Gtk.Align.CENTER, sensitive=enabled)
        btn.add_css_class("pill-small")
        btn.connect("clicked", lambda *_: cb())
        row.add_suffix(btn)
        return row

    def _build_settings(self):
        self._clear(self.settings)
        d = self.ctx.disk
        a = d.ata_props
        if not d.has_ata:
            self.settings.set_visible(False)
            return
        config = d.drive_props.get("Configuration", {}) or {}
        self._loading = True
        specs = []
        if a.get("PmSupported"):
            specs.append(("ata-pm-standby", "Standby timeout",
                          "Spin down after this much idle time", manage.STANDBY_CHOICES))
        if a.get("ApmSupported"):
            specs.append(("ata-apm-level", "Power management level",
                          "Trade-off between power saving and performance", manage.APM_CHOICES))
        if a.get("WriteCacheSupported"):
            specs.append(("ata-write-cache-enabled", "Write cache",
                          "Faster writes; cached data can be lost if power fails", manage.WRITE_CACHE_CHOICES))
        for key, title, subtitle, choices in specs:
            row = Adw.ComboRow(title=title, subtitle=subtitle)
            row.set_model(Gtk.StringList.new([label for _, label in choices]))
            current = config.get(key)
            idx = next((i for i, (v, _) in enumerate(choices) if v is not None and v == current), 0)
            row.set_selected(idx)
            row.connect("notify::selected", self._on_setting, key, choices)
            self._add(self.settings, row)
        self.settings.set_visible(bool(specs))
        self._loading = False

    # ---------------------------------------------------------- operations
    def _run(self, title, fn, done, error_prefix=None):
        if self.busy:
            return
        self.busy = True
        for g in (self.volumes, self.disk_group, self.settings):
            g.set_sensitive(False)
        self.op_title.set_label(title + "…")
        self.op_detail.set_label("Starting. You may be asked for your password")
        self.op_bar.set_fraction(0)
        self.op_cancel.set_visible(False)
        self.op.set_visible(True)
        paths = [self.ctx.disk.block_path] + [p.block_path for p in self.ctx.disk.partitions]
        self._poll_id = GLib.timeout_add(700, self._poll_jobs, paths)

        def work():
            try:
                result = fn()
                GLib.idle_add(self._finish, result, None, done)
            except Exception as err:  # surface every failure to the user
                GLib.idle_add(self._finish, None, str(err), (error_prefix or title))

        threading.Thread(target=work, daemon=True).start()

    def _finish(self, result, error, msg):
        self.busy = False
        if self._poll_id:
            GLib.source_remove(self._poll_id)
            self._poll_id = None
        self._job = None
        self.op.set_visible(False)
        if error:
            self.toast(f"{msg} failed: {error}")
        else:
            self.toast(msg(result) if callable(msg) else msg)
        self.request_reload()
        return False

    def _poll_jobs(self, paths):
        jobs = manage.jobs_for(self.ctx.client, paths)
        if not jobs:
            self.op_bar.pulse()
            return True
        job = jobs[0]
        self._job = job
        self.op_cancel.set_visible(job["cancelable"])
        name = job["operation"].replace("format-", "").replace("-", " ").capitalize()
        if job["valid"]:
            self.op_bar.set_fraction(job["progress"])
            detail = f"{name} · {job['progress'] * 100:.0f}%"
            if job["rate"]:
                detail += f" · {fmt.rate(job['rate'])}"
            if job["end"]:
                left = job["end"] / 1e6 - time.time()
                if left > 0:
                    detail += f" · {left / 60:.0f} min left" if left > 90 else f" · {left:.0f} s left"
            self.op_detail.set_label(detail)
        else:
            self.op_bar.pulse()
            self.op_detail.set_label(name)
        return True

    def _on_cancel_job(self, *_):
        job = self._job
        if job:
            threading.Thread(target=lambda: _quiet(manage.cancel_job, self.ctx.client, job["path"]),
                             daemon=True).start()

    # -------------------------------------------------------------- actions
    def _write_rate(self):
        fr, sr = self.ctx.file_result, self.ctx.surface_result
        if fr and fr.seq_write:
            return fr.seq_write
        if sr and sr.avg:
            return sr.avg * 0.9
        return {"nvme": 1.0e9, "ssd": 400e6, "hdd": 110e6}.get(self.ctx.disk.kind, 100e6)

    def _contents_summary(self, parts):
        lines = []
        for p in parts:
            name = p.label or p.part_name or p.name
            desc = f"{name} ({fmt.size(p.size)}"
            if p.used is not None:
                desc += f", {fmt.size(p.used)} used"
            desc += ")"
            if p.all_mountpoints:
                desc += f" mounted at {', '.join(p.all_mountpoints)}"
            lines.append("• " + GLib.markup_escape_text(desc))
        return "\n".join(lines)

    def _format_disk(self):
        d = self.ctx.disk
        reason = manage.disk_busy_reason(d, self.ctx)
        if reason:
            self.toast(reason)
            return

        def submit(params):
            body = f"All data on <b>{GLib.markup_escape_text(d.title)}</b> ({fmt.size(d.size)}) " \
                   "will be permanently lost."
            if d.partitions:
                body += "\n\nThis includes:\n" + self._contents_summary(d.partitions)
            confirm_destructive(
                self._parent(), f"Erase {d.name}?", body, d.name, "Erase and Format",
                lambda: self._run(f"Formatting {d.name}",
                                  lambda: manage.format_disk(self.ctx.client, d, params["scheme"],
                                                             params["fstype"], params["label"],
                                                             params["passphrase"], params["erase"]),
                                  f"{d.name} formatted"))

        FormatDialog(self.ctx.client, "disk", f"{d.title} ({d.name})", d.size, submit,
                     self._write_rate()).present(self._parent())

    def _format_partition(self, p, title):
        if manage.partition_busy_reason(p):
            return

        def submit(params):
            body = "Everything on this partition will be permanently lost:\n" + self._contents_summary([p])
            confirm_destructive(
                self._parent(), f"Erase {p.name}?", body, p.name, "Erase and Format",
                lambda: self._run(f"Formatting {title}",
                                  lambda: manage.format_partition(self.ctx.client, p, params["fstype"],
                                                                  params["label"], params["passphrase"],
                                                                  params["erase"]),
                                  f"{p.name} formatted"))

        FormatDialog(self.ctx.client, "partition", f"{title} ({p.name})", p.size, submit,
                     self._write_rate()).present(self._parent())

    def _delete_partition(self, p, title):
        if manage.partition_busy_reason(p):
            return
        body = "The partition and everything on it will be permanently lost:\n" + self._contents_summary([p])
        confirm_destructive(self._parent(), f"Delete {p.name}?", body, p.name, "Delete",
                            lambda: self._run(f"Deleting {title}",
                                              lambda: manage.delete_partition(self.ctx.client, p),
                                              f"{p.name} deleted"))

    def _create_partition(self, offset, size):
        d = self.ctx.disk

        def submit(params):
            self._run("Creating partition",
                      lambda: manage.create_partition(self.ctx.client, d.block_path, d.partition_table,
                                                      offset, params["size"], params["fstype"],
                                                      params["label"], params["passphrase"]),
                      "Partition created")

        FormatDialog(self.ctx.client, "create", f"Free space on {d.name}", size, submit).present(self._parent())

    def _unlock(self, p):
        ask_password(self._parent(), f"Unlock {p.name}", "Enter the password for this encrypted volume.",
                     "Unlock",
                     lambda pw: self._run(f"Unlocking {p.name}", lambda: manage.unlock(self.ctx.client, p, pw),
                                          f"{p.name} unlocked"))

    def _rename(self, p, path, title):
        max_len = next((f[3] for f in manage.FILESYSTEMS
                        if f[0] == (p.cleartext_fstype if p.encrypted else p.fstype)), 32)
        ask_text(self._parent(), "Rename Volume", f"New name for {title}", p.label, "Rename",
                 lambda name: self._run("Renaming", lambda: manage.set_label(self.ctx.client, path, name),
                                        f"Renamed to {name}" if name else "Name removed"), max_len)

    def _check(self, p, path, title, repair):
        if repair:
            confirm_destructive(
                self._parent(), f"Repair {p.name}?",
                "Repairing can lose data in damaged areas. Back up first if you can.", p.name, "Repair",
                lambda: self._run(f"Repairing {title}", lambda: manage.repair_filesystem(self.ctx.client, path),
                                  lambda ok: "Filesystem repaired" if ok else "Repair finished with problems"))
        else:
            self._run(f"Checking {title}", lambda: manage.check_filesystem(self.ctx.client, path),
                      lambda ok: f"{title}: no problems found" if ok else f"{title} has errors. Try Repair")

    def _power_off(self):
        d = self.ctx.disk
        self._run(f"Powering off {d.name}", lambda: manage.power_off(self.ctx.client, d),
                  f"{d.title} can be unplugged now")

    def _open(self, mountpoint):
        Gio.AppInfo.launch_default_for_uri(GLib.filename_to_uri(mountpoint, None), None)

    def _on_setting(self, row, _pspec, key, choices):
        if self._loading:
            return
        value = choices[row.get_selected()][0]
        label = choices[row.get_selected()][1]
        self._run("Applying drive setting",
                  lambda: manage.set_drive_setting(self.ctx.client, self.ctx.disk, key, value),
                  f"{row.get_title()}: {label}")

    def tick(self):
        pass


def _quiet(fn, *args):
    try:
        fn(*args)
    except Exception:
        pass
