# Contributing to Diskwise

Thanks for your interest in improving Diskwise. Bug reports, feature ideas, documentation fixes and
code are all welcome.

## Reporting a bug

Open an issue at <https://github.com/peteriishak/Diskwise/issues> and include:

- What you did, what you expected, and what happened instead
- Your distribution and version, and the libadwaita version (`rpm -q libadwaita` or `dpkg -l libadwaita-1-0`)
- The drive type and connection (NVMe, SATA SSD, hard disk, USB enclosure) and its model if relevant
- The terminal output of `./diskwise-run` (or `diskwise`) while reproducing the problem

Please remove serial numbers, volume labels, mount paths and other private details from logs and
screenshots before posting.

## Suggesting a feature

Open an issue describing the problem you want solved, not only the solution. Screenshots or mock-ups help.

## Working on the code

1. Fork the repository and create a branch from `main`.
2. Install the system packages listed in the [README](README.md#requirements).
3. Run the app from the source tree with `./diskwise-run`.
4. Make your change and test it on the drive types it affects.
5. Open a pull request that explains what changed, why, and how you tested it.

### Working safely with disks

Diskwise can erase data, so changes to `diskwise/backend/manage.py`, the format dialogs or the
benchmark code need extra care:

- Test destructive operations only on a spare drive, a USB stick, or a loop device
  (`truncate -s 2G test.img && udisksctl loop-setup -f test.img`).
- Keep the system-disk protection and the typed confirmation for every destructive action.
- Go through UDisks2 and polkit for privileged work; don't add `sudo` or new setuid paths.
- Keep `helper.py` limited to fixed, read-only queries.

### Guidelines

- Keep changes focused: one fix or feature per pull request.
- Follow the existing style: PEP 8 and short docstrings on public functions.
- User-facing text should be short, plain and free of jargon. Follow the
  [GNOME Human Interface Guidelines](https://developer.gnome.org/hig/) for UI work.
- Prefer the GNOME platform (GTK, libadwaita, GLib, UDisks2) over new dependencies.
- Never commit secrets, credentials or personal data.

## License

By contributing, you agree that your contributions are licensed under the
[GNU General Public License v3.0 or later](LICENSE), the same license as the project.
