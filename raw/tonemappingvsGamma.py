import numpy as np
import matplotlib.pyplot as plt

# Création d'une gamme de luminances linéaires (0 à 1)
x = np.linspace(0, 1, 500)

# Correction gamma (sRGB ~ 1/2.2)
gamma = 1/2.2
y_gamma = x ** gamma

# Tone mapping simple (ex: courbe de Reinhard)
y_tonemap = x / (x + 0.5)

# Tone mapping + gamma combinés
y_combined = (x / (x + 0.5)) ** gamma

# Tracé
plt.figure(figsize=(8,6))
plt.plot(x, y_gamma, label="Correction gamma (γ=2.2)")
plt.plot(x, y_tonemap, label="Tone mapping (Reinhard)")
plt.plot(x, y_combined, label="Tone mapping + gamma", linestyle='--', color='black')
plt.title("Différence entre tone mapping et correction gamma")
plt.xlabel("Luminance d'entrée (linéaire)")
plt.ylabel("Valeur affichée")
plt.legend()
plt.grid(True)
plt.show()
