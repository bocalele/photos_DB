"""Démo : détecte les visages d'une photo et enregistre le résultat.

Usage :
    pixi run python detect.py "/chemin/vers/photo.jpg"

L'image annotée est écrite à côté du script sous le nom `resultat.jpg`.
"""

import sys
from pathlib import Path

import cv2
from insightface.app import FaceAnalysis

if len(sys.argv) != 2:
    raise SystemExit("Usage : python detect.py /chemin/vers/photo.jpg")
source = Path(sys.argv[1]).expanduser()
if not source.is_file():
    raise SystemExit(f"Image introuvable : {source}")

# Initialiser le détecteur et le modèle de reconnaissance
app = FaceAnalysis(
    name="buffalo_l",
    providers=["CPUExecutionProvider"],
)

app.prepare(ctx_id=-1, det_size=(640, 640))

image = cv2.imread(str(source))
if image is None:
    raise FileNotFoundError(f"Impossible de lire {source}")

faces = app.get(image)

print(f"{len(faces)} visage(s) détecté(s)")

for i, face in enumerate(faces, start=1):
    x1, y1, x2, y2 = face.bbox.astype(int)
    print(f"Visage {i} : boîte [{x1}, {y1}, {x2}, {y2}]")
    print(f"Confiance : {face.det_score:.3f}")
    print(f"Embedding : {face.embedding.shape}")

    cv2.rectangle(image, (x1, y1), (x2, y2), (0, 255, 0), 2)

destination = Path(__file__).resolve().parent / "resultat.jpg"
cv2.imwrite(str(destination), image)
print(f"Image enregistrée dans {destination}")
