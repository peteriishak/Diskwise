"""Disk discovery: combines UDisks2 objects with sysfs details."""

import glob
import os
from dataclasses import dataclass, field

from gi.repository import GLib

from . import udisks as ud
from .udisks import bytestring


def read_sys(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def read_int(path, default=None):
    value = read_sys(path)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass
class Partition:
    name: str
    device: str
    number: int
    size: int
    offset: int
    fstype: str = ""
    label: str = ""
    part_name: str = ""
    mountpoints: list = field(default_factory=list)
    used: int | None = None
    free: int | None = None
    block_path: str = ""
    id_usage: str = ""
    has_filesystem: bool = False
    encrypted: bool = False
    cleartext_path: str = ""      # unlocked LUKS mapping, if any
    cleartext_fstype: str = ""
    cleartext_mountpoints: list = field(default_factory=list)
    swap_active: bool = False
    type_id: str = ""

    @property
    def all_mountpoints(self):
        return self.mountpoints + self.cleartext_mountpoints

    @property
    def display_name(self):
        return self.label or self.part_name or self.name


@dataclass
class Disk:
    drive_path: str
    block_path: str
    name: str
    device: str
    model: str
    vendor: str
    serial: str
    firmware: str
    size: int
    wwn: str
    rotation_rate: int
    connection_bus: str
    removable: bool
    media: str
    has_ata: bool
    has_nvme: bool
    partition_table: str = ""
    partitions: list = field(default_factory=list)
    whole_fstype: str = ""            # filesystem directly on the disk (no partitions)
    whole_mountpoints: list = field(default_factory=list)
    drive_props: dict = field(default_factory=dict)
    ata_props: dict = field(default_factory=dict)
    nvme_props: dict = field(default_factory=dict)
    smart_attrs: object = None  # list (ATA) or dict (NVMe)
    smart_error: str = ""

    @property
    def kind(self):
        """One of: nvme, ssd, hdd, usb-flash, unknown."""
        if self.has_nvme or self.name.startswith("nvme"):
            return "nvme"
        if self.rotation_rate > 0:
            return "hdd"
        if self.rotation_rate == 0:
            return "ssd"
        rot = read_int(f"/sys/block/{self.name}/queue/rotational")
        if rot == 1 and self.connection_bus != "usb":
            return "hdd"
        if self.connection_bus == "usb" and self.removable:
            return "usb-flash"
        return "ssd" if rot == 0 else "unknown"

    @property
    def kind_label(self):
        return {
            "nvme": "NVMe SSD",
            "ssd": "SATA SSD" if self.connection_bus != "usb" else "SSD",
            "hdd": "Hard disk",
            "usb-flash": "Flash drive",
            "unknown": "Disk",
        }[self.kind]

    @property
    def icon_name(self):
        if self.connection_bus == "usb":
            return "drive-harddisk-usb-symbolic" if self.kind != "usb-flash" else "drive-removable-media-symbolic"
        if self.kind in ("nvme", "ssd"):
            return "drive-harddisk-solidstate-symbolic"
        return "drive-harddisk-symbolic"

    @property
    def interface_label(self):
        if self.kind == "nvme":
            return "NVMe"
        if self.connection_bus == "usb":
            return "USB"
        if self.connection_bus:
            return self.connection_bus.upper()
        tran = read_sys(f"/sys/block/{self.name}/device/transport")
        if self.has_ata:
            return "SATA"
        return tran.upper() if tran else "Unknown"

    @property
    def form_factor_hint(self):
        """Best guess for spinning drives: 2.5-inch laptop drives spin at <= 5400 rpm."""
        if self.kind != "hdd":
            return None
        if 0 < self.rotation_rate <= 5400 or self.model.upper().startswith(("ST1000LM", "ST2000LM", "WD10SPZX", "HTS", "MQ0")):
            return "2.5"
        return "3.5"

    @property
    def title(self):
        model = self.model.strip() or self.name
        vendor = self.vendor.strip()
        if vendor and not model.lower().startswith(vendor.lower()):
            return f"{vendor} {model}"
        return model


def _drive_from_objects(path, ifaces, blocks):
    drive = ifaces[ud.IFACE_DRIVE]
    block_path, block = None, None
    for bpath, bifaces in blocks.items():
        b = bifaces[ud.IFACE_BLOCK]
        if b.get("Drive") == path and ud.IFACE_PARTITION not in bifaces:
            block_path, block = bpath, b
            break
    if block is None:
        return None
    device = bytestring(block.get("PreferredDevice")) or bytestring(block.get("Device"))
    name = os.path.basename(bytestring(block.get("Device")))
    disk = Disk(
        drive_path=path,
        block_path=block_path,
        name=name,
        device=device,
        model=drive.get("Model", "").strip(),
        vendor=drive.get("Vendor", "").strip(),
        serial=drive.get("Serial", "").strip(),
        firmware=drive.get("Revision", "").strip(),
        size=drive.get("Size", 0) or block.get("Size", 0),
        wwn=drive.get("WWN", ""),
        rotation_rate=drive.get("RotationRate", -1),
        connection_bus=drive.get("ConnectionBus", ""),
        removable=drive.get("Removable", False),
        media=drive.get("Media", ""),
        has_ata=ud.IFACE_ATA in ifaces,
        has_nvme=ud.IFACE_NVME in ifaces,
        drive_props=drive,
        ata_props=ifaces.get(ud.IFACE_ATA, {}),
        nvme_props=ifaces.get(ud.IFACE_NVME, {}),
    )
    pt = blocks[block_path].get(ud.IFACE_PARTITION_TABLE)
    if pt:
        disk.partition_table = pt.get("Type", "")
    if ud.IFACE_FILESYSTEM in blocks[block_path]:
        disk.whole_fstype = block.get("IdType", "")
        disk.whole_mountpoints = [bytestring(m) for m in
                                  blocks[block_path][ud.IFACE_FILESYSTEM].get("MountPoints", [])]

    parts = []
    for bpath, bifaces in blocks.items():
        part = bifaces.get(ud.IFACE_PARTITION)
        if not part or part.get("Table") != block_path:
            continue
        b = bifaces[ud.IFACE_BLOCK]
        fs = bifaces.get(ud.IFACE_FILESYSTEM)
        mounts = [bytestring(m) for m in fs.get("MountPoints", [])] if fs else []
        p = Partition(
            name=os.path.basename(bytestring(b.get("Device"))),
            device=bytestring(b.get("Device")),
            number=part.get("Number", 0),
            size=part.get("Size", 0) or b.get("Size", 0),
            offset=part.get("Offset", 0),
            fstype=b.get("IdType", ""),
            label=b.get("IdLabel", ""),
            part_name=part.get("Name", ""),
            mountpoints=mounts,
            block_path=bpath,
            id_usage=b.get("IdUsage", ""),
            has_filesystem=fs is not None,
            encrypted=ud.IFACE_ENCRYPTED in bifaces,
            swap_active=bifaces.get(ud.IFACE_SWAP, {}).get("Active", False),
            type_id=part.get("Type", ""),
        )
        if p.encrypted:
            for cpath, cifaces in blocks.items():
                cb = cifaces[ud.IFACE_BLOCK]
                if cb.get("CryptoBackingDevice") == bpath:
                    p.cleartext_path = cpath
                    p.cleartext_fstype = cb.get("IdType", "")
                    cfs = cifaces.get(ud.IFACE_FILESYSTEM)
                    if cfs:
                        p.cleartext_mountpoints = [bytestring(m) for m in cfs.get("MountPoints", [])]
                    if p.cleartext_mountpoints and not mounts:
                        mounts = p.cleartext_mountpoints
                    break
        if mounts:
            try:
                st = os.statvfs(mounts[0])
                total = st.f_blocks * st.f_frsize
                p.free = st.f_bavail * st.f_frsize
                p.used = total - st.f_bfree * st.f_frsize
            except OSError:
                pass
        parts.append(p)
    parts.sort(key=lambda p: p.offset)
    disk.partitions = parts
    return disk


def discover(client):
    objects = client.managed_objects()
    blocks = {p: i for p, i in objects.items() if ud.IFACE_BLOCK in i}
    disks = []
    for path, ifaces in objects.items():
        if ud.IFACE_DRIVE not in ifaces:
            continue
        disk = _drive_from_objects(path, ifaces, blocks)
        if disk and disk.size > 0:
            disks.append(disk)
    order = {"nvme": 0, "ssd": 1, "hdd": 2, "usb-flash": 3, "unknown": 4}
    disks.sort(key=lambda d: (d.connection_bus == "usb", order.get(d.kind, 9), d.name))
    return disks


def refresh_smart(client, disk):
    """Re-read SMART properties and attributes from UDisks (no privileges needed)."""
    disk.smart_error = ""
    try:
        if disk.has_ata:
            disk.ata_props = client.get_properties(disk.drive_path, ud.IFACE_ATA)
            if disk.ata_props.get("SmartSupported") and disk.ata_props.get("SmartEnabled"):
                disk.smart_attrs = client.call(disk.drive_path, ud.IFACE_ATA, "SmartGetAttributes")[0]
        elif disk.has_nvme:
            disk.nvme_props = client.get_properties(disk.drive_path, ud.IFACE_NVME)
            disk.smart_attrs = client.call(disk.drive_path, ud.IFACE_NVME, "SmartGetAttributes")[0]
    except GLib.Error as err:
        disk.smart_error = client.error_message(err)


# ---------------------------------------------------------------- sysfs extras

def sysfs_details(disk):
    """Static-ish details that UDisks does not expose."""
    base = f"/sys/block/{disk.name}"
    d = {
        "logical_block": read_int(f"{base}/queue/logical_block_size"),
        "physical_block": read_int(f"{base}/queue/physical_block_size"),
        "trim": (read_int(f"{base}/queue/discard_max_bytes", 0) or 0) > 0,
        "scheduler": None,
        "link": None,
        "link_max": None,
        "runtime_pm": read_sys(f"{base}/device/power/runtime_status"),
        "queue_depth": read_int(f"{base}/device/queue_depth"),
        "nr_requests": read_int(f"{base}/queue/nr_requests"),
    }
    sched = read_sys(f"{base}/queue/scheduler")
    if sched:
        active = [s for s in sched.split() if s.startswith("[")]
        d["scheduler"] = active[0].strip("[]") if active else sched
    real = os.path.realpath(base)

    if disk.kind == "nvme":
        pci = f"{base}/device/device"
        speed = read_sys(f"{pci}/current_link_speed")
        width = read_sys(f"{pci}/current_link_width")
        mspeed = read_sys(f"{pci}/max_link_speed")
        mwidth = read_sys(f"{pci}/max_link_width")
        if speed:
            d["link"] = f"PCIe {_pcie_gen(speed)} x{width}" if width else speed
        if mspeed:
            d["link_max"] = f"PCIe {_pcie_gen(mspeed)} x{mwidth}" if mwidth else mspeed
    elif disk.connection_bus == "usb":
        path = real
        for _ in range(12):
            path = os.path.dirname(path)
            speed = read_sys(os.path.join(path, "speed"))
            version = read_sys(os.path.join(path, "version"))
            if speed and version:
                d["link"] = f"USB {version.strip()} · {_usb_speed(speed)}"
                break
    else:
        for part in real.split("/"):
            if part.startswith("ata") and part[3:].isdigit():
                spd = read_sys(f"/sys/class/ata_link/link{part[3:]}/sata_spd")
                if spd and spd != "<unknown>":
                    d["link"] = f"SATA {spd}"
                break
    return d


def _pcie_gen(speed):
    gens = {"2.5": "1.0", "5.0": "2.0", "8.0": "3.0", "16.0": "4.0", "32.0": "5.0", "64.0": "6.0"}
    value = speed.split()[0]
    gen = gens.get(value)
    return f"{gen} ({value} GT/s)" if gen else speed


def _usb_speed(mbps):
    try:
        v = float(mbps)
    except ValueError:
        return mbps
    return f"{v / 1000:g} Gbps" if v >= 1000 else f"{v:g} Mbps"


def live_temperature(disk):
    """Temperature in °C from a hwmon sensor if one exists (NVMe, drivetemp)."""
    candidates = []
    if disk.kind == "nvme":
        ctrl = os.path.basename(os.path.realpath(f"/sys/block/{disk.name}/device"))
        candidates += glob.glob(f"/sys/class/nvme/{ctrl}/hwmon*/temp1_input")
        candidates += glob.glob(f"/sys/class/nvme/{ctrl}/device/hwmon/hwmon*/temp1_input")
    candidates += glob.glob(f"/sys/block/{disk.name}/device/hwmon/hwmon*/temp1_input")
    for c in candidates:
        v = read_int(c)
        if v is not None and v > 0:
            return v / 1000.0
    return None


def link_power_policy(disk):
    """SATA ALPM policy for the host the disk is attached to."""
    real = os.path.realpath(f"/sys/block/{disk.name}")
    for part in real.split("/"):
        if part.startswith("host") and part[4:].isdigit():
            return read_sys(f"/sys/class/scsi_host/{part}/link_power_management_policy")
    return None


def nvme_apst_latency():
    return read_int("/sys/module/nvme_core/parameters/default_ps_max_latency_us")
