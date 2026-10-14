# import cv2
# import numpy as np
# import matplotlib.pyplot as plt

# # Charger l'image en niveaux de gris
# img = cv2.imread('cameraman.png', cv2.IMREAD_GRAYSCALE)

# # Paramètres
# levels = 4  # niveaux de pyramide
# sigma = 1.0

# # Fonctions pour calculer Laplacien et FFT avec downsample/upsample
# def laplacian_pyramid(img, levels, gaussian=True):
#     current = img.copy()
#     gaussian_pyr = [current]
#     laplacian_pyr = []

#     for i in range(levels-1):
#         if gaussian:
#             blurred = cv2.GaussianBlur(current, (5,5), sigma)
#         else:
#             blurred = current.copy()
#         # Downsample
#         down = cv2.pyrDown(blurred)
#         # Upsample pour calcul du laplacien
#         up = cv2.pyrUp(down, dstsize=(current.shape[1], current.shape[0]))
#         lap = cv2.subtract(current, up)
#         laplacian_pyr.append(lap)
#         gaussian_pyr.append(down)
#         current = down
    
#     # dernier niveau
#     laplacian_pyr.append(current)
#     return gaussian_pyr, laplacian_pyr

# def fft_image(img):
#     f = np.fft.fft2(img)
#     fshift = np.fft.fftshift(f)
#     magnitude_spectrum = np.log1p(np.abs(fshift))
#     return magnitude_spectrum

# # Générer pyramides et FFT
# gauss_pyr, lap_pyr = laplacian_pyramid(img, levels, gaussian=True)
# gauss_pyr_noblur, lap_pyr_noblur = laplacian_pyramid(img, levels, gaussian=False)

# fft_gauss = [fft_image(l) for l in lap_pyr]
# fft_noblur = [fft_image(l) for l in lap_pyr_noblur]

# # Reconstruction avec upsampling
# def reconstruct_from_laplacian(lap_pyr):
#     current = lap_pyr[-1]  # dernier niveau
#     for i in range(len(lap_pyr)-2, -1, -1):
#         up = cv2.pyrUp(current, dstsize=(lap_pyr[i].shape[1], lap_pyr[i].shape[0]))
#         current = cv2.add(up, lap_pyr[i])
#     return current

# reconstruction = reconstruct_from_laplacian(lap_pyr)
# reconstruction_noblur = reconstruct_from_laplacian(lap_pyr_noblur)

# # Affichage
# fig, axes = plt.subplots(6, levels, figsize=(12, 16))

# for col in range(levels):
#     axes[0, col].imshow(gauss_pyr[col], cmap='gray')
#     axes[0, col].set_title(f'Gauss lvl {col}')
#     axes[1, col].imshow(lap_pyr[col], cmap='seismic')
#     axes[1, col].set_title(f'Lap lvl {col}')
#     axes[2, col].imshow(fft_gauss[col], cmap='jet')
#     axes[2, col].set_title(f'FFT Gauss lvl {col}')
    
#     axes[3, col].imshow(lap_pyr_noblur[col], cmap='seismic')
#     axes[3, col].set_title(f'NoBlur Lapl lvl {col}')
#     axes[4, col].imshow(fft_noblur[col], cmap='jet')
#     axes[4, col].set_title(f'FFT NoBlur lvl {col}')

# axes[5, 0].imshow(reconstruction, cmap='gray')
# axes[5, 0].set_title('Reconstruction')
# axes[5, 1].imshow(reconstruction_noblur, cmap='gray')
# axes[5, 1].set_title('Reconstruction NoBlur')

# # Masquer axes vides
# for ax in axes.flatten():
#     ax.axis('off')

# plt.tight_layout()
# plt.show()


import numpy as np
import matplotlib.pyplot as plt

# Intensité physique (valeurs normalisées entre 0 et 1)
I = np.linspace(0, 1, 500)

# Trois modèles de perception possibles
# 1. Linéaire (réponse d’un capteur, pas de l’œil)
L_linear = I

# 2. Loi de Stevens : L_perçue = I^γ, avec γ ≈ 0.4 (valeur typique pour la luminosité)
gamma = 0.4
L_stevens = I ** gamma

# 3. Loi logarithmique (Weber–Fechner, normalisée)
L_weber_fechner = np.log10(1 + 9 * I) / np.log10(10)

# Création du graphique
plt.figure(figsize=(8, 5))
plt.plot(I, L_linear, label="Réponse linéaire (capteur)", linestyle="--", color="gray")
plt.plot(I, L_stevens, label="Loi de Stevens ($I^{0.4}$)", color="orange", linewidth=2)
plt.plot(I, L_weber_fechner, label="Loi de Weber–Fechner (log)", color="blue", linewidth=2)
plt.title("Relation entre intensité physique et luminosité perçue")
plt.xlabel("Intensité physique (luminance réelle)")
plt.ylabel("Luminosité perçue (réponse visuelle)")
plt.legend()
plt.grid(True, linestyle=":")
plt.show()
