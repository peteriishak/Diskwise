"""Thin synchronous/asynchronous wrapper around the UDisks2 D-Bus API."""

from gi.repository import Gio, GLib

BUS_NAME = "org.freedesktop.UDisks2"
ROOT = "/org/freedesktop/UDisks2"

IFACE_DRIVE = "org.freedesktop.UDisks2.Drive"
IFACE_ATA = "org.freedesktop.UDisks2.Drive.Ata"
IFACE_NVME = "org.freedesktop.UDisks2.NVMe.Controller"
IFACE_BLOCK = "org.freedesktop.UDisks2.Block"
IFACE_PARTITION = "org.freedesktop.UDisks2.Partition"
IFACE_PARTITION_TABLE = "org.freedesktop.UDisks2.PartitionTable"
IFACE_FILESYSTEM = "org.freedesktop.UDisks2.Filesystem"
IFACE_ENCRYPTED = "org.freedesktop.UDisks2.Encrypted"
IFACE_SWAP = "org.freedesktop.UDisks2.Swapspace"
IFACE_JOB = "org.freedesktop.UDisks2.Job"
IFACE_MANAGER = "org.freedesktop.UDisks2.Manager"
MANAGER = ROOT + "/Manager"
FOREVER = GLib.MAXINT32  # timeout for long operations such as erasing a disk

NO_OPTIONS = GLib.Variant("(a{sv})", ({},))


def bytestring(value):
    """Decode a D-Bus 'ay' (list of ints, NUL-terminated) into str."""
    if not value:
        return ""
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
    else:
        raw = bytes(value)
    return raw.rstrip(b"\0").decode("utf-8", "replace")


class UDisksClient:
    def __init__(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        self._subscriptions = []

    def available(self):
        try:
            self.bus.call_sync(
                BUS_NAME, ROOT + "/Manager", "org.freedesktop.DBus.Peer", "Ping",
                None, None, Gio.DBusCallFlags.NONE, 2000, None)
            return True
        except GLib.Error:
            return False

    def managed_objects(self):
        result = self.bus.call_sync(
            BUS_NAME, ROOT, "org.freedesktop.DBus.ObjectManager", "GetManagedObjects",
            None, GLib.VariantType("(a{oa{sa{sv}}})"), Gio.DBusCallFlags.NONE, 10000, None)
        return result.unpack()[0]

    def get_properties(self, path, iface):
        result = self.bus.call_sync(
            BUS_NAME, path, "org.freedesktop.DBus.Properties", "GetAll",
            GLib.Variant("(s)", (iface,)), GLib.VariantType("(a{sv})"),
            Gio.DBusCallFlags.NONE, 5000, None)
        return result.unpack()[0]

    def call(self, path, iface, method, params=NO_OPTIONS, interactive=False, timeout=15000):
        flags = (Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION
                 if interactive else Gio.DBusCallFlags.NONE)
        result = self.bus.call_sync(BUS_NAME, path, iface, method, params, None,
                                    flags, timeout, None)
        return result.unpack() if result is not None else None

    def call_async(self, path, iface, method, params=NO_OPTIONS, callback=None,
                   interactive=True, timeout=-1):
        """Call a method asynchronously. callback(result_or_None, error_or_None)."""
        flags = (Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION
                 if interactive else Gio.DBusCallFlags.NONE)

        def done(bus, res):
            try:
                value = bus.call_finish(res)
                if callback:
                    callback(value.unpack() if value is not None else None, None)
            except GLib.Error as err:
                if callback:
                    callback(None, err)

        self.bus.call(BUS_NAME, path, iface, method, params, None, flags, timeout, None, done)

    def open_for_benchmark(self, block_path):
        """Open a block device read-only through UDisks (polkit prompt). Returns an fd."""
        params = GLib.Variant("(a{sv})", ({},))
        result, fd_list = self.bus.call_with_unix_fd_list_sync(
            BUS_NAME, block_path, IFACE_BLOCK, "OpenForBenchmark", params,
            GLib.VariantType("(h)"), Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION,
            -1, None, None)
        index = result.unpack()[0]
        return fd_list.get(index)

    def watch_objects(self, callback):
        """Invoke callback() whenever drives/blocks appear or disappear."""
        for member in ("InterfacesAdded", "InterfacesRemoved"):
            sid = self.bus.signal_subscribe(
                BUS_NAME, "org.freedesktop.DBus.ObjectManager", member, ROOT, None,
                Gio.DBusSignalFlags.NONE, lambda *a: callback(), )
            self._subscriptions.append(sid)

    @staticmethod
    def error_message(err):
        if err is None:
            return ""
        msg = err.message or str(err)
        try:
            Gio.DBusError.strip_remote_error(err)
            msg = err.message
        except Exception:
            pass
        if msg.startswith("GDBus.Error:"):
            msg = msg.split(":", 2)[-1].strip()
        return msg
