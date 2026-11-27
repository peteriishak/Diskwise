import time

from gi.repository import Adw, Gtk

from ..backend import benchmark
from . import fmt
from .charts import LineChart
from .widgets import StatTile, clear, legend, page_box, section, tile_grid

READ, WRITE = 0, 1  # categorical slots: blue, orange

SIZES = [(256, "256 MB"), (1024, "1 GB"), (4096, "4 GB")]


def rate_axis(v, precise=False):
    if precise:
        return fmt.rate(v)
    return fmt.size(v).replace(".0 ", " ") + "/s" if v else "0"


def time_ticks(n):
    return [(0, "2 min ago"), (n // 2, "1 min ago"), (n - 1, "now")] if n > 2 else []


def time_hover(i, n):
    s = n - 1 - i
    return "now" if s == 0 else f"{s} s ago"


class PerformancePage:
    def __init__(self, ctx, all_disks, toast):
        self.ctx = ctx
        self.all_disks = all_disks
        self.toast = toast
        self.widget, box = page_box()

        # Live tiles
        self.t_read = StatTile("Read")
        self.t_write = StatTile("Write")
        self.t_iops = StatTile("Operations")
        self.t_busy = StatTile("Busy")
        self.t_lat = StatTile("Response time")
        box.append(tile_grid([self.t_read, self.t_write, self.t_iops, self.t_busy, self.t_lat],
                             min_per_line=2, max_per_line=5))

        # Live chart
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card.add_css_class("card")
        card.add_css_class("chart-card")
        card.append(section("Throughput", "Live transfer rate over the last 2 minutes",
                            legend([("Read", READ), ("Write", WRITE)])))
        self.chart = LineChart(rate_axis, time_ticks, time_hover, height=220, min_max=1_000_000)
        card.append(self.chart)
        self.totals = Gtk.Label(xalign=0, wrap=True)
        self.totals.add_css_class("caption")
        self.totals.add_css_class("dim-label")
        card.append(self.totals)
        box.append(card)

        # Benchmark controls
        self.folders = benchmark.candidate_folders(ctx.disk, all_disks)
        group = Adw.PreferencesGroup(
            title="Speed test",
            description="Measures what this drive can do. Close heavy apps first for accurate numbers.")
        self.mode_row = Adw.ComboRow(title="Test")
        modes = Gtk.StringList()
        self.modes = []
        if self.folders:
            modes.append("Read and write (file)")
            self.modes.append("file")
        modes.append("Surface read (whole disk, read-only)")
        self.modes.append("surface")
        self.mode_row.set_model(modes)
        self.mode_row.connect("notify::selected", lambda *a: self._sync_rows())
        group.add(self.mode_row)

        self.folder_row = Adw.ComboRow(title="Folder", subtitle="A temporary file is created and removed here")
        self.folder_row.set_model(Gtk.StringList.new(self.folders))
        group.add(self.folder_row)
        self.size_row = Adw.ComboRow(title="Test file size")
        self.size_row.set_model(Gtk.StringList.new([s for _, s in SIZES]))
        self.size_row.set_selected(1)
        group.add(self.size_row)

        self.surface_note = Adw.ActionRow(
            title="Reads samples across the entire disk",
            subtitle="Nothing is written. Requires administrator authorization.")
        self.surface_note.add_prefix(Gtk.Image.new_from_icon_name("dialog-information-symbolic"))
        group.add(self.surface_note)

        run_row = Adw.ActionRow()
        self.progress = Gtk.ProgressBar(valign=Gtk.Align.CENTER, hexpand=True, show_text=False)
        self.progress_label = Gtk.Label(xalign=0)
        self.progress_label.add_css_class("caption")
        self.progress_label.add_css_class("dim-label")
        pbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER,
                       hexpand=True)
        pbox.append(self.progress)
        pbox.append(self.progress_label)
        run_row.add_prefix(pbox)
        self.run_btn = Gtk.Button(label="Start Test", valign=Gtk.Align.CENTER)
        self.run_btn.add_css_class("suggested-action")
        self.run_btn.add_css_class("pill-small")
        self.run_btn.connect("clicked", self._on_run)
        run_row.add_suffix(self.run_btn)
        group.add(run_row)
        self.progress.set_visible(False)
        self.progress_label.set_label("Takes about 20 seconds")
        box.append(group)

        # Results
        self.results = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        box.append(self.results)

        self._sync_rows()
        self._show_results()
        if ctx.runner:
            self._set_running(True)
        self.tick()

    def _mode(self):
        return self.modes[self.mode_row.get_selected()]

    def _sync_rows(self):
        file_mode = self._mode() == "file"
        self.folder_row.set_visible(file_mode)
        self.size_row.set_visible(file_mode)
        self.surface_note.set_visible(not file_mode)

    # --------------------------------------------------------------- live
    def tick(self):
        io = self.ctx.io
        s = io.latest
        self.t_read.set(fmt.rate(s.read_bps), f"Peak {fmt.rate(io.peak_read)}")
        self.t_write.set(fmt.rate(s.write_bps), f"Peak {fmt.rate(io.peak_write)}")
        self.t_iops.set(f"{fmt.compact(s.read_iops + s.write_iops)}/s",
                        f"{fmt.compact(s.read_iops)} read · {fmt.compact(s.write_iops)} write")
        self.t_busy.set(f"{s.util * 100:.0f}%", f"{s.in_flight} requests in flight")
        self.t_lat.set(fmt.ms(s.latency_ms) if s.latency_ms else "—", "Average per request")
        self.chart.set_series([("Read", READ, io.series("read_bps")),
                               ("Write", WRITE, io.series("write_bps"))])
        rd, wr = io.lifetime_counters()
        self.totals.set_label(f"Since boot: {fmt.size(rd)} read · {fmt.size(wr)} written")

    # ---------------------------------------------------------- benchmark
    def _set_running(self, running):
        self.run_btn.set_label("Cancel" if running else "Start Test")
        if running:
            self.run_btn.remove_css_class("suggested-action")
            self.run_btn.add_css_class("destructive-action")
        else:
            self.run_btn.remove_css_class("destructive-action")
            self.run_btn.add_css_class("suggested-action")
        self.mode_row.set_sensitive(not running)
        self.folder_row.set_sensitive(not running)
        self.size_row.set_sensitive(not running)

    def _on_run(self, _btn):
        if self.ctx.runner:
            self.ctx.runner.cancel()
            return
        if self._mode() == "file":
            folder = self.folders[self.folder_row.get_selected()]
            size = SIZES[self.size_row.get_selected()][0] * benchmark.MiB
            runner = benchmark.FileBenchmark(folder, size, self._on_progress, self._on_done)
        else:
            runner = benchmark.SurfaceBenchmark(self.ctx.client, self.ctx.disk,
                                                self._on_progress, self._on_done)
        self.ctx.runner = runner
        self._set_running(True)
        self.progress.set_visible(True)
        self.progress.set_fraction(0)
        self.progress_label.set_label("Starting…")
        runner.start()

    def _on_progress(self, fraction, text):
        self.progress.set_fraction(fraction)
        self.progress_label.set_label(text)
        return False

    def _on_done(self, result, error):
        self.ctx.runner = None
        self._set_running(False)
        self.progress.set_visible(False)
        if error:
            self.progress_label.set_label(error)
            if error != "Cancelled":
                self.toast(f"Speed test failed: {error}")
            return False
        self.progress_label.set_label("Finished " + time.strftime("%H:%M"))
        if isinstance(result, benchmark.FileResult):
            self.ctx.file_result = result
        else:
            self.ctx.surface_result = result
        try:
            benchmark.save_result(self.ctx.disk, result)
        except OSError:
            pass
        self._show_results()
        return False

    def _show_results(self):
        clear(self.results)
        fr, sr = self.ctx.file_result, self.ctx.surface_result
        if fr:
            when = time.strftime("%d %b %Y, %H:%M", time.localtime(fr.timestamp))
            note = f"{when} · {fmt.size(fr.size_bytes)} file in {fr.folder}"
            if not fr.direct_io:
                note += " · cache flushed (direct I/O unavailable)"
            self.results.append(section("Read and write results", note))
            tiles = [
                StatTile("Sequential read"), StatTile("Sequential write"),
                StatTile("Random 4K read"), StatTile("Random 4K read, 8 parallel"),
                StatTile("Random 4K write"),
            ]
            tiles[0].set(fmt.rate(fr.seq_read), "Large files, 8 MB blocks")
            tiles[1].set(fmt.rate(fr.seq_write), "Including flush to disk")
            tiles[2].set(f"{fmt.compact(fr.rnd_read_iops)} IOPS",
                         f"{fmt.ms(fr.rnd_read_lat_ms)} per read · {fmt.rate(fr.rnd_read_iops * 4096)}")
            tiles[3].set(f"{fmt.compact(fr.rnd_read_mt_iops)} IOPS", fmt.rate(fr.rnd_read_mt_iops * 4096))
            tiles[4].set(f"{fmt.compact(fr.rnd_write_iops)} IOPS",
                         f"{fmt.ms(fr.rnd_write_lat_ms)} per write · {fmt.rate(fr.rnd_write_iops * 4096)}")
            self.results.append(tile_grid(tiles, 2, 5))
        if sr and sr.positions:
            when = time.strftime("%d %b %Y, %H:%M", time.localtime(sr.timestamp))
            self.results.append(section("Surface read results", f"{when} · read-only, raw device"))
            tiles = [StatTile("Average read"), StatTile("Minimum"), StatTile("Maximum"),
                     StatTile("Access time")]
            tiles[0].set(fmt.rate(sr.avg))
            tiles[1].set(fmt.rate(sr.min))
            tiles[2].set(fmt.rate(sr.max))
            tiles[3].set(fmt.ms(sr.access_ms), "Random 4K read")
            self.results.append(tile_grid(tiles, 2, 4))
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            card.add_css_class("card")
            card.add_css_class("chart-card")
            card.append(section("Read speed across the disk",
                                "Hard disks are fastest at the start (outer tracks); SSDs stay flat"))
            pos = [p for p, _ in sr.positions]
            n = len(pos)
            chart = LineChart(
                rate_axis,
                lambda n_: [(0, "Start"), (n_ // 2, "Middle"), (n_ - 1, "End")] if n_ > 2 else [],
                lambda i, _n: f"Position {pos[i]:.0f}% · offset {fmt.size(self.ctx.disk.size * pos[i] / 100)}",
                height=200, min_max=1_000_000)
            chart.set_series([("Read", READ, [r for _, r in sr.positions])])
            card.append(chart)
            self.results.append(card)
        if not fr and not (sr and sr.positions):
            empty = Gtk.Label(label="No speed test has been run on this drive yet.", xalign=0)
            empty.add_css_class("dim-label")
            self.results.append(empty)
