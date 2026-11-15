"""Turn raw SMART data into a health assessment that people can act on."""

import time
from dataclasses import dataclass, field

GOOD, WARNING, CRITICAL, UNKNOWN = "good", "warning", "critical", "unknown"
INFO = "info"

STATUS_LABEL = {
    GOOD: "Good",
    WARNING: "Warning",
    CRITICAL: "Critical",
    UNKNOWN: "Unknown",
}

# Units used by UDisks for the "pretty" value of ATA attributes.
UNIT_UNKNOWN, UNIT_NONE, UNIT_MSEC, UNIT_SECTORS, UNIT_MKELVIN = range(5)

ATA_DESCRIPTIONS = {
    1: "Rate of hardware read errors. Raw values are vendor-specific; rely on the normalized value.",
    2: "Overall throughput performance of the drive.",
    3: "Time the spindle needs to spin up.",
    4: "Number of spindle start/stop cycles.",
    5: "Sectors that failed and were remapped to spare areas. Any non-zero count means the surface is degrading.",
    7: "Rate of seek errors of the magnetic heads.",
    8: "Average seek performance.",
    9: "Total time the drive has been powered on.",
    10: "Retries needed to spin up to speed. Non-zero values point to mechanical trouble.",
    11: "Retries needed to recalibrate the heads.",
    12: "Number of complete power on/off cycles.",
    167: "SSD protected mode / vendor specific.",
    168: "SATA PHY error count.",
    169: "Remaining life of the SSD (vendor specific).",
    170: "Reserved blocks available for bad block replacement.",
    171: "Program (write) failures.",
    172: "Erase failures.",
    173: "Average erase count / wear leveling.",
    174: "Unexpected power loss count.",
    175: "Power loss protection failures.",
    177: "Wear leveling count. The normalized value approximates remaining life.",
    179: "Used reserved block count.",
    180: "Unused reserved block count.",
    181: "Program failures.",
    182: "Erase failures.",
    183: "Runtime bad blocks / SATA downshifts.",
    184: "Parity errors in the data path between host and media.",
    187: "Errors that could not be corrected by ECC.",
    188: "Commands aborted because of a timeout.",
    189: "Writes done while the head was flying outside its normal range.",
    190: "Airflow temperature.",
    191: "Errors caused by shock or vibration.",
    192: "Emergency head retracts caused by power loss.",
    193: "Head load/unload cycles. Laptop drives are typically rated for 300 000 to 600 000.",
    194: "Current internal temperature.",
    195: "ECC corrections done on the fly.",
    196: "Count of remap operations.",
    197: "Unstable sectors waiting to be remapped. These may cause read errors.",
    198: "Sectors that could not be read during offline scans.",
    199: "Transfer errors between drive and host, usually caused by a bad cable or connector.",
    200: "Errors found when writing a sector.",
    202: "Remaining rated life of the SSD (percent).",
    206: "Flying height of the heads.",
    220: "Disk shift relative to the spindle.",
    222: "Time spent with data loaded.",
    223: "Load/unload retries.",
    225: "Total host writes.",
    226: "Load-in time.",
    230: "Head amplitude / drive life protection status.",
    231: "Remaining SSD life (percent).",
    232: "Available reserved space.",
    233: "Media wearout indicator. The normalized value approximates remaining life.",
    234: "Average/maximum erase count.",
    235: "Good block count / power fail backup health.",
    240: "Time spent positioning the heads.",
    241: "Total logical blocks written.",
    242: "Total logical blocks read.",
    254: "Free-fall protection events.",
}

SSD_LIFE_IDS = (231, 202, 233, 177, 169)


@dataclass
class Issue:
    severity: str
    title: str
    detail: str


@dataclass
class HealthReport:
    status: str = UNKNOWN
    score: int | None = None
    summary: str = "No SMART data available"
    issues: list = field(default_factory=list)
    life_remaining: int | None = None
    temperature: float | None = None
    temp_warning: float | None = None
    temp_critical: float | None = None
    power_on_hours: float | None = None
    power_cycles: int | None = None
    data_read: int | None = None
    data_written: int | None = None
    busy_hours: float | None = None
    unsafe_shutdowns: int | None = None
    selftest_status: str = ""
    selftest_remaining: int = -1
    smart_updated: int | None = None
    indicators: list = field(default_factory=list)  # (label, value, severity, description)


def assess(disk):
    if disk.has_nvme and isinstance(disk.smart_attrs, dict):
        report = _assess_nvme(disk)
    elif disk.has_ata and disk.smart_attrs:
        report = _assess_ata(disk)
    else:
        report = HealthReport()
        if disk.has_ata and not disk.ata_props.get("SmartSupported", True):
            report.summary = "This drive does not support SMART"
        elif disk.connection_bus == "usb":
            report.summary = "SMART is not available through this USB adapter"
        elif disk.smart_error:
            report.summary = disk.smart_error
        return report

    _finalize(report)
    return report


def _finalize(r):
    severities = {i.severity for i in r.issues}
    if CRITICAL in severities:
        r.status = CRITICAL
    elif WARNING in severities:
        r.status = WARNING
    else:
        r.status = GOOD
    if r.score is not None:
        r.score = max(0, min(100, round(r.score)))
    problems = [i for i in r.issues if i.severity in (CRITICAL, WARNING)]
    if r.status == CRITICAL:
        r.summary = problems[0].title + ". Back up your data now."
    elif r.status == WARNING:
        r.summary = problems[0].title + ". Keep backups current and monitor this drive."
    else:
        r.summary = "No problems detected."
    order = {CRITICAL: 0, WARNING: 1, INFO: 2, GOOD: 3}
    r.issues.sort(key=lambda i: order.get(i.severity, 9))


# ------------------------------------------------------------------- NVMe

def _assess_nvme(disk):
    a = disk.smart_attrs
    p = disk.nvme_props
    r = HealthReport()
    r.smart_updated = p.get("SmartUpdated")
    r.selftest_status = p.get("SmartSelftestStatus", "")
    r.selftest_remaining = p.get("SmartSelftestPercentRemaining", -1)

    used = a.get("percent_used")
    spare = a.get("avail_spare")
    spare_thresh = a.get("spare_thresh")
    media_errors = a.get("media_errors", 0)
    err_log = a.get("num_err_log_entries", 0)
    temp_k = p.get("SmartTemperature") or 0
    wctemp = a.get("wctemp") or 0
    cctemp = a.get("cctemp") or 0

    r.temperature = temp_k - 273.15 if temp_k else None
    r.temp_warning = wctemp - 273.15 if wctemp else 70.0
    r.temp_critical = cctemp - 273.15 if cctemp else 80.0
    r.power_on_hours = p.get("SmartPowerOnHours")
    r.power_cycles = a.get("power_cycles")
    r.data_read = a.get("total_data_read")
    r.data_written = a.get("total_data_written")
    r.busy_hours = a.get("ctrl_busy_time", 0) / 60 if "ctrl_busy_time" in a else None
    r.unsafe_shutdowns = a.get("unsafe_shutdowns")

    score = 100.0
    if used is not None:
        r.life_remaining = max(0, 100 - used)
        score = r.life_remaining
        if r.life_remaining <= 10:
            r.issues.append(Issue(CRITICAL, "Rated endurance almost exhausted",
                                  f"The drive reports {used}% of its rated write endurance used."))
        elif r.life_remaining <= 25:
            r.issues.append(Issue(WARNING, "Rated endurance running low",
                                  f"The drive reports {used}% of its rated write endurance used."))
        else:
            r.issues.append(Issue(INFO, f"{r.life_remaining}% of rated endurance remaining",
                                  f"{used}% of the manufacturer's write endurance rating has been used. "
                                  "Drives often keep working well past 100%, but wear-related errors become more likely."))

    warnings = p.get("SmartCriticalWarning") or []
    labels = {
        "spare": "Available spare below threshold",
        "temperature": "Temperature outside safe range",
        "degraded": "Reliability degraded by media or internal errors",
        "readonly": "Media placed in read-only mode",
        "volatile_mem": "Volatile memory backup failed",
        "pmr_readonly": "Persistent memory region is read-only",
    }
    for w in warnings:
        r.issues.append(Issue(CRITICAL, labels.get(w, f"Critical warning: {w}"),
                              "The controller raised a critical warning flag."))
        score = min(score, 10)

    if spare is not None and spare_thresh is not None:
        if spare <= spare_thresh:
            r.issues.append(Issue(CRITICAL, "Spare blocks exhausted",
                                  f"Available spare is {spare}%, at or below the {spare_thresh}% threshold."))
            score = min(score, 10)
        elif spare < 50:
            r.issues.append(Issue(WARNING, "Spare blocks running low",
                                  f"Only {spare}% of the spare area is left."))
            score -= 15

    if media_errors:
        r.issues.append(Issue(WARNING, f"{media_errors} media errors",
                              "The controller detected unrecovered data integrity errors."))
        score -= min(30, 5 + media_errors)

    if r.temperature is not None:
        if r.temperature >= r.temp_critical:
            r.issues.append(Issue(CRITICAL, "Temperature critical",
                                  f"{r.temperature:.0f} °C is above the critical limit of {r.temp_critical:.0f} °C."))
        elif r.temperature >= r.temp_warning:
            r.issues.append(Issue(WARNING, "Running hot",
                                  f"{r.temperature:.0f} °C is above the warning limit of {r.temp_warning:.0f} °C."))
    if a.get("critical_temp_time"):
        r.issues.append(Issue(INFO, "Has run at critical temperature",
                              f"{a['critical_temp_time']} minutes spent above the critical temperature."))
    elif a.get("warning_temp_time"):
        r.issues.append(Issue(INFO, "Has run hot before",
                              f"{a['warning_temp_time']} minutes spent above the warning temperature."))

    if r.selftest_status in ("fatal", "error", "aborted_unknown_test_failure", "failed"):
        r.issues.append(Issue(CRITICAL, "Last self-test failed", f"Self-test status: {r.selftest_status}."))
        score = min(score, 20)

    r.score = score
    r.indicators = [
        ("Endurance used", f"{used}%" if used is not None else "—",
         _sev_range(used, 75, 90), "Share of the rated write endurance consumed"),
        ("Available spare", f"{spare}%" if spare is not None else "—",
         CRITICAL if spare is not None and spare_thresh is not None and spare <= spare_thresh else GOOD,
         f"Threshold {spare_thresh}%" if spare_thresh is not None else ""),
        ("Media errors", f"{media_errors}", WARNING if media_errors else GOOD,
         "Unrecovered data integrity errors"),
        ("Critical warnings", ", ".join(warnings) if warnings else "None",
         CRITICAL if warnings else GOOD, "Controller critical warning flags"),
        ("Error log entries", f"{err_log}", INFO if err_log else GOOD,
         "Entries in the controller error log (often harmless)"),
        ("Unsafe shutdowns", f"{a.get('unsafe_shutdowns', 0)}", INFO,
         "Power lost without a clean shutdown"),
    ]
    return r


# -------------------------------------------------------------------- ATA

def _attr_map(attrs):
    out = {}
    for t in attrs:
        aid, name, flags, value, worst, thresh, pretty, unit, _exp = t
        out[aid] = dict(id=aid, name=name, flags=flags, value=value, worst=worst,
                        threshold=thresh, pretty=pretty, unit=unit)
    return out


def attribute_rows(disk):
    """Rows for the SMART attribute table: dicts with formatted fields and a status."""
    rows = []
    for a in _attr_map(disk.smart_attrs or []).values():
        failing_now = a["threshold"] > 0 and 0 < a["value"] <= a["threshold"]
        failed_past = a["threshold"] > 0 and 0 < a["worst"] <= a["threshold"]
        prefail = bool(a["flags"] & 0x0001)
        status = GOOD
        if failing_now:
            status = CRITICAL if prefail else WARNING
        elif failed_past:
            status = WARNING
        elif a["id"] in (5, 197, 198, 10, 187, 196) and a["pretty"] > 0:
            status = WARNING
        rows.append(dict(
            id=a["id"],
            name=humanize(a["name"]),
            description=ATA_DESCRIPTIONS.get(a["id"], ""),
            value=a["value"], worst=a["worst"], threshold=a["threshold"],
            raw=format_pretty(a["pretty"], a["unit"]),
            prefail=prefail, status=status))
    return rows


def humanize(name):
    if not name:
        return "Unknown"
    words = name.replace("-", " ").split()
    fixes = {"udma": "UDMA", "crc": "CRC", "ecc": "ECC", "ssd": "SSD", "celsius": "°C",
             "lba": "LBA", "nand": "NAND", "g": "G"}
    out = [fixes.get(w, w) for w in words]
    return (out[0][:1].upper() + out[0][1:] + (" " + " ".join(out[1:]) if len(out) > 1 else ""))


def format_pretty(pretty, unit):
    if unit == UNIT_MSEC:
        hours = pretty / 3_600_000
        if hours >= 48:
            return f"{hours / 24:,.0f} days"
        if hours >= 1:
            return f"{hours:,.1f} h"
        return f"{pretty / 1000:,.1f} s"
    if unit == UNIT_SECTORS:
        return f"{pretty:,} sectors"
    if unit == UNIT_MKELVIN:
        return f"{pretty / 1000 - 273.15:.0f} °C"
    return f"{pretty:,}"


def _assess_ata(disk):
    p = disk.ata_props
    attrs = _attr_map(disk.smart_attrs)
    r = HealthReport()
    r.smart_updated = p.get("SmartUpdated")
    r.selftest_status = p.get("SmartSelftestStatus", "")
    r.selftest_remaining = p.get("SmartSelftestPercentRemaining", -1)
    temp_k = p.get("SmartTemperature") or 0
    r.temperature = temp_k - 273.15 if temp_k else None
    hdd = disk.kind == "hdd"
    r.temp_warning = 55.0 if hdd else 70.0
    r.temp_critical = 65.0 if hdd else 80.0
    if p.get("SmartPowerOnSeconds"):
        r.power_on_hours = p["SmartPowerOnSeconds"] / 3600

    def raw(aid):
        return attrs[aid]["pretty"] if aid in attrs else None

    r.power_cycles = raw(12)
    # Attributes 241/242 are usually counted in 512-byte LBAs on SSDs. Hard disk
    # vendors pack other data into the raw value, so they are only trusted on
    # SSDs, and only when the result is plausible (under 3000 drive fills).
    if not hdd:
        for aid, attr in ((241, "data_written"), (242, "data_read")):
            if raw(aid):
                total = raw(aid) * 512
                if total < disk.size * 3000:
                    setattr(r, attr, total)
    r.unsafe_shutdowns = raw(192) if hdd else raw(174)

    score = 100.0

    if p.get("SmartFailing"):
        r.issues.append(Issue(CRITICAL, "Drive predicts its own failure",
                              "The drive's SMART self-assessment reports that failure is imminent."))
        score = min(score, 5)

    failing = [a for a in attrs.values() if a["threshold"] > 0 and 0 < a["value"] <= a["threshold"]]
    for a in failing:
        sev = CRITICAL if a["flags"] & 1 else WARNING
        r.issues.append(Issue(sev, f"{humanize(a['name'])} below threshold",
                              f"Normalized value {a['value']} is at or below the threshold {a['threshold']}."))
        score = min(score, 15 if sev == CRITICAL else 50)

    past = p.get("SmartNumAttributesFailedInThePast", 0)
    if past and not failing:
        r.issues.append(Issue(WARNING, f"{past} attribute(s) failed in the past",
                              "At some point an attribute crossed its failure threshold."))
        score -= 10

    realloc = raw(5)
    if realloc:
        r.issues.append(Issue(WARNING, f"{realloc:,} reallocated sectors",
                              "Bad sectors were found and replaced with spares. A growing count is an "
                              "early sign of surface or head degradation."))
        score -= min(50, 10 + realloc * 0.1)
    pending = raw(197)
    if pending:
        r.issues.append(Issue(WARNING if pending < 100 else CRITICAL, f"{pending:,} pending sectors",
                              "Unstable sectors that could not be read reliably. Data in them may be lost."))
        score -= min(45, 10 + pending * 0.5)
    offline = raw(198)
    if offline:
        r.issues.append(Issue(WARNING, f"{offline:,} offline uncorrectable sectors",
                              "Sectors that could not be read during the drive's background scan."))
        score -= min(40, 10 + offline)
    uncorrect = raw(187)
    if uncorrect:
        r.issues.append(Issue(WARNING, f"{uncorrect:,} uncorrectable errors reported",
                              "Read errors that ECC could not correct."))
        score -= min(25, uncorrect * 2)
    spin_retry = raw(10)
    if spin_retry:
        r.issues.append(Issue(WARNING, "Spin-up retries detected",
                              "The motor needed several attempts to reach speed."))
        score -= 15
    crc = raw(199)
    if crc:
        r.issues.append(Issue(INFO, f"{crc:,} interface CRC errors",
                              "Transfer errors between the drive and the computer. Usually a cable or "
                              "connector issue, not the drive itself."))
        score -= min(5, crc * 0.05)

    if disk.kind != "hdd":
        for aid in SSD_LIFE_IDS:
            if aid in attrs and 0 < attrs[aid]["value"] <= 100:
                r.life_remaining = attrs[aid]["value"]
                break
        if r.life_remaining is not None:
            score = min(score, r.life_remaining)
            if r.life_remaining <= 10:
                r.issues.append(Issue(CRITICAL, "SSD life almost exhausted",
                                      f"{r.life_remaining}% of rated life remaining."))
            elif r.life_remaining <= 25:
                r.issues.append(Issue(WARNING, "SSD life running low",
                                      f"{r.life_remaining}% of rated life remaining."))
            else:
                r.issues.append(Issue(INFO, f"{r.life_remaining}% of rated life remaining",
                                      "Based on the drive's wear indicator."))

    if hdd and raw(193):
        lcc = raw(193)
        if lcc > 600_000:
            r.issues.append(Issue(WARNING, "Very high head load cycle count",
                                  f"{lcc:,} cycles; laptop drives are usually rated for 300 000 to 600 000."))
            score -= 10
        elif lcc > 300_000:
            r.issues.append(Issue(INFO, "High head load cycle count",
                                  f"{lcc:,} load cycles. Aggressive power saving parks the heads often."))

    if r.temperature is not None:
        if r.temperature >= r.temp_critical:
            r.issues.append(Issue(CRITICAL, "Temperature critical",
                                  f"{r.temperature:.0f} °C. Sustained heat shortens drive life."))
        elif r.temperature >= r.temp_warning:
            r.issues.append(Issue(WARNING, "Running hot", f"{r.temperature:.0f} °C."))

    st = r.selftest_status
    if st in ("fatal", "error_unknown", "error_electrical", "error_servo", "error_read", "error_handling"):
        r.issues.append(Issue(CRITICAL, "Last self-test failed", f"Result: {st.replace('_', ' ')}."))
        score = min(score, 20)

    r.score = score

    def sev_nonzero(v, warn=WARNING):
        return GOOD if not v else warn

    r.indicators = [
        ("SMART self-assessment", "Failing" if p.get("SmartFailing") else "Passed",
         CRITICAL if p.get("SmartFailing") else GOOD, "Drive's own overall verdict"),
        ("Reallocated sectors", f"{realloc or 0:,}", sev_nonzero(realloc), "Bad sectors replaced with spares"),
        ("Pending sectors", f"{pending or 0:,}", sev_nonzero(pending), "Unstable sectors awaiting remap"),
        ("Uncorrectable sectors", f"{offline or 0:,}", sev_nonzero(offline), "Found during offline scan"),
        ("Reported uncorrectable", f"{uncorrect or 0:,}", sev_nonzero(uncorrect), "Errors ECC could not fix"),
        ("Interface CRC errors", f"{crc or 0:,}", sev_nonzero(crc, INFO), "Cable or connector problems"),
    ]
    if hdd:
        r.indicators.append(("Head load cycles", f"{raw(193) or 0:,}",
                             INFO if (raw(193) or 0) > 300_000 else GOOD, "Head park/unpark count"))
    return r


def _sev_range(v, warn, crit):
    if v is None:
        return UNKNOWN
    if v >= crit:
        return CRITICAL
    if v >= warn:
        return WARNING
    return GOOD


def selftest_label(status, remaining):
    labels = {
        "success": "Completed without error",
        "aborted": "Aborted by host",
        "interrupted": "Interrupted",
        "fatal": "Failed (fatal error)",
        "error_unknown": "Failed (unknown error)",
        "error_electrical": "Failed (electrical)",
        "error_servo": "Failed (servo/seek)",
        "error_read": "Failed (read error)",
        "error_handling": "Failed (handling damage)",
        "inprogress": "In progress",
        "": "Never run",
    }
    if status == "inprogress" and remaining >= 0:
        return f"In progress, {100 - remaining}% done"
    return labels.get(status, status.replace("_", " ").capitalize())


def ago(timestamp):
    if not timestamp:
        return "never"
    d = time.time() - timestamp
    if d < 90:
        return "just now"
    if d < 3600:
        return f"{d / 60:.0f} min ago"
    if d < 86400 * 2:
        return f"{d / 3600:.0f} h ago"
    return f"{d / 86400:.0f} days ago"
