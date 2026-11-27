Name:           diskwise
Version:        1.0.0
Release:        1%{?dist}
Summary:        Know your drives: health, speed, power and management
License:        GPL-3.0-or-later
URL:            https://github.com/peteriishak/Diskwise
Source0:        %{url}/archive/v%{version}/%{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  desktop-file-utils
BuildRequires:  libappstream-glib
Requires:       python3
Requires:       python3-gobject
Requires:       gtk4 >= 4.12
Requires:       libadwaita >= 1.6
Requires:       udisks2
Requires:       smartmontools
Requires:       nvme-cli
Requires:       polkit
Recommends:     exfatprogs
Recommends:     ntfsprogs
Recommends:     dosfstools
Recommends:     xfsprogs
Recommends:     btrfs-progs
Recommends:     cryptsetup

%description
Diskwise shows the health (SMART / NVMe health log), temperature,
wear, live throughput, benchmark speed and estimated power use of every
drive, in a GNOME-style interface.

%prep
%autosetup -n Diskwise-%{version}

%build

%install
install -d %{buildroot}%{_datadir}/%{name} %{buildroot}%{_bindir} %{buildroot}%{_libexecdir}
cp -r diskwise data %{buildroot}%{_datadir}/%{name}/
cat > %{buildroot}%{_bindir}/%{name} <<'LAUNCHER'
#!/usr/bin/python3
import sys
sys.path.insert(0, "/usr/share/diskwise")
from diskwise.app import main
sys.exit(main())
LAUNCHER
chmod 755 %{buildroot}%{_bindir}/%{name}
install -m755 diskwise/helper.py %{buildroot}%{_libexecdir}/diskwise-helper
install -Dm644 data/io.github.peteriishak.Diskwise.desktop %{buildroot}%{_datadir}/applications/io.github.peteriishak.Diskwise.desktop
install -Dm644 data/io.github.peteriishak.Diskwise.metainfo.xml %{buildroot}%{_metainfodir}/io.github.peteriishak.Diskwise.metainfo.xml
install -Dm644 data/io.github.peteriishak.Diskwise.policy %{buildroot}%{_datadir}/polkit-1/actions/io.github.peteriishak.Diskwise.policy
install -Dm644 data/icons/hicolor/scalable/apps/io.github.peteriishak.Diskwise.svg %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/io.github.peteriishak.Diskwise.svg
install -Dm644 data/icons/hicolor/scalable/apps/io.github.peteriishak.Diskwise-symbolic.svg %{buildroot}%{_datadir}/icons/hicolor/symbolic/apps/io.github.peteriishak.Diskwise-symbolic.svg

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/io.github.peteriishak.Diskwise.desktop
appstream-util validate-relax --nonet %{buildroot}%{_metainfodir}/io.github.peteriishak.Diskwise.metainfo.xml

%files
%license LICENSE
%doc README.md
%{_bindir}/%{name}
%{_libexecdir}/diskwise-helper
%{_datadir}/%{name}/
%{_datadir}/applications/io.github.peteriishak.Diskwise.desktop
%{_metainfodir}/io.github.peteriishak.Diskwise.metainfo.xml
%{_datadir}/polkit-1/actions/io.github.peteriishak.Diskwise.policy
%{_datadir}/icons/hicolor/scalable/apps/io.github.peteriishak.Diskwise.svg
%{_datadir}/icons/hicolor/symbolic/apps/io.github.peteriishak.Diskwise-symbolic.svg

%changelog
* Thu Oct 15 2026 Peter Habib <343233346+peteriishak@users.noreply.github.com> - 1.0.0-1
- Initial release
