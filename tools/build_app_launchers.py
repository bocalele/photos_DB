#!/usr/bin/env python3
"""Reconstruit les bundles .app macOS comme applications AppleScript autonomes.

Un simple script shell comme exécutable de bundle ne signale jamais à macOS que
le lancement est terminé : l'icône du Dock clignote, et macOS peut tuer les
processus enfants quand le script se termine. Une application AppleScript
« stay open » garde le serveur vivant et rend la main immédiatement.
"""

from __future__ import annotations

import plistlib
from pathlib import Path
import shutil
import subprocess
import tempfile


PROJECT_DIR = Path(__file__).resolve().parent.parent

APPS = (
    ("annotations/Annotations.app", "Lancer Annotations.command"),
    ("faune/Faune.app", "Lancer Faune.command"),
    ("face/Face.app", "Lancer Face.command"),
    ("geotag/Geotag.app", "Lancer Geotag.command"),
    ("motscles/Motscles.app", "Lancer Mots-clés.command"),
    ("select/Selection.app", "Lancer Sélection.command"),
)

TEMPLATE = """\
property startedAt : missing value
property sawServer : false

on run
    set startedAt to current date
    set sawServer to false
    my launchServer()
end run

on reopen
    my launchServer()
end reopen

on idle
    set appDir to my appDirectory()
    set pidFile to appDir & "/logs/server.pid"
    try
        set pidText to do shell script "cat " & quoted form of pidFile & " 2>/dev/null"
        if pidText is "" then error "pid vide"
        do shell script "kill -0 " & pidText & " 2>/dev/null"
        set sawServer to true
        return 2
    on error
        if sawServer then quit
        if startedAt is not missing value then
            if (current date) - startedAt > 30 then quit
        end if
        return 1
    end try
end idle

on quit
    set appDir to my appDirectory()
    set urlFile to appDir & "/logs/server.url"
    try
        set serverURL to do shell script "cat " & quoted form of urlFile & " 2>/dev/null"
        if serverURL is not "" then
            do shell script "/usr/bin/curl --silent --max-time 3 -X POST " & quoted form of (serverURL & "/api/shutdown") & " >/dev/null 2>&1 || true"
        end if
    end try
    continue quit
end quit

on launchServer()
    set appDir to my appDirectory()
    set launcherPath to appDir & "/__LAUNCHER__"
    set logDir to appDir & "/logs"
    set helperPath to appDir & "/../tools/app_launcher.sh"
    do shell script "mkdir -p " & quoted form of logDir
    do shell script "/bin/bash " & quoted form of helperPath & " " & quoted form of launcherPath & " " & quoted form of logDir
end launchServer

on appDirectory()
    set bundlePath to POSIX path of (path to me)
    if bundlePath ends with "/" then set bundlePath to text 1 thru -2 of bundlePath
    return do shell script "dirname " & quoted form of bundlePath
end appDirectory
"""


def build(bundle: Path, launcher_name: str) -> None:
    info_path = bundle / "Contents" / "Info.plist"
    with info_path.open("rb") as source:
        old_info = plistlib.load(source)

    icon_name = old_info.get("CFBundleIconFile", "")
    icon_data = None
    if icon_name:
        icon_path = bundle / "Contents" / "Resources" / (
            icon_name if str(icon_name).endswith(".icns") else f"{icon_name}.icns"
        )
        if icon_path.is_file():
            icon_data = icon_path.read_bytes()

    source_text = TEMPLATE.replace("__LAUNCHER__", launcher_name)
    with tempfile.TemporaryDirectory(prefix="app-launcher-") as temporary:
        source_path = Path(temporary) / "launcher.applescript"
        source_path.write_text(source_text, encoding="utf-8")
        output = Path(temporary) / bundle.name
        subprocess.run(
            ["/usr/bin/osacompile", "-s", "-o", str(output), str(source_path)],
            check=True,
        )

        # Les icônes et l'identité du bundle sont conservées.
        shutil.rmtree(bundle)
        shutil.copytree(output, bundle)

    new_info = {
        "CFBundleDevelopmentRegion": old_info.get("CFBundleDevelopmentRegion", "fr"),
        "CFBundleDisplayName": old_info.get("CFBundleDisplayName", old_info.get("CFBundleName", bundle.stem)),
        "CFBundleExecutable": "applet",
        "CFBundleIdentifier": old_info.get("CFBundleIdentifier", f"org.photos.{bundle.stem.lower()}"),
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": old_info.get("CFBundleName", bundle.stem),
        "CFBundlePackageType": "APPL",
        "LSMinimumSystemVersion": old_info.get("LSMinimumSystemVersion", "12.0"),
        "NSHighResolutionCapable": True,
    }
    if icon_data is not None and icon_name:
        resources = bundle / "Contents" / "Resources"
        resources.mkdir(parents=True, exist_ok=True)
        icon_file = icon_name if str(icon_name).endswith(".icns") else f"{icon_name}.icns"
        (resources / icon_file).write_bytes(icon_data)
        new_info["CFBundleIconFile"] = icon_name

    info_path = bundle / "Contents" / "Info.plist"
    with info_path.open("wb") as target:
        plistlib.dump(new_info, target)
    subprocess.run(
        ["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(bundle)],
        check=True,
        capture_output=True,
    )


def main() -> None:
    for relative, launcher in APPS:
        bundle = PROJECT_DIR / relative
        if not bundle.is_dir():
            raise SystemExit(f"Bundle absent : {bundle}")
        build(bundle, launcher)
        print(f"construit : {relative}")


if __name__ == "__main__":
    main()
