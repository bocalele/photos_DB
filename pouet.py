import json
import subprocess
from collections import defaultdict

def parse_exiv2(stdout_text: str) -> dict:
    metadata = {}
    for line in stdout_text.splitlines():
        parts = line.strip().split(maxsplit=3)
        if len(parts) == 4:
            metadata[parts[0]] = parts[3].strip()
        elif len(parts) >= 1:
            metadata[parts[0]] = ""
    return metadata

def get_metadata_exiv2(filepath: str) -> dict | list:
    # Exécution de exiv2 avec l'option -p j pour obtenir le JSON
    result = subprocess.run(
        ["exiv2", "-pa", filepath],
        capture_output=True,
        text=True,
        check=True,
    )
    categories = defaultdict(dict)
    for tag, value in parse_exiv2(result.stdout).items():
        print (tag.upper())
        if tag.upper().startswith("EXIF."):
            categories["EXIF"][tag[5:]] = value
        elif tag.upper().startswith("IPTC."):
            categories["IPTC"][tag[5:]] = value
        elif tag.upper().startswith("XMP."):
            categories["XMP"][tag[4:]] = value
        elif tag.upper().startswith("MakerNotes."):
            categories["MakerNotes"][tag[11:]] = value
        elif tag.upper().startswith("Composite:"):
            categories["Composite"][tag[10:]] = value
        else:
            categories["OTHER"][tag] = value 
    return categories