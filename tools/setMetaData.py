import shutil
import subprocess
from os import PathLike
from typing import Union


WRITABLE_CATEGORIES = {"EXIF", "IPTC", "XMP"}


def set_metadata(filepath: Union[str, PathLike], categories: dict, dry_run=False):
    arguments = ["-overwrite_original"]
    for category, metadata in categories.items():
        if category not in WRITABLE_CATEGORIES:
            continue
        if not isinstance(metadata, dict):
            raise TypeError(f"La catégorie {category} doit être un dictionnaire.")
        for tag, value in metadata.items():
            if value is None:
                continue
            values = value if isinstance(value, list) else [value]
            arguments.extend(
                f"-{category}:{tag}={item}" for item in values
            )

    if len(arguments) == 1:
        return None
    arguments.append(str(filepath))
    if dry_run:
        print(f"Dry run: Metadata to be set for {filepath}:")
        for argument in arguments[1:-1]:
            print(argument)
        return arguments

    executable = shutil.which("exiftool")
    if executable is None:
        raise FileNotFoundError("ExifTool est introuvable dans le PATH.")
    result = subprocess.run(
        [executable, *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if result.returncode:
        message = result.stderr.strip() or result.stdout.strip() or "Erreur ExifTool."
        raise RuntimeError(f"Échec d’écriture des métadonnées pour {filepath}: {message}")
    return result
