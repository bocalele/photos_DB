import cv2
import numpy as np
import matplotlib.pyplot as plt

# Charger l'image en niveaux de gris
img = cv2.imread('cameraman.png', cv2.IMREAD_GRAYSCALE)

# Paramètres
levels = 4  # niveaux de pyramide
sigma = 1.0

# Fonctions pour calculer Laplacien et FFT
def laplacian_pyramid(img, levels, gaussian=True):
    current = img.copy()
    gaussian_pyr = [current]
    laplacian_pyr = []

    for i in range(levels-1):
        if gaussian:
            next_level = cv2.GaussianBlur(current, (5,5), sigma)
        else:
            next_level = current.copy()
        lap = cv2.subtract(current, next_level)
        laplacian_pyr.append(lap)
        gaussian_pyr.append(next_level)
        current = next_level
    
    # dernier niveau
    laplacian_pyr.append(current)
    return gaussian_pyr, laplacian_pyr

def fft_image(img):
    f = np.fft.fft2(img)
    fshift = np.fft.fftshift(f)
    magnitude_spectrum = np.log1p(np.abs(fshift))
    return magnitude_spectrum

# Générer pyramides et FFT
gauss_pyr, lap_pyr = laplacian_pyramid(img, levels, gaussian=True)
gauss_pyr_noblur, lap_pyr_noblur = laplacian_pyramid(img, levels, gaussian=False)

fft_gauss = [fft_image(l) for l in lap_pyr]
fft_noblur = [fft_image(l) for l in lap_pyr_noblur]

# Reconstruction (simple addition)
reconstruction = sum(lap_pyr)
reconstruction_noblur = sum(lap_pyr_noblur)

# Affichage
fig, axes = plt.subplots(6, levels, figsize=(12, 16))

for col in range(levels):
    axes[0, col].imshow(gauss_pyr[col], cmap='gray')
    axes[0, col].set_title(f'Gauss lvl {col}')
    axes[1, col].imshow(lap_pyr[col], cmap='seismic')
    axes[1, col].set_title(f'Lap lvl {col}')
    axes[2, col].imshow(fft_gauss[col], cmap='jet')
    axes[2, col].set_title(f'FFT Gauss lvl {col}')
    
    axes[3, col].imshow(lap_pyr_noblur[col], cmap='seismic')
    axes[3, col].set_title(f'NoBlur Lapl lvl {col}')
    axes[4, col].imshow(fft_noblur[col], cmap='jet')
    axes[4, col].set_title(f'FFT NoBlur lvl {col}')

axes[5, 0].imshow(reconstruction, cmap='gray')
axes[5, 0].set_title('Reconstruction')
axes[5, 1].imshow(reconstruction_noblur, cmap='gray')
axes[5, 1].set_title('Reconstruction NoBlur')

# Masquer axes vides
for ax in axes.flatten():
    ax.axis('off')

plt.tight_layout()
plt.show()
