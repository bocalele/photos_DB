import numpy as np
import matplotlib.pyplot as plt

# Plage de longueurs d'onde
wavelength = np.linspace(400, 700, 300)

# --- Sensibilités Bayer (approximation gaussienne) ---
def gaussian(x, mu, sigma):
    return np.exp(-0.5 * ((x - mu) / sigma)**2)

blue_bayer = gaussian(wavelength, 450, 30)    # Bleu Bayer
green_bayer = gaussian(wavelength, 550, 40)   # Vert Bayer
red_bayer = gaussian(wavelength, 620, 50)     # Rouge Bayer

# --- Sensibilités des cônes humains ---
blue_cone = gaussian(wavelength, 430, 20)     # S-cone (bleu)
green_cone = gaussian(wavelength, 540, 30)    # M-cone (vert)
red_cone = gaussian(wavelength, 570, 40)      # L-cone (rouge)

# Normalisation pour que le pic soit 1
blue_bayer /= max(blue_bayer)
green_bayer /= max(green_bayer)
red_bayer /= max(red_bayer)
blue_cone /= max(blue_cone)
green_cone /= max(green_cone)
red_cone /= max(red_cone)

# --- Tracé ---
plt.figure(figsize=(10,6))

# Courbes Bayer
plt.plot(wavelength, blue_bayer, color='blue', linestyle='-', label='Bayer Bleu')
plt.plot(wavelength, green_bayer, color='green', linestyle='-', label='Bayer Vert')
plt.plot(wavelength, red_bayer, color='red', linestyle='-', label='Bayer Rouge')

# Courbes cônes
plt.plot(wavelength, blue_cone, color='blue', linestyle='--', label='Cône Bleu (S)')
plt.plot(wavelength, green_cone, color='green', linestyle='--', label='Cône Vert (M)')
plt.plot(wavelength, red_cone, color='red', linestyle='--', label='Cône Rouge (L)')

plt.title("Comparaison des sensibilités spectrales : filtres Bayer vs cônes humains")
plt.xlabel("Longueur d'onde (nm)")
plt.ylabel("Sensibilité relative")
plt.ylim(0, 1.1)
plt.grid(True)
plt.legend()
plt.show()
