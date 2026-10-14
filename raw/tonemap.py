import numpy as np 
def reinhard (rgb):
    return (rgb / (1+rgb))

def s_curve (rgb,a=5):
    
    print (np.min(rgb), np.max (rgb), np.median(rgb), np.percentile (rgb, 99.9))
    rgb /= np.max (rgb)
    seuil = 2*np.median(rgb)
    #seuil = .5*(np.max (rgb) - np.min (rgb))
    rgb -= seuil
    rgb *=a
    print (np.min(rgb), np.max (rgb), np.median(rgb), np.percentile (rgb, 99.9))

    rgb = 0.5 * (np.tanh(rgb) + 1)
    rgb = (rgb - rgb.min())/(rgb.max() - rgb.min())
    print (np.min(rgb), np.max (rgb), np.median(rgb), np.percentile (rgb, 99.9))

    return (rgb)
    #return (0.5 * (np.tanh((rgb-.5)*a)) + 1)

def adaptive_s_curve(rgb, a_base=5, a_min=2, a_max=20):
    """
    S-curve adaptative selon le contraste global de l'image.
    
    rgb : image normalisée [0,1]
    a_base : valeur de référence
    a_min / a_max : bornes de l’adaptation
    """
    # Mesure du contraste global (écart-type sur luminance)
    luminance = 0.2126*rgb[...,0] + 0.7152*rgb[...,1] + 0.0722*rgb[...,2]
    contrast = np.std(luminance)

    # Ajustement dynamique du paramètre a
    # On suppose qu’un contraste ~0.2 est "moyen"
    a = a_base * (0.2 / (contrast + 1e-6))
    print (a)
    a = np.clip(a, a_min, a_max)

    seuil = np.median(rgb)
    seuil = np.median(luminance)
    seuil = 2*np.median(rgb)

    #seuil = .5*(np.max (rgb) - np.min (rgb))
    rgb -= seuil
    # Application de la S-curve classique
    sc = 0.5 * (np.tanh((rgb) * a) + 1)

    # Renormalisation du résultat dans [0,1]
    sc = (sc - sc.min()) / (sc.max() - sc.min())

    print(f"Contraste mesuré = {contrast:.3f}, facteur a = {a:.2f}")
    return sc