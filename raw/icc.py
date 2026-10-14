import numpy as np
from PIL import ImageCms



import struct
import numpy as np

def read_icc_xyz_tags(path):
    with open(path, "rb") as f:
        data = f.read()

    tag_count = struct.unpack_from(">I", data, 128)[0]
    tags = {}
    for i in range(tag_count):
        sig, offset, size = struct.unpack_from(">4sII", data, 132 + i*12)
        tags[sig.decode()] = (offset, size)

    def read_xyz(tag):
        offset, _ = tags[tag]
        type_sig = data[offset:offset+4].decode()
        if type_sig != "XYZ ":
            raise ValueError(f"{tag} n'est pas de type XYZ mais {type_sig}")
        vals = struct.unpack_from(">3i", data, offset + 8)
        return [v / 65536.0 for v in vals]

    rXYZ = read_xyz("rXYZ")
    gXYZ = read_xyz("gXYZ")
    bXYZ = read_xyz("bXYZ")
    wtpt = read_xyz("wtpt")
    M = np.column_stack([rXYZ, gXYZ, bXYZ])
    return M, wtpt/np.sum(wtpt)

def getProfiles ():
    iccs = {
    'Imac':'/Library/ColorSync/Profiles/Displays/iMac-CADD2DC0-EE21-62B7-66A0-2F18531DF6C8.icc',
    'Cinema':'/Library/ColorSync/Profiles/Displays/Cinema HD-7A313A3F-4CC1-EFC7-4703-7F9D7C8832C7.icc',
    'AdobeRGB':'/System/Library/ColorSync/Profiles/AdobeRGB1998.icc',
    'P3':'/System/Library/ColorSync/Profiles/Display P3.icc',
    'Generic':'/System/Library/ColorSync/Profiles/Generic RGB Profile.icc',
    'ITU2020':'/System/Library/ColorSync/Profiles/ITU-2020.icc',
    'ITU':'/System/Library/ColorSync/Profiles/ITU-709.icc',
    'ROMM':'/System/Library/ColorSync/Profiles/ROMM RGB.icc',
    'SMPTE':'/System/Library/ColorSync/Profiles/DCI(P3) RGB.icc',
    'sRGB':'/System/Library/ColorSync/Profiles/sRGB Profile.icc'
    }
    M, wp = {}, {}
    for k, icc in iccs.items():
        M [k], wp[k] = read_icc_xyz_tags (icc)
    return (M, wp)


if __name__ == "__main__":
    icc_path = "/System/Library/ColorSync/Profiles/Display P3.icc"  # chemin vers ton profil ICC
    M, white_point = read_icc_xyz_tags(icc_path)
    print("Matrice RGB→XYZ :")
    print(M)
    print("Point blanc (XYZ) :")
    print(white_point)

    M, w = getProfiles()
    print (M)