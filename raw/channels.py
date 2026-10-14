import rawpy

import numpy as np
import matplotlib.pyplot as plt
from demosaic import bi_linear
from tonemap import reinhard, s_curve, adaptive_s_curve

file = "/Users/pc/PHOTOS/2025/Angleterre/Raw/DSC_9096.NEF"
BlackLevel = True
WhiteBalance = True
Algo = 'BiLinear'
Tone='Reinhard'
#Tone='S_Curve'
Tone='AS_Curve'

Gamma = True

with rawpy.imread(file) as raw:
    raw_data = raw.raw_image_visible.astype(np.float32)
    pattern = raw.raw_pattern
    colors = raw.color_desc.decode('ascii')
    wb = raw.camera_whitebalance     #dans le cas du Z7, le tag est le memem que l'export rawpy
    h, w = raw_data.shape

    print(f'Pattern Bayer classique   : {colors:4}')
    print(f'Stockage Z7 + Balance     : \n\
          {colors[pattern[0,0]]:2} ({wb[0]:12.9e}) {colors[pattern[0,1]]:2} ({wb[1]:12.9e})\n\
          {colors[pattern[1,0]]:2} ({wb[3]:12.9e}) {colors[pattern[1,1]]:2} ({wb[2]:12.9e})\n')
    print (f'Nombre de Pixels height {h:6} width {w:6}')
    print (f'Black Level', raw.black_level_per_channel)
    print (f'White Level', raw.white_level)

    #Traitement du Bruit: BlackLevel
    #SUr le Z7, le BlackLevel est le meme sur tous les cannaux 
    if BlackLevel:
        norm = raw.white_level-raw.black_level_per_channel[0]
        raw_data = (raw_data-raw.black_level_per_channel[0])/norm
    else:
        raw_data /= np.max (raw_data)

    # Balance des blancs
    rgb = np.zeros((h, w, 3), dtype=np.float32)
    R=0; G=1; B=2
    if WhiteBalance:
        rgb[0::2, 0::2, R] = raw_data[0::2, 0::2] * wb[0]
        rgb[0::2, 1::2, G] = raw_data[0::2, 1::2] * wb[1]
        rgb[1::2, 0::2, G] = raw_data[1::2, 0::2] * wb[3]
        rgb[1::2, 1::2, B] = raw_data[1::2, 1::2] * wb[2]
    else:
        rgb[0::2, 0::2, R] = raw_data[0::2, 0::2]  
        rgb[0::2, 1::2, G] = raw_data[0::2, 1::2]  
        rgb[1::2, 0::2, G] = raw_data[1::2, 0::2]  
        rgb[1::2, 1::2, B] = raw_data[1::2, 1::2]  


    if Algo == 'BiLinear':
        rgb = bi_linear (rgb)
    
    ev = 1.3
    if Tone == "Reinhard":
        rgb = reinhard (rgb*2**ev)
    if Tone == "S_Curve":
        rgb = s_curve (rgb,5)
    if Tone == "AS_Curve":
        rgb = adaptive_s_curve (rgb)

    if Gamma:
        gamma = 2.2
        rgb = np.clip(rgb, 0, 1) ** (1/gamma)



plt.figure(figsize=(10,10))
plt.imshow(rgb); 
plt.show()



# plt.figure(figsize=(25,10))
# plt.subplot(1,3,1); plt.imshow(np.clip (rgb[:,:,0], 0, 1), cmap='Reds'); 
# plt.subplot(1,3,2); plt.imshow(np.clip (rgb[:,:,1], 0, 1), cmap='Greens'); 
# plt.subplot(1,3,3); plt.imshow(np.clip (rgb[:,:,2], 0, 1), cmap='Blues'); 

# plt.show()


# plt.figure(figsize=(15,15))
# plt.subplot(2,2,1); plt.imshow(norm_channel(raw_data[0::2,0::2]), cmap='Reds'); plt.title('Rouge')
# plt.subplot(2,2,2); plt.imshow(norm_channel(raw_data[0::2,1::2]), cmap='Greens'); plt.title('Vert ligne 0')
# plt.subplot(2,2,3); plt.imshow(norm_channel(raw_data[1::2,0::2]), cmap='Greens'); plt.title('Vert ligne 1')
# plt.subplot(2,2,4); plt.imshow(norm_channel(raw_data[1::2,1::2]), cmap='Blues'); plt.title('Bleu')
# plt.tight_layout()
# plt.show()
