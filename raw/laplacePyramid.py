import numpy as np
import matplotlib.pyplot as plt
from scipy.ndimage import gaussian_filter, zoom
from imageio import imread

# ============================================================
# Fonctions utilitaires
# ============================================================

def downsample(img, sigma=1.0):
    """Floute puis sous-échantillonne de facteur 2"""
    #blurred = gaussian_filter(img, sigma=(sigma, sigma, 0))  # flou 2D sur chaque canal
    blurred = img
    return blurred[::2, ::2, :]

def upsample(img, shape, sigma=1.0):
    """Agrandit (x2) puis applique un léger flou"""
    factor = (shape[0]/img.shape[0], shape[1]/img.shape[1], 1)
    up = zoom(img, factor, order=1)  # interpolation bilinéaire
    up = gaussian_filter(up, sigma=(sigma, sigma, 0))
    return up

def build_pyramids(img, levels=4, sigma=1.0):
    """Construit la pyramide gaussienne et la pyramide laplacienne"""
    gauss_pyr = [img]
    for i in range(1, levels):
        gauss_pyr.append(downsample(gauss_pyr[-1], sigma))
    
    laplace_pyr = []
    for i in range(levels - 1):
        expanded = upsample(gauss_pyr[i+1], gauss_pyr[i].shape, sigma)
        laplace = gauss_pyr[i] - expanded
        laplace_pyr.append(laplace)
    laplace_pyr.append(gauss_pyr[-1])  # le dernier niveau (base floue)
    return gauss_pyr, laplace_pyr

def reconstruct_from_laplacian(laplace_pyr, sigma=1.0):
    """Reconstruit l'image à partir de la pyramide de Laplace"""
    img = laplace_pyr[-1]
    for level in reversed(laplace_pyr[:-1]):
        img = upsample(img, level.shape, sigma) + level
    return img


# ============================================================
# Exemple d'utilisation
# ============================================================

# Lecture d'une image (normalisée [0,1])
img = imread('/Users/pc/PHOTOS/2024/Jpeg/JPEG/DSC_7673.jpg').astype(np.float32) / 255.0

# Construction des pyramides
gauss_pyr, laplace_pyr = build_pyramids(img, levels=4, sigma=1.0)

# Reconstruction
reconstructed = reconstruct_from_laplacian(laplace_pyr, sigma=1.0)

# Vérification
diff = np.abs(img - reconstructed)
print("Erreur moyenne :", np.mean(diff))

# ============================================================
# Visualisation
# ============================================================
plt.figure(figsize=(24,12))

# Affiche les niveaux de la pyramide gaussienne
for i, g in enumerate(gauss_pyr):
    plt.subplot(2, len(gauss_pyr), i+1)
    plt.imshow(np.clip(g, 0, 1))
    plt.axis('off')
    plt.title(f'Gauss {i}')

# Affiche les niveaux de la pyramide laplacienne
for i, l in enumerate(laplace_pyr):
    plt.subplot(2, len(laplace_pyr), len(gauss_pyr)+i+1)
    plt.imshow(np.clip(l - l.min(), 0, 1))  # recentre pour affichage
    plt.axis('off')
    plt.title(f'Laplace {i}')

plt.tight_layout()
plt.show()

# Image reconstruite vs originale
plt.figure(figsize=(20,15))
plt.subplot(2,1,1); plt.imshow(np.clip(img, 0, 1)); plt.title('Originale'); plt.axis('off')
plt.subplot(2,1,2); plt.imshow(np.clip(reconstructed, 0, 1)); plt.title('Reconstituée'); plt.axis('off')
plt.show()
