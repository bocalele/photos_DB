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

def wavelength_to_rgb_hex(wavelength):
    """
    Convertit une longueur d'onde (en nm) en code couleur RVB Hex.
    (Approximation basée sur le code de John Walker)
    """
    gamma = 0.8
    intensity_max = 255
    R, G, B = 0, 0, 0

    if wavelength >= 380 and wavelength <= 440:
        attenuation = 0.3 + 0.7 * (wavelength - 380) / (440 - 380)
        R = ((-(wavelength - 440) / (440 - 380)) * attenuation) ** gamma
        B = attenuation ** gamma
    elif wavelength >= 440 and wavelength <= 490:
        R = 0.0
        G = ((wavelength - 440) / (490 - 440)) ** gamma
        B = 1.0
    elif wavelength >= 490 and wavelength <= 510:
        R = 0.0
        G = 1.0
        B = (-(wavelength - 510) / (510 - 490)) ** gamma
    elif wavelength >= 510 and wavelength <= 580:
        R = ((wavelength - 510) / (580 - 510)) ** gamma
        G = 1.0
        B = 0.0
    elif wavelength >= 580 and wavelength <= 645:
        R = 1.0
        G = (-(wavelength - 645) / (645 - 580)) ** gamma
        B = 0.0
    elif wavelength >= 645 and wavelength <= 780:
        attenuation = 0.3 + 0.7 * (780 - wavelength) / (780 - 645)
        R = attenuation ** gamma
        G = 0.0
        B = 0.0
    else:
        # Longueur d'onde en dehors du spectre visible
        R, G, B = 0.0, 0.0, 0.0

    R = int(R * intensity_max)
    G = int(G * intensity_max)
    B = int(B * intensity_max)

    # Conversion en format hexadécimal
    return f'#{R:02x}{G:02x}{B:02x}'