# Public Release Audit: Diskwise

**Date:** 2026-11-27
**Repository:** https://github.com/peteriishak/Diskwise (private at the time of the audit)
**Version audited:** 1.0.0
**Result:** Ready to make public. See [Remaining issues](#remaining-issues) for optional follow-ups.

## 1. What was audited

- Every tracked file (43 before cleanup) and the full Git history: all 3 original commits, every blob
  ever committed, all branches and tags on the remote (only `main` existed)
- Source code (`diskwise/`, 25 Python modules), the privileged helper, launchers and installer,
  packaging (`diskwise.spec`), desktop integration and polkit policy (`data/`), documentation
- Untracked and ignored files in the working tree
- Commit authorship and contributor attribution
- Third-party dependencies and bundled assets

## 2. Removed or reorganized

| Item | Action | Reason |
|---|---|---|
| `__pycache__/` | Deleted from the working tree | Bytecode caches. They were never committed and are ignored. |
| 4 unused imports | Removed | Dead code reported by pyflakes. No logic changed. |
| `io.github.diskwise.Diskwise` app ID | Renamed to `io.github.peteriishak.Diskwise` | The ID implied a `diskwise` GitHub account the author doesn't own. The desktop file, metainfo, polkit policy (file and action ID), icons, `APP_ID`, `install.sh` and the spec were renamed together. |
| "Diskwise contributors", `noreply@example.com` | Replaced with Peter Habib and the GitHub noreply address | Placeholder identity |
| Project URLs | Added to the metainfo, About dialog, polkit policy and spec | None existed |
| `docs/screenshots/` | Added | README and AppStream screenshots |

The `Design/` folder (branding drafts) stays local and is excluded by `.gitignore`, as before. The
structure was already clean (`diskwise/backend`, `diskwise/ui`, `data/`), so no source files moved.

## 3. Bugs found and fixed

| Bug | Fix |
|---|---|
| **RPM build failure:** `%install` contained a bare path line (`%{_datadir}/icons/.../Diskwise-symbolic.svg`) that rpmbuild runs as a shell command, so the build stopped. | Line removed. The icon was already installed by the next line. |
| **No LICENSE file**, although the spec and metainfo declare GPL-3.0-or-later | Added the canonical GPL-3.0 text from gnu.org. The spec now ships it with `%license`. |
| **Understated requirement:** the code uses `Adw.Spinner` (libadwaita 1.6) but the spec required `>= 1.5` | `Requires: libadwaita >= 1.6`; README states the real minimum |
| **RPM source layout:** no `URL`, and `%autosetup` expected a directory name that a GitHub archive doesn't produce | Added `URL`, `Source0` pointing at the tag archive, `%autosetup -n Diskwise-%{version}`; README documents `git archive --prefix=Diskwise-1.0.0/` |

## 4. Security and privacy checks

| Check | Result |
|---|---|
| API keys, tokens, passwords, private keys (AWS, GitHub, OpenAI, Slack and generic patterns) across all history | None found. Matches for "password" and "token" are UI strings for LUKS unlocking and typed confirmation. |
| Personal data: home paths, usernames, email addresses, host names, private URLs | None found |
| Files that existed in history but not in the current tree | None |
| Image metadata | None. The SVGs are minimal, hand-written paths. |
| Screenshots | Taken from the developer machine's drive. They show the drive model (a retail Kingston NVMe), usage figures and the standard `/boot` and `/` mount points. No serial number, host name, username, volume label or file name is visible. |
| Privileged code | `helper.py` runs only fixed, read-only `smartctl -c` / `nvme get-feature` queries. It is reached through `pkexec` with a dedicated polkit action (`auth_admin_keep`). Destructive operations go through UDisks2 and polkit. No setuid binaries or `sudo` calls. |
| Commit identities | All commits authored and committed by `Peter Habib <343233346+peteriishak@users.noreply.github.com>`, a GitHub noreply address |
| `.gitignore` | Extended to cover caches, packaging output, editor files and a standard secrets block |

## 5. Licensing

- **Project license:** GPL-3.0-or-later. It was already declared in the RPM spec and AppStream
  metadata. The author is the sole contributor. The LICENSE file now carries the full text.
- **Runtime dependencies** (all compatible): PyGObject (LGPL-2.1+), GTK 4 and libadwaita (LGPL-2.1+).
  UDisks2, smartmontools, nvme-cli and the filesystem tools are separate programs reached over D-Bus
  or as subprocesses, not linked.
- **Bundled assets:** app and page icons are original SVGs with no third-party attribution.
- **Metadata license:** AppStream metainfo is CC0-1.0, as AppStream requires.

## 6. Contributor attribution

- All 3 original commits carried a `Co-Authored-By: Claude` trailer, which makes GitHub credit an AI
  account. The trailers were removed from the commit messages. Authorship, author and committer dates
  and file contents are unchanged, and tree hashes were verified identical before and after.
- New commits were made under the same identity without AI trailers.
- `main` on GitHub was replaced with the rewritten history (`--force-with-lease`). The pre-rewrite
  history is backed up as a Git bundle outside the repository.
- No commits from other people exist, so no authorship was changed or invented.

## 7. Build and functionality tests

All run on Fedora 44 (Python 3.14, GTK 4.22, libadwaita 1.9, UDisks2) with one NVMe drive.

| Test | Result |
|---|---|
| Automated tests | None exist in the project (see Remaining issues) |
| Compile and import | All 25 modules import; `helper.py` and `diskwise-run` compile |
| GUI launch on real hardware | All five pages (Overview, Health, Performance, Power, Manage) render with live data and no errors or warnings in the log |
| `./install.sh <prefix>` | Installs the launcher, app files, desktop entry, metainfo and icons; the installed copy imports |
| `./install.sh --uninstall <prefix>` | Removes everything except the regenerated GTK icon and MIME caches (expected) |
| `desktop-file-validate` | Passes |
| `appstreamcli validate` | Passes |
| Polkit policy XML (`xmllint`) | Well-formed |
| RPM build and install in a clean `fedora:44` container | **Pass**, no warnings. `rpmbuild -ba` built the binary and source RPMs from a `git archive` tarball (it failed before the spec fix); `%check` passed; `dnf install` resolved every dependency; launcher, `/usr/libexec/diskwise-helper`, polkit policy, desktop entry, metainfo, icons and LICENSE are installed, and the installed app imports |
| Destructive operations (format, partition, delete) | Not run against real disks during this audit. The code paths were unchanged apart from import cleanup. |

## 8. Documentation

- `README.md` rewritten: purpose, features, safety guarantees, screenshots, requirements, three install
  paths (source, `install.sh`, RPM), usage, privileges, how the numbers are calculated, configuration,
  troubleshooting, project layout, license
- `CONTRIBUTING.md` added, including rules for working safely with disks (loop devices, keeping
  system-disk protection and typed confirmation)
- AppStream metainfo now lists screenshots and project URLs

## Remaining issues

None block the public release. Optional follow-ups:

1. **No automated tests.** The health scoring (`backend/health.py`), power estimate and free-region
   calculation are pure functions that would be easy to cover with pytest.
2. **Push a `v1.0.0` tag.** The spec's `Source0` points at the GitHub archive for tag `v1.0.0`. Until
   the tag exists, build RPMs from `git archive` as the README describes.
3. **Unused local variables (2)** in `ui/health_page.py` and `ui/performance.py` are harmless and were left alone.
4. **Screenshots come from the developer's machine** (drive model and usage only). Replace them if
   you'd rather not publish that.
5. **Old installs:** anyone who installed an earlier build under `io.github.diskwise.Diskwise` will see
   a second launcher until they remove it (`./install.sh --uninstall` from the old checkout).

## Final readiness

| Requirement | Status |
|---|---|
| No secrets or personal data in files or history | ✅ |
| Complete license, compatible dependencies | ✅ |
| Clean attribution (sole author, no AI co-author trailers) | ✅ |
| Builds, installs and runs | ✅ |
| README, CONTRIBUTING, .gitignore | ✅ |

**Diskwise is ready to be switched from private to public.**
