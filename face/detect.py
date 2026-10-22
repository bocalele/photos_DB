import cv2
from insightface.app import FaceAnalysis

# Initialiser le détecteur et le modèle de reconnaissance
app = FaceAnalysis(
    name="buffalo_l",
    providers=["CPUExecutionProvider"],
)

app.prepare(ctx_id=-1, det_size=(640, 640))

# Charger une photo
image = cv2.imread("/Volumes/WD/PHOTOS/REPRODUCTION/Groupe4/JPEG/DSC_1400.jpg")

if image is None:
    raise FileNotFoundError("Impossible de lire photo.jpg")

# Détecter les visages
faces = app.get(image)

print(f"{len(faces)} visage(s) détecté(s)")

for i, face in enumerate(faces, start=1):
    x1, y1, x2, y2 = face.bbox.astype(int)
    print(f"Visage {i} : boîte [{x1}, {y1}, {x2}, {y2}]")
    print(f"Confiance : {face.det_score:.3f}")
    print(f"Embedding : {face.embedding.shape}")

    cv2.rectangle(image, (x1, y1), (x2, y2), (0, 255, 0), 2)

cv2.imwrite("resultat.jpg", image)
print("Image enregistrée dans resultat.jpg")
