#!/usr/bin/python3
"""Privileged helper for Diskwise, run through pkexec.

It only runs fixed, read-only queries (smartctl / nvme-cli) against a device
path that is strictly validated, and prints JSON on stdout.
"""

import json
import re
import shutil
import subprocess
import sys

ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}
PS_LINE = re.compile(
    r"^\s*(\d+)\s+([+-])\s+([\d.]+)W\s+\S+\s+\S+\s+\d+\s+\d+\s+\d+\s+\d+\s+(\d+)\s+(\d+)\s*$")


def run(argv):
    return subprocess.run(argv, capture_output=True, text=True, env=ENV, timeout=60)


def nvme_power(controller):
    if not re.fullmatch(r"/dev/nvme\d{1,3}", controller):
        sys.exit("invalid device")
    smartctl = shutil.which("smartctl", path=ENV["PATH"])
    if not smartctl:
        sys.exit("smartctl (smartmontools) is not installed")
    out = run([smartctl, "-c", controller]).stdout
    states = []
    for line in out.splitlines():
        m = PS_LINE.match(line)
        if m:
            states.append(dict(index=int(m.group(1)), operational=m.group(2) == "+",
                               max_w=float(m.group(3)), entry_lat_us=int(m.group(4)),
                               exit_lat_us=int(m.group(5))))
    current = None
    nvme = shutil.which("nvme", path=ENV["PATH"])
    if nvme:
        feat = run([nvme, "get-feature", controller, "--feature-id=2", "-H"]).stdout
        m = re.search(r"Power State\s*\(PS\):\s*(\d+)", feat)
        if m:
            current = int(m.group(1))
        else:
            m = re.search(r"Current value:\s*(?:0x)?([0-9a-fA-F]+)", feat)
            if m:
                current = int(m.group(1), 16) & 0x1F
    print(json.dumps({"states": states, "current": current}))


def main():
    if len(sys.argv) != 3 or sys.argv[1] != "nvme-power":
        sys.exit("usage: helper.py nvme-power /dev/nvmeN")
    nvme_power(sys.argv[2])


if __name__ == "__main__":
    main()
