# Diskwise

**Know your drives.** A GTK4 / libadwaita app for Fedora that shows the health, speed and power use of
every drive, and lets you format, partition and control them safely.

<img src="data/icons/hicolor/scalable/apps/io.github.diskwise.Diskwise.svg" width="96" alt="Diskwise logo">

## Pages

| Page | Contents |
|---|---|
| **Overview** | Health score ring and plain-language verdict, temperature, power-on time, data written, estimated power draw, findings, partition map with usage, device details |
| **Health** | Status, temperature against the drive's limits, key failure indicators, self-test (short / extended / abort), full SMART attribute table or NVMe health log |
| **Performance** | Live read/write chart, IOPS, busy %, latency. Speed tests: read & write on a temporary file, and a read-only surface scan of the whole disk |
| **Power** | Power state, estimated draw, session and lifetime energy, power profile, NVMe power states, APM / ALPM / APST settings, share of battery draw |
| **Manage** | Mount / unmount, unlock / lock encrypted volumes, rename, check and repair filesystems, format partitions, delete partitions, create partitions in free space, format the whole disk (GPT or MBR, ext4 / Btrfs / XFS / exFAT / NTFS / FAT32, optional LUKS encryption, optional zero fill), power off, eject, spin down, and persistent standby / APM / write-cache settings |

## Safety

* The disk holding the running system (`/`, `/boot`, `/boot/efi`, `/home`, `/usr`, `/var`, or active swap,
  including through LUKS or LVM) cannot be formatted. Its system partitions cannot be formatted, deleted,
  unmounted or locked. Other partitions on that disk stay manageable.
* Every destructive action lists what will be lost and requires typing the device name (for example `sda`).
* Filesystems are unmounted and encrypted volumes locked before formatting.
* Operations go through UDisks2, so polkit asks for your password where the system requires it.

## Run from source

```bash
sudo dnf install python3-gobject gtk4 libadwaita udisks2 smartmontools nvme-cli \
                 exfatprogs ntfsprogs dosfstools xfsprogs btrfs-progs cryptsetup
./diskwise-run
```

## Install

```bash
./install.sh                 # current user (~/.local), appears in the app grid
sudo ./install.sh /usr       # system-wide, also installs the helper and polkit policy
./install.sh --uninstall     # remove the user install
```

An RPM spec is included (`diskwise.spec`).

## Privileges

Monitoring works as a normal user: SMART data comes from UDisks2 and live statistics from sysfs. The
following ask for authorization through polkit:

* Surface read test: opens the raw disk read-only through UDisks (`OpenForBenchmark`)
* NVMe power state table: runs `diskwise/helper.py` through `pkexec` (`smartctl -c`, `nvme get-feature` only)
* Management actions on internal disks (removable drives usually need no password)

## How the numbers are made

* **Health score**: hard disks start at 100, with deductions for reallocated, pending and uncorrectable
  sectors, uncorrectable errors and spin retries. SSDs start from the drive's own wear indicator. The drive's
  own failure prediction, failing attributes or a failed self-test mark it critical. Every deduction is
  listed as a finding.
* **Power**: drives don't report watts. The estimate is `idle + (active − idle) × utilization`, or the standby
  value when the drive is spun down. Values come from the drive's NVMe power table once read, otherwise from
  typical figures for its class.
* **Speed test**: uses `O_DIRECT` to bypass the page cache, falling back to fsync plus cache dropping where the
  filesystem doesn't support it.

## Layout

```
diskwise-run              launcher (run from source)
diskwise/
  app.py, window.py       application and adaptive main window
  context.py              per-disk state
  helper.py               privileged helper (pkexec)
  backend/                udisks, devices, health, iostats, power, benchmark, manage
  ui/                     pages, dialogs, cairo charts, widgets
data/                     desktop file, metainfo, polkit policy, icons
```
