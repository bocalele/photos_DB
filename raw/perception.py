import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap

# Longueurs d'onde (spectre visible)
wavelengths = np.arange(380, 781, 1)

# Fonctions photopique et scotopique (approximations gaussiennes)
V_photopic = np.exp(-0.5 * ((wavelengths - 555) / 60)**2)
V_scotopic = np.exp(-0.5 * ((wavelengths - 507) / 45)**2)
V_photopic /= V_photopic.max()
V_scotopic /= V_scotopic.max()

# Interpolation mésopique (selon luminance)
# a(L) varie de 1 (jour) à 0 (nuit)
luminance_levels = [1.0, 0.75, 0.5, 0.25, 0.0]
colors = ["green", "#77cc77", "#cccc44", "#8888ff", "blue"]

# Création d'une colormap spectrale pour la barre de couleur
def wavelength_to_rgb(wavelength):
    gamma = 0.8
    R = G = B = 0
    if 380 <= wavelength < 440:
        R = -(wavelength - 440) / (440 - 380)
        B = 1.0
    elif 440 <= wavelength < 490:
        G = (wavelength - 440) / (490 - 440)
        B = 1.0
    elif 490 <= wavelength < 510:
        G = 1.0
        B = -(wavelength - 510) / (510 - 490)
    elif 510 <= wavelength < 580:
        R = (wavelength - 510) / (580 - 510)
        G = 1.0
    elif 580 <= wavelength < 645:
        R = 1.0
        G = -(wavelength - 645) / (645 - 580)
    elif 645 <= wavelength <= 780:
        R = 1.0
    factor = 1.0
    R = (R * factor) ** gamma
    G = (G * factor) ** gamma
    B = (B * factor) ** gamma
    return (R, G, B)

cmap = ListedColormap([wavelength_to_rgb(w) for w in wavelengths])

# --- Tracé ---
fig, ax = plt.subplots(figsize=(9, 5))

# Courbes photopique, scotopique et mésopiques intermédiaires
for a, col in zip(luminance_levels, colors):
    V_mesopic = a * V_photopic + (1 - a) * V_scotopic
    if a == 0:
        label= "Vision Nocturne"
    elif a ==1:
        label= "Vision Diurne"
    else:
        label = f"a={a:.2f}"
    ax.plot(wavelengths, V_mesopic, color=col, linewidth=2, label=label)

ax.set_title("Évolution de la sensibilité spectrale avec la luminance")
ax.set_xlabel("Longueur d’onde (nm)")
ax.set_ylabel("Sensibilité relative")
ax.grid(True, linestyle=":")
    
# --- Barre de couleur du spectre visible ---
extent = [wavelengths[0], wavelengths[-1], -0.08, -0.02]
ax.imshow(np.linspace(0, 1, len(wavelengths))[None, :],
          aspect="auto", extent=extent, cmap=cmap, origin="lower")

ax.set_xlim(380, 780)
ax.set_ylim(-0.1, 1.05)
plt.tight_layout()
plt.show()
