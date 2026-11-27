"""Disk power estimation.

Drives do not report how many watts they draw. We combine the drive's power
state (where readable) and its live utilization with a power profile: either
the drive's own NVMe power state table (read with admin rights) or typical
values for its class of device.
"""

import json
import os
import re
import subprocess
import sys
from collections import deque
from dataclasses import dataclass

from gi.repository import GLib

from . import udisks as ud
from .devices import nvme_apst_latency, link_power_policy, read_int, read_sys
from .iostats import HISTORY


@dataclass
class PowerProfile:
    active_w: float
    idle_w: float
    standby_w: float
    source: str
    measured: bool = False   # True when taken from the drive's own table


@dataclass
class NvmePowerState:
    index: int
    operational: bool
    max_w: float
    entry_lat_us: int
    exit_lat_us: int


@dataclass
class PowerState:
    label: str          # Active / Idle / Standby / Sleeping
    detail: str = ""
    standby: bool = False


TYPICAL = {
    "nvme": PowerProfile(4.5, 0.6, 0.01, "Typical values for a consumer NVMe SSD"),
    "nvme-apst": PowerProfile(4.5, 0.08, 0.01, "Typical values for a consumer NVMe SSD with APST power saving"),
    "ssd": PowerProfile(2.8, 0.35, 0.05, "Typical values for a 2.5″ SATA SSD"),
    "hdd-2.5": PowerProfile(1.8, 0.75, 0.15, "Typical values for a 2.5″ 5400 rpm hard disk"),
    "hdd-3.5": PowerProfile(7.0, 4.5, 0.8, "Typical values for a 3.5″ 7200 rpm hard disk"),
    "usb-flash": PowerProfile(0.5, 0.1, 0.05, "Typical values for a USB flash drive"),
    "unknown": PowerProfile(2.5, 0.5, 0.1, "Generic estimate"),
}


def profile_for(disk, nvme_states=None):
    if nvme_states:
        ops = [s for s in nvme_states if s.operational]
        nonops = [s for s in nvme_states if not s.operational]
        active = max((s.max_w for s in ops), default=4.5)
        if nonops and (nvme_apst_latency() or 0) > 0:
            idle = min(s.max_w for s in nonops)
        else:
            idle = min((s.max_w for s in ops), default=0.6) * 0.3
        standby = min((s.max_w for s in nvme_states), default=0.01)
        return PowerProfile(active, idle, standby, "From the drive's power state table", True)
    kind = disk.kind
    if kind == "nvme":
        return TYPICAL["nvme-apst" if (nvme_apst_latency() or 0) > 0 else "nvme"]
    if kind == "hdd":
        return TYPICAL["hdd-2.5" if disk.form_factor_hint == "2.5" else "hdd-3.5"]
    return TYPICAL.get(kind, TYPICAL["unknown"])


class PowerTracker:
    """Integrates estimated power over time for one disk."""

    def __init__(self, disk):
        self.disk = disk
        self.nvme_states = None
        self.nvme_current_ps = None
        self.profile = profile_for(disk)
        self.history = deque([None] * HISTORY, maxlen=HISTORY)
        self.energy_wh = 0.0
        self.state = PowerState("Unknown")
        self._last_pm_check = 0
        self.peak_w = 0.0

    def set_nvme_states(self, states, current):
        self.nvme_states = states
        self.nvme_current_ps = current
        self.profile = profile_for(self.disk, states)

    def update(self, client, sample, tick):
        """Called once per second with the latest IOSample."""
        if self.disk.has_ata and tick - self._last_pm_check >= 5:
            self._last_pm_check = tick
            self.state = self._ata_state(client)
        elif not self.disk.has_ata:
            self.state = PowerState("Active" if sample.util > 0.01 else "Idle",
                                    "Handling I/O" if sample.util > 0.01 else "No I/O in the last second")
        if self.disk.has_ata and not self.state.standby and self.state.label != "Unknown":
            busy = sample.util > 0.01
            self.state = PowerState("Active" if busy else "Idle", self.state.detail)
        if self.state.standby:
            watts = self.profile.standby_w
        else:
            watts = self.profile.idle_w + (self.profile.active_w - self.profile.idle_w) * sample.util
        self.history.append(watts)
        self.peak_w = max(self.peak_w, watts)
        self.energy_wh += watts / 3600.0
        return watts

    def _ata_state(self, client):
        try:
            value = client.call(self.disk.drive_path, ud.IFACE_ATA, "PmGetState", timeout=3000)[0]
        except GLib.Error:
            return PowerState("Unknown", "Power state not readable")
        if value == 0x00:
            return PowerState("Standby", "Spun down", standby=True)
        if value == 0x80:
            return PowerState("Idle", "Spinning, heads idle")
        if value in (0x40, 0x41):
            return PowerState("Idle", "NV cache power mode")
        return PowerState("Active", "Spinning, ready")

    @property
    def current_w(self):
        return self.history[-1] or 0.0

    def lifetime_kwh(self, report):
        """Rough lifetime energy: powered-on time at idle plus busy time at active power."""
        if not report or not report.power_on_hours:
            return None
        hours = report.power_on_hours
        busy = report.busy_hours or 0
        busy = min(busy, hours)
        p = self.profile
        idle_w = p.idle_w if self.disk.kind == "hdd" else max(p.idle_w, 0.3)
        return ((hours - busy) * idle_w + busy * p.active_w) / 1000


# ------------------------------------------------------------ system context

def battery_draw_w():
    """System power draw from the battery while discharging, in watts."""
    base = "/sys/class/power_supply"
    try:
        supplies = os.listdir(base)
    except OSError:
        return None
    for s in supplies:
        if read_sys(f"{base}/{s}/type") != "Battery":
            continue
        if read_sys(f"{base}/{s}/status") != "Discharging":
            return None
        p = read_int(f"{base}/{s}/power_now")
        if p:
            return p / 1e6
        i, v = read_int(f"{base}/{s}/current_now"), read_int(f"{base}/{s}/voltage_now")
        if i and v:
            return i * v / 1e12
    return None


def power_management(disk):
    """List of (label, value, description) describing power-saving configuration."""
    rows = []
    if disk.kind == "nvme":
        lat = nvme_apst_latency()
        if lat is not None:
            rows.append(("Autonomous power state transitions (APST)",
                         "Enabled" if lat > 0 else "Disabled",
                         f"Drive may enter states with up to {lat / 1000:g} ms exit latency" if lat > 0
                         else "The drive stays in an operational power state"))
    if disk.has_ata:
        a = disk.ata_props
        if a.get("ApmSupported"):
            rows.append(("Advanced power management (APM)",
                         "Enabled" if a.get("ApmEnabled") else "Disabled",
                         "Lets the drive lower its speed or park heads when idle"))
        if a.get("PmSupported"):
            rows.append(("Standby support", "Enabled" if a.get("PmEnabled") else "Disabled",
                         "Drive can spin down / enter standby"))
        if a.get("WriteCacheSupported"):
            rows.append(("Write cache", "Enabled" if a.get("WriteCacheEnabled") else "Disabled",
                         "Faster writes; data in cache can be lost on power failure"))
        policy = link_power_policy(disk)
        if policy:
            nice = {
                "max_performance": "Max performance",
                "medium_power": "Medium power",
                "med_power_with_dipm": "Medium power with DIPM",
                "min_power": "Minimum power",
                "keep_firmware_settings": "Firmware default",
            }.get(policy, policy)
            rows.append(("SATA link power management", nice,
                         "How aggressively the SATA link is put to sleep"))
    rpm = read_sys(f"/sys/block/{disk.name}/device/power/control")
    status = read_sys(f"/sys/block/{disk.name}/device/power/runtime_status")
    if rpm:
        rows.append(("Runtime power management", "Automatic" if rpm == "auto" else "Always on",
                     f"Kernel runtime state: {status}" if status else ""))
    return rows


# --------------------------------------------------------- privileged helper

HELPER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "helper.py")
SYSTEM_HELPER = "/usr/libexec/diskwise-helper"  # installed by the RPM with a polkit policy


def read_nvme_power_states(device):
    """Run the privileged helper through pkexec. Returns (states, current_ps) or raises."""
    controller = re.sub(r"n\d+$", "", device)
    if os.access(SYSTEM_HELPER, os.X_OK):
        argv = ["pkexec", SYSTEM_HELPER]
    else:
        argv = ["pkexec", sys.executable, "-I", HELPER]
    proc = subprocess.run(
        argv + ["nvme-power", controller],
        capture_output=True, text=True, timeout=120)
    if proc.returncode in (126, 127):
        raise PermissionError("Authorization was cancelled")
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"helper exited with {proc.returncode}")
    data = json.loads(proc.stdout)
    states = [NvmePowerState(**s) for s in data.get("states", [])]
    return states, data.get("current")
