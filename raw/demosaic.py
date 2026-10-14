import numpy as np
from scipy.ndimage import convolve

R=0; G=1; B=2

def bi_linear (rgb):

    # === Filtres d’interpolation ===
    # Moyenne pondérée simple pour chaque couleur
    kernel_G = np.array([[0, 1, 0],
                         [1, 4, 1],
                         [0, 1, 0]], dtype=np.float32) / 4.0

    kernel_RB = np.array([[1, 2, 1],
                          [2, 4, 2],
                          [1, 2, 1]], dtype=np.float32) / 4.0

    # === Interpolation bilinéaire ===
    rgb[:, :, R] = convolve( rgb[:, :, R], kernel_RB, mode='mirror')
    rgb[:, :, G] = convolve( rgb[:, :, G], kernel_G, mode='mirror')
    rgb[:, :, B] = convolve( rgb[:, :, B], kernel_RB, mode='mirror')

    return rgb
