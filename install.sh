#!/bin/bash
# Install Diskwise.
#   ./install.sh              install for the current user (~/.local)
#   sudo ./install.sh /usr    install system-wide
#   ./install.sh --uninstall  remove a user install (add the prefix for system-wide)
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNINSTALL=0
if [[ "${1:-}" == "--uninstall" ]]; then UNINSTALL=1; shift; fi
PREFIX="${1:-$HOME/.local}"
APP_ID=io.github.diskwise.Diskwise
SHARE="$PREFIX/share"
APPDIR="$SHARE/diskwise"

if (( UNINSTALL )); then
  rm -rf "$APPDIR" "$PREFIX/bin/diskwise" \
         "$SHARE/applications/$APP_ID.desktop" \
         "$SHARE/metainfo/$APP_ID.metainfo.xml" \
         "$SHARE/icons/hicolor/scalable/apps/$APP_ID.svg" \
         "$SHARE/icons/hicolor/symbolic/apps/$APP_ID-symbolic.svg"
  if [[ "$PREFIX" == /usr ]]; then
    rm -f /usr/libexec/diskwise-helper "/usr/share/polkit-1/actions/$APP_ID.policy"
  fi
  echo "Diskwise removed from $PREFIX"
  exit 0
fi

missing=()
python3 -c 'import gi; gi.require_version("Gtk","4.0"); gi.require_version("Adw","1")' 2>/dev/null \
  || missing+=(python3-gobject gtk4 libadwaita)
command -v udisksctl >/dev/null || missing+=(udisks2)
command -v smartctl >/dev/null || missing+=(smartmontools)
command -v nvme >/dev/null || missing+=(nvme-cli)
if (( ${#missing[@]} )); then
  echo "Missing packages. Install them with:"
  echo "  sudo dnf install ${missing[*]}"
  exit 1
fi

install -d "$APPDIR" "$PREFIX/bin" "$SHARE/applications" "$SHARE/metainfo" \
           "$SHARE/icons/hicolor/scalable/apps" "$SHARE/icons/hicolor/symbolic/apps"
rm -rf "$APPDIR/diskwise" "$APPDIR/data"
cp -r "$SRC/diskwise" "$APPDIR/"
cp -r "$SRC/data" "$APPDIR/"
find "$APPDIR" -name __pycache__ -type d -prune -exec rm -rf {} +

cat > "$PREFIX/bin/diskwise" <<LAUNCHER
#!/usr/bin/python3
import sys
sys.path.insert(0, "$APPDIR")
from diskwise.app import main
sys.exit(main())
LAUNCHER
chmod 755 "$PREFIX/bin/diskwise"

install -m644 "$SRC/data/$APP_ID.desktop" "$SHARE/applications/"
install -m644 "$SRC/data/$APP_ID.metainfo.xml" "$SHARE/metainfo/"
install -m644 "$SRC/data/icons/hicolor/scalable/apps/$APP_ID.svg" "$SHARE/icons/hicolor/scalable/apps/"
install -m644 "$SRC/data/icons/hicolor/scalable/apps/$APP_ID-symbolic.svg" "$SHARE/icons/hicolor/symbolic/apps/"

# System-wide installs also get the privileged helper with its own polkit policy,
# so the password prompt names Diskwise instead of a generic python3.
if [[ "$PREFIX" == /usr ]]; then
  install -Dm755 "$SRC/diskwise/helper.py" /usr/libexec/diskwise-helper
  install -Dm644 "$SRC/data/$APP_ID.policy" "/usr/share/polkit-1/actions/$APP_ID.policy"
fi
gtk-update-icon-cache -q -t "$SHARE/icons/hicolor" 2>/dev/null || true
update-desktop-database -q "$SHARE/applications" 2>/dev/null || true

echo "Diskwise installed to $PREFIX"
echo "Launch it from the app grid or run: diskwise"
