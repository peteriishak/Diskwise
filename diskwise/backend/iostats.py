"""Live I/O statistics sampled from /sys/block/<dev>/stat."""

import time
from collections import deque
from dataclasses import dataclass

SECTOR = 512
HISTORY = 120  # samples (one per second)


@dataclass
class IOSample:
    read_bps: float = 0.0
    write_bps: float = 0.0
    read_iops: float = 0.0
    write_iops: float = 0.0
    util: float = 0.0          # fraction of time the device was busy (0..1)
    latency_ms: float = 0.0    # average completion time per request
    in_flight: int = 0


def _read_stat(name):
    try:
        with open(f"/sys/block/{name}/stat") as f:
            return [int(x) for x in f.read().split()]
    except (OSError, ValueError):
        return None


class IOTracker:
    """Keeps a rolling history of throughput for one block device."""

    def __init__(self, name):
        self.name = name
        self.prev = _read_stat(name)
        self.prev_t = time.monotonic()
        self.samples = deque([None] * HISTORY, maxlen=HISTORY)
        self.total_read = 0
        self.total_written = 0
        self.peak_read = 0.0
        self.peak_write = 0.0

    @property
    def latest(self):
        return self.samples[-1] or IOSample()

    def sample(self):
        cur = _read_stat(self.name)
        now = time.monotonic()
        if cur is None or self.prev is None or len(cur) < 11:
            self.prev, self.prev_t = cur, now
            self.samples.append(IOSample())
            return self.samples[-1]
        dt = max(now - self.prev_t, 1e-3)
        d = [c - p for c, p in zip(cur, self.prev)]
        r_ios, _, r_sec, r_ticks, w_ios, _, w_sec, w_ticks = d[:8]
        io_ticks = d[9]
        ios = r_ios + w_ios
        s = IOSample(
            read_bps=r_sec * SECTOR / dt,
            write_bps=w_sec * SECTOR / dt,
            read_iops=r_ios / dt,
            write_iops=w_ios / dt,
            util=min(1.0, io_ticks / (dt * 1000)),
            latency_ms=(r_ticks + w_ticks) / ios if ios else 0.0,
            in_flight=cur[8],
        )
        self.total_read += r_sec * SECTOR
        self.total_written += w_sec * SECTOR
        self.peak_read = max(self.peak_read, s.read_bps)
        self.peak_write = max(self.peak_write, s.write_bps)
        self.prev, self.prev_t = cur, now
        self.samples.append(s)
        return s

    def series(self, attr):
        return [getattr(s, attr) if s is not None else None for s in self.samples]

    def lifetime_counters(self):
        """Bytes read/written since boot."""
        cur = _read_stat(self.name)
        if not cur:
            return None, None
        return cur[2] * SECTOR, cur[6] * SECTOR
