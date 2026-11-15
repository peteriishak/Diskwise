"""Per-disk state shared by the pages: SMART report, live trackers, results."""

from .backend import benchmark, devices, health
from .backend.iostats import IOTracker
from .backend.power import PowerTracker


class DiskContext:
    def __init__(self, client, disk):
        self.client = client
        self.disk = disk
        self.io = IOTracker(disk.name)
        self.power = PowerTracker(disk)
        self.report = health.HealthReport()
        self.details = devices.sysfs_details(disk)
        self.live_temp = None
        self.file_result = None
        self.surface_result = None
        self.history = benchmark.load_history(disk)
        for entry in reversed(self.history):
            if entry.get("kind") == "file" and self.file_result is None:
                self.file_result = benchmark.FileResult(**entry)
            if entry.get("kind") == "surface" and self.surface_result is None:
                entry = dict(entry, positions=[tuple(p) for p in entry.get("positions", [])])
                self.surface_result = benchmark.SurfaceResult(**entry)
        self.runner = None  # active benchmark
        self.listeners = []

    @property
    def key(self):
        return self.disk.drive_path

    def replace_disk(self, disk):
        self.disk = disk
        self.power.disk = disk
        self.details = devices.sysfs_details(disk)

    def refresh_smart(self):
        devices.refresh_smart(self.client, self.disk)
        self.report = health.assess(self.disk)

    @property
    def temperature(self):
        if self.live_temp is not None:
            return self.live_temp
        return self.report.temperature

    def tick(self, tick):
        sample = self.io.sample()
        self.power.update(self.client, sample, tick)
        if tick % 2 == 0:
            self.live_temp = devices.live_temperature(self.disk)
        return sample
