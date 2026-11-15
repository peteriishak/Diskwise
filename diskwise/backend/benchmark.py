"""Disk speed benchmarks.

* File benchmark: sequential and random read/write on a temporary file in a
  writable folder on the disk. Uses O_DIRECT to bypass the page cache where the
  filesystem supports it. Needs no privileges.
* Surface read benchmark: read-only sampling across the whole raw device,
  opened through UDisks (asks for authorization). Never writes.
"""

import errno
import json
import mmap
import os
import random
import threading
import time
from dataclasses import asdict, dataclass, field

from gi.repository import GLib

MiB = 1024 * 1024
SEQ_BLOCK = 8 * MiB
RND_BLOCK = 4096
RND_SECONDS = 3.0


class Cancelled(Exception):
    pass


@dataclass
class FileResult:
    kind: str = "file"
    timestamp: float = 0.0
    folder: str = ""
    size_bytes: int = 0
    direct_io: bool = True
    seq_read: float = 0.0       # bytes/s
    seq_write: float = 0.0      # bytes/s
    rnd_read_iops: float = 0.0
    rnd_read_lat_ms: float = 0.0
    rnd_read_mt_iops: float = 0.0
    rnd_write_iops: float = 0.0
    rnd_write_lat_ms: float = 0.0


@dataclass
class SurfaceResult:
    kind: str = "surface"
    timestamp: float = 0.0
    positions: list = field(default_factory=list)   # [(percent, bytes/s)]
    avg: float = 0.0
    min: float = 0.0
    max: float = 0.0
    access_ms: float = 0.0


def _aligned_buffer(size, fill=True):
    buf = mmap.mmap(-1, size)
    if fill:
        chunk = os.urandom(MiB)
        for off in range(0, size, MiB):
            buf[off:off + MiB] = chunk[: min(MiB, size - off)]
    return buf


class _Runner:
    def __init__(self, progress, done):
        self._progress = progress
        self._done = done
        self.cancel_event = threading.Event()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._main, daemon=True)
        self.thread.start()

    def cancel(self):
        self.cancel_event.set()

    def report(self, fraction, text):
        GLib.idle_add(self._progress, fraction, text)

    def check(self):
        if self.cancel_event.is_set():
            raise Cancelled()

    def _main(self):
        try:
            result = self.run()
            GLib.idle_add(self._done, result, None)
        except Cancelled:
            GLib.idle_add(self._done, None, "Cancelled")
        except GLib.Error as err:
            msg = err.message
            if "dismissed" in msg or "NotAuthorized" in msg or "not authorized" in msg.lower():
                msg = "Authorization was not granted"
            GLib.idle_add(self._done, None, msg)
        except Exception as err:  # report anything to the UI instead of dying silently
            GLib.idle_add(self._done, None, str(err) or err.__class__.__name__)


class FileBenchmark(_Runner):
    def __init__(self, folder, size_bytes, progress, done):
        super().__init__(progress, done)
        self.folder = folder
        self.size = max(SEQ_BLOCK * 4, size_bytes // SEQ_BLOCK * SEQ_BLOCK)

    def run(self):
        st = os.statvfs(self.folder)
        if st.f_bavail * st.f_frsize < self.size * 1.5:
            raise RuntimeError("Not enough free space in the selected folder")
        res = FileResult(timestamp=time.time(), folder=self.folder, size_bytes=self.size)
        fd_tmp, path = None, os.path.join(self.folder, f".diskwise-bench-{os.getpid()}")
        try:
            flags = os.O_RDWR | os.O_CREAT | os.O_TRUNC
            try:
                fd_tmp = os.open(path, flags | os.O_DIRECT, 0o600)
                # Probe: some filesystems accept O_DIRECT at open but fail on I/O.
                os.pwrite(fd_tmp, _aligned_buffer(RND_BLOCK, fill=False), 0)
            except OSError as e:
                if e.errno not in (errno.EINVAL, errno.EOPNOTSUPP):
                    raise
                if fd_tmp is not None:
                    os.close(fd_tmp)
                fd_tmp = os.open(path, flags, 0o600)
                res.direct_io = False
            fd = fd_tmp

            buf = _aligned_buffer(SEQ_BLOCK)
            blocks = self.size // SEQ_BLOCK
            total_steps = 5

            # 1. Sequential write
            t0 = time.perf_counter()
            for i in range(blocks):
                self.check()
                os.pwrite(fd, buf, i * SEQ_BLOCK)
                if i % 4 == 0:
                    self.report((i / blocks) / total_steps, "Sequential write")
            os.fsync(fd)
            res.seq_write = self.size / (time.perf_counter() - t0)
            self._drop_cache(fd, res.direct_io)

            # 2. Sequential read
            t0 = time.perf_counter()
            for i in range(blocks):
                self.check()
                os.preadv(fd, [buf], i * SEQ_BLOCK)
                if i % 4 == 0:
                    self.report((1 + i / blocks) / total_steps, "Sequential read")
            res.seq_read = self.size / (time.perf_counter() - t0)
            self._drop_cache(fd, res.direct_io)

            small = _aligned_buffer(RND_BLOCK)
            nblocks = self.size // RND_BLOCK

            # 3. Random 4K read, one request at a time
            n, lat = self._random(fd, small, nblocks, write=False, step=2, total=total_steps)
            res.rnd_read_iops = n / RND_SECONDS if n else 0
            res.rnd_read_lat_ms = lat
            self._drop_cache(fd, res.direct_io)

            # 4. Random 4K read, 8 parallel requests
            res.rnd_read_mt_iops = self._random_parallel(fd, nblocks, threads=8, step=3, total=total_steps)
            self._drop_cache(fd, res.direct_io)

            # 5. Random 4K write, one request at a time
            t0 = time.perf_counter()
            n, lat = self._random(fd, small, nblocks, write=True, step=4, total=total_steps)
            os.fsync(fd)
            res.rnd_write_iops = n / (time.perf_counter() - t0)
            res.rnd_write_lat_ms = lat
            self.report(1.0, "Done")
            return res
        finally:
            if fd_tmp is not None:
                os.close(fd_tmp)
            try:
                os.unlink(path)
            except OSError:
                pass

    @staticmethod
    def _drop_cache(fd, direct):
        if not direct:
            os.fsync(fd)
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)

    def _random(self, fd, buf, nblocks, write, step, total):
        label = "Random 4K write" if write else "Random 4K read"
        rng = random.Random()
        count, lat_sum = 0, 0.0
        end = time.perf_counter() + RND_SECONDS
        start = time.perf_counter()
        while True:
            now = time.perf_counter()
            if now >= end:
                break
            off = rng.randrange(nblocks) * RND_BLOCK
            t = time.perf_counter()
            if write:
                os.pwrite(fd, buf, off)
            else:
                os.preadv(fd, [buf], off)
            lat_sum += time.perf_counter() - t
            count += 1
            if count % 256 == 0:
                self.check()
                self.report((step + (now - start) / RND_SECONDS) / total, label)
        return count, (lat_sum / count * 1000) if count else 0.0

    def _random_parallel(self, fd, nblocks, threads, step, total):
        counts = [0] * threads
        stop = time.perf_counter() + RND_SECONDS

        def worker(i):
            buf = _aligned_buffer(RND_BLOCK, fill=False)
            rng = random.Random()
            while time.perf_counter() < stop and not self.cancel_event.is_set():
                os.preadv(fd, [buf], rng.randrange(nblocks) * RND_BLOCK)
                counts[i] += 1

        ts = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(threads)]
        start = time.perf_counter()
        for t in ts:
            t.start()
        while any(t.is_alive() for t in ts):
            time.sleep(0.1)
            self.report((step + min(1, (time.perf_counter() - start) / RND_SECONDS)) / total,
                        f"Random 4K read, {threads} parallel")
        self.check()
        return sum(counts) / (time.perf_counter() - start)


class SurfaceBenchmark(_Runner):
    """Read-only sampling of transfer rate across the device (like GNOME Disks)."""

    SAMPLES = 25
    SAMPLE_BYTES = 32 * MiB
    ACCESS_SAMPLES = 150

    def __init__(self, client, disk, progress, done):
        super().__init__(progress, done)
        self.client = client
        self.disk = disk

    def run(self):
        self.report(0.0, "Waiting for authorization…")
        fd = self.client.open_for_benchmark(self.disk.block_path)
        try:
            size = os.lseek(fd, 0, os.SEEK_END) or self.disk.size
            res = SurfaceResult(timestamp=time.time())
            buf = _aligned_buffer(SEQ_BLOCK, fill=False)
            span = size - self.SAMPLE_BYTES
            for i in range(self.SAMPLES):
                self.check()
                pos = (span * i // (self.SAMPLES - 1)) // MiB * MiB
                read = 0
                t0 = time.perf_counter()
                while read < self.SAMPLE_BYTES:
                    n = os.preadv(fd, [buf], pos + read)
                    if n <= 0:
                        break
                    read += n
                rate = read / (time.perf_counter() - t0)
                res.positions.append((100.0 * i / (self.SAMPLES - 1), rate))
                self.report(0.8 * (i + 1) / self.SAMPLES, "Reading across the disk surface")
            rates = [r for _, r in res.positions]
            res.avg, res.min, res.max = sum(rates) / len(rates), min(rates), max(rates)

            small = _aligned_buffer(RND_BLOCK, fill=False)
            rng = random.Random()
            total, n = 0.0, 0
            deadline = time.perf_counter() + 4
            for i in range(self.ACCESS_SAMPLES):
                self.check()
                off = rng.randrange(size // RND_BLOCK) * RND_BLOCK
                t = time.perf_counter()
                os.preadv(fd, [small], off)
                total += time.perf_counter() - t
                n += 1
                if i % 10 == 0:
                    self.report(0.8 + 0.2 * i / self.ACCESS_SAMPLES, "Measuring access time")
                if time.perf_counter() > deadline:
                    break
            res.access_ms = total / n * 1000 if n else 0
            self.report(1.0, "Done")
            return res
        finally:
            os.close(fd)


# --------------------------------------------------------------- helpers

def candidate_folders(disk, all_disks):
    """Writable folders that live on this disk, best first."""
    mounts = []  # (mountpoint, disk)
    for d in all_disks:
        for p in d.partitions:
            for mp in p.mountpoints:
                mounts.append((mp, d))

    def owner(path):
        path = os.path.realpath(path)
        best, best_disk = "", None
        for mp, d in mounts:
            if (path == mp or path.startswith(mp.rstrip("/") + "/")) and len(mp) > len(best):
                best, best_disk = mp, d
        return best_disk

    out = []
    for path in (os.path.expanduser("~"), "/var/tmp"):
        if owner(path) is disk and os.access(path, os.W_OK):
            out.append(path)
    for p in disk.partitions:
        for mp in p.mountpoints:
            if mp not in out and os.access(mp, os.W_OK) and os.path.isdir(mp):
                out.append(mp)
    return out


def _history_path():
    base = GLib.get_user_data_dir()
    return os.path.join(base, "diskwise", "benchmarks.json")


def disk_key(disk):
    return disk.serial or disk.drive_path


def load_history(disk):
    try:
        with open(_history_path()) as f:
            return json.load(f).get(disk_key(disk), [])
    except (OSError, ValueError):
        return []


def save_result(disk, result):
    path = _history_path()
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    entries = data.setdefault(disk_key(disk), [])
    entries.append(asdict(result))
    data[disk_key(disk)] = entries[-20:]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)
