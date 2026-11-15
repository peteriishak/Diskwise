"""Disk management operations (format, partition, mount, power) through UDisks2.

All functions here are blocking and meant to run in a worker thread. Each one
lets UDisks ask for authorization through polkit when needed.
"""

import json
import subprocess
import time

from gi.repository import GLib

from . import udisks as ud

MiB = 1024 * 1024

# (udisks type, label, description, max label length)
FILESYSTEMS = [
    ("ext4", "Ext4", "Linux only. The usual choice for internal data disks", 16),
    ("btrfs", "Btrfs", "Linux only. Snapshots, compression and checksums", 255),
    ("xfs", "XFS", "Linux only. Suited to large files and heavy workloads", 12),
    ("exfat", "exFAT", "Works with Linux, Windows and macOS. Best for USB drives", 15),
    ("ntfs", "NTFS", "Windows. Readable by Linux and macOS", 32),
    ("vfat", "FAT32", "Works with almost everything. Files up to 4 GB", 11),
]
LINUX_FS = {"ext4", "btrfs", "xfs"}

# Mount points that mean "the running system lives here".
SYSTEM_MOUNTS = {"/", "/boot", "/boot/efi", "/efi", "/home", "/usr", "/var", "/var/home",
                 "/sysroot", "/opt", "/srv", "[SWAP]"}

# hdparm -S standby values, as accepted by UDisks' ata-pm-standby.
STANDBY_CHOICES = [(None, "Drive default"), (0, "Never"), (60, "5 minutes"), (120, "10 minutes"),
                   (240, "20 minutes"), (241, "30 minutes"), (242, "1 hour"), (244, "2 hours")]
APM_CHOICES = [(None, "Drive default"), (1, "Maximum power saving"), (127, "Balanced, may spin down"),
               (254, "Performance, never spins down"), (255, "Disabled")]
WRITE_CACHE_CHOICES = [(None, "Drive default"), (True, "Enabled"), (False, "Disabled")]


class OperationError(Exception):
    pass


def _variant_dict(options):
    out = {}
    for key, value in options.items():
        if isinstance(value, GLib.Variant):
            out[key] = value
        elif isinstance(value, bool):
            out[key] = GLib.Variant("b", value)
        elif isinstance(value, int):
            out[key] = GLib.Variant("i", value)
        else:
            out[key] = GLib.Variant("s", str(value))
    return out


def _opts(options=None):
    return GLib.Variant("(a{sv})", (_variant_dict(options or {}),))


def _call(client, path, iface, method, params=None):
    try:
        return client.call(path, iface, method, params if params is not None else _opts(),
                           interactive=True, timeout=ud.FOREVER)
    except GLib.Error as err:
        msg = client.error_message(err)
        if "dismissed" in msg or "NotAuthorized" in err.message or "not authorized" in msg.lower():
            msg = "Authorization was not granted"
        raise OperationError(msg) from None


# ------------------------------------------------------------------ safety

def _lsblk_mounts(device):
    try:
        out = subprocess.run(["lsblk", "-J", "-o", "NAME,MOUNTPOINTS", device],
                             capture_output=True, text=True, timeout=10).stdout
        data = json.loads(out)
    except (OSError, ValueError, subprocess.SubprocessError):
        return set()
    found = set()

    def walk(node):
        for m in node.get("mountpoints") or []:
            if m:
                found.add(m)
        for c in node.get("children") or []:
            walk(c)

    for node in data.get("blockdevices", []):
        walk(node)
    return found


def system_mounts(device):
    """System mount points on this device or anything stacked on it (LUKS, LVM)."""
    return sorted(m for m in _lsblk_mounts(device) if m in SYSTEM_MOUNTS)


def disk_busy_reason(disk, ctx=None):
    """Why whole-disk operations must be refused, or None."""
    crit = system_mounts(disk.device)
    if crit:
        return "This disk holds the running system (" + ", ".join(crit) + ")"
    if ctx is not None and ctx.runner:
        return "A speed test is running on this disk"
    return None


def partition_busy_reason(part):
    crit = system_mounts(part.device)
    if crit:
        return "Used by the running system (" + ", ".join(crit) + ")"
    return None


# ------------------------------------------------------------- free space

def free_regions(disk, minimum=16 * MiB):
    """Unpartitioned regions as (offset, size), aligned to 1 MiB."""
    if not disk.partition_table:
        return []
    end_limit = disk.size - (MiB if disk.partition_table == "gpt" else 0)
    regions, cursor = [], MiB
    for p in sorted(disk.partitions, key=lambda p: p.offset):
        if p.offset - cursor >= minimum:
            regions.append((cursor, p.offset - cursor))
        cursor = max(cursor, -(-(p.offset + p.size) // MiB) * MiB)
    if end_limit - cursor >= minimum:
        regions.append((cursor, end_limit - cursor))
    return regions


# --------------------------------------------------------------- queries

def can_format(client, fstype):
    try:
        # Returns a single (bs) struct: (available, missing utility)
        ok, util = client.call(ud.MANAGER, ud.IFACE_MANAGER, "CanFormat", GLib.Variant("(s)", (fstype,)))[0]
        return ok, util
    except GLib.Error:
        return True, ""


def can_check_repair(client, fstype):
    out = []
    for method in ("CanCheck", "CanRepair"):
        try:
            ok, _ = client.call(ud.MANAGER, ud.IFACE_MANAGER, method, GLib.Variant("(s)", (fstype,)))[0]
        except GLib.Error:
            ok = False
        out.append(ok)
    return tuple(out)


def jobs_for(client, paths):
    """Running UDisks jobs touching any of the given object paths."""
    paths = set(paths)
    jobs = []
    try:
        objects = client.managed_objects()
    except GLib.Error:
        return jobs
    for jpath, ifaces in objects.items():
        job = ifaces.get(ud.IFACE_JOB)
        if job and paths.intersection(job.get("Objects", [])):
            jobs.append(dict(path=jpath, operation=job.get("Operation", ""),
                             progress=job.get("Progress", 0.0), valid=job.get("ProgressValid", False),
                             rate=job.get("Rate", 0), end=job.get("ExpectedEndTime", 0),
                             cancelable=job.get("Cancelable", False)))
    return jobs


def cancel_job(client, job_path):
    _call(client, job_path, ud.IFACE_JOB, "Cancel")


def drive_configuration(client, disk):
    try:
        return client.get_properties(disk.drive_path, ud.IFACE_DRIVE).get("Configuration", {})
    except GLib.Error:
        return {}


# ------------------------------------------------------------- operations

def _format_options(fstype, label, passphrase, erase):
    opts = {"tear-down": True, "update-partition-type": True}
    if label:
        opts["label"] = label
    if fstype in LINUX_FS:
        opts["take-ownership"] = True
    if passphrase:
        opts["encrypt.passphrase"] = passphrase
        opts["encrypt.type"] = "luks2"
    if erase:
        opts["erase"] = "zero"
    return opts


def _wait_for_interface(client, path, iface, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if iface in client.managed_objects().get(path, {}):
                return True
        except GLib.Error:
            pass
        time.sleep(0.3)
    return False


def format_disk(client, disk, scheme, fstype=None, label="", passphrase=None, erase=False):
    """Create a new partition table, optionally with one partition filling the disk."""
    release_all(client, disk)
    opts = {"tear-down": True}
    if erase:
        opts["erase"] = "zero"
    _call(client, disk.block_path, ud.IFACE_BLOCK, "Format",
          GLib.Variant("(sa{sv})", (scheme, _variant_dict(opts))))
    if fstype:
        if not _wait_for_interface(client, disk.block_path, ud.IFACE_PARTITION_TABLE):
            raise OperationError("The new partition table did not appear")
        return create_partition(client, disk.block_path, scheme, MiB, 0, fstype, label, passphrase)
    return None


def create_partition(client, table_path, scheme, offset, size, fstype, label="", passphrase=None):
    """Create a partition (size 0 = all free space from offset) and format it."""
    name = label if scheme == "gpt" else ""
    params = GLib.Variant("(ttssa{sv}sa{sv})", (
        offset, size, "", name, {}, fstype,
        _variant_dict(_format_options(fstype, label, passphrase, False))))
    return _call(client, table_path, ud.IFACE_PARTITION_TABLE, "CreatePartitionAndFormat", params)[0]


def format_partition(client, part, fstype, label="", passphrase=None, erase=False):
    release(client, part.block_path)
    _call(client, part.block_path, ud.IFACE_BLOCK, "Format",
          GLib.Variant("(sa{sv})", (fstype, _variant_dict(_format_options(fstype, label, passphrase, erase)))))


def delete_partition(client, part):
    release(client, part.block_path)
    _call(client, part.block_path, ud.IFACE_PARTITION, "Delete", _opts({"tear-down": True}))


def fs_path(part):
    """Object path of the filesystem for a partition (the unlocked mapping if encrypted)."""
    return part.cleartext_path if part.encrypted else part.block_path


def mount(client, path):
    try:
        return _call(client, path, ud.IFACE_FILESYSTEM, "Mount")[0]
    except OperationError as err:
        if "already mounted" in str(err):  # the desktop may have automounted it first
            return None
        raise


def unmount(client, path):
    try:
        _call(client, path, ud.IFACE_FILESYSTEM, "Unmount")
    except OperationError as err:
        if "not mounted" not in str(err):
            raise


def unlock(client, part, passphrase):
    return _call(client, part.block_path, ud.IFACE_ENCRYPTED, "Unlock",
                 GLib.Variant("(sa{sv})", (passphrase, {})))[0]


def lock(client, part):
    if part.cleartext_mountpoints:
        unmount(client, part.cleartext_path)
    _call(client, part.block_path, ud.IFACE_ENCRYPTED, "Lock")


def set_label(client, path, label):
    _call(client, path, ud.IFACE_FILESYSTEM, "SetLabel", GLib.Variant("(sa{sv})", (label, {})))


def check_filesystem(client, path):
    return _call(client, path, ud.IFACE_FILESYSTEM, "Check")[0]


def repair_filesystem(client, path):
    return _call(client, path, ud.IFACE_FILESYSTEM, "Repair")[0]


def release(client, block_path, include_partitions=False):
    """Make a block device unused: unmount, stop swap, lock encryption.

    Reads fresh state, since the desktop may automount things at any moment.
    With include_partitions, does the same for every partition in its table.
    """
    objects = client.managed_objects()
    targets = [block_path]
    if include_partitions:
        targets += [p for p, i in objects.items()
                    if i.get(ud.IFACE_PARTITION, {}).get("Table") == block_path]
    for path in targets:
        ifaces = objects.get(path, {})
        for cpath, cifaces in objects.items():
            if cifaces.get(ud.IFACE_BLOCK, {}).get("CryptoBackingDevice") == path:
                if cifaces.get(ud.IFACE_FILESYSTEM, {}).get("MountPoints"):
                    unmount(client, cpath)
                _call(client, path, ud.IFACE_ENCRYPTED, "Lock")
        if ifaces.get(ud.IFACE_FILESYSTEM, {}).get("MountPoints"):
            unmount(client, path)
        if ifaces.get(ud.IFACE_SWAP, {}).get("Active"):
            _call(client, path, ud.IFACE_SWAP, "Stop")


def release_all(client, disk):
    release(client, disk.block_path, include_partitions=True)


def power_off(client, disk):
    release_all(client, disk)
    _call(client, disk.drive_path, ud.IFACE_DRIVE, "PowerOff")


def eject(client, disk):
    release_all(client, disk)
    _call(client, disk.drive_path, ud.IFACE_DRIVE, "Eject")


def standby(client, disk):
    _call(client, disk.drive_path, ud.IFACE_ATA, "PmStandby")


def wakeup(client, disk):
    _call(client, disk.drive_path, ud.IFACE_ATA, "PmWakeup")


def set_drive_setting(client, disk, key, value):
    """Persist one ATA setting through UDisks (applied now and at every boot)."""
    config = {k: v for k, v in drive_configuration(client, disk).items()}
    if value is None:
        config.pop(key, None)
    else:
        config[key] = value
    variants = {}
    for k, v in config.items():
        variants[k] = GLib.Variant("b", v) if isinstance(v, bool) else GLib.Variant("i", int(v))
    _call(client, disk.drive_path, ud.IFACE_DRIVE, "SetConfiguration",
          GLib.Variant("(a{sv}a{sv})", (variants, {})))
