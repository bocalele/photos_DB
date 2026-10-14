from PIL import Image
import matplotlib.pyplot as plt
import numpy as np
from icc import getProfiles
import pandas as pd


def getCIExyz (RVB, M):
    XYZ = M@RVB
    return (XYZ/np.sum(XYZ, axis=0))

def getCIEXYZ (RVB, M):
    return (M@RVB)

def getRVB (xyz, M, norm=True):
    RVB = np.linalg.inv(M)@xyz
    if norm:
        return (255*RVB/np.max (RVB, axis=0))
    else:
        return (RVB)

def getCIE1931 ():
    cie = pd.read_csv ('./CIE_1931.csv', delimiter=';')
    cie['S'] = cie['x'] + cie['y'] + cie['z']
    cie['x'] /= cie['S']
    cie['y'] /= cie['S']
    return (cie)

def getTriangles (Matrices):
    RVB = np.array ([[255,0,0], [0, 255,0], [0, 0, 255]]).T
    triangles = {}
    for key, matrice in Matrices.items():
        triangles [key] = getCIExyz (RVB, matrice)
    return (triangles)



show = 2
cie = getCIE1931 ()


Matrices, wp = getProfiles ()
RVB = np.array ([[235, 10, 156], [156, 10, 235]]).T
# print (RVB.T, getCIExyz (RVB, Matrices['Imac']), getRVB (getCIExyz (RVB, Matrices['Imac']),  Matrices['Imac']))
# print (RVB.T, getCIEXYZ (RVB, Matrices['Imac']), getRVB (getCIEXYZ (RVB, Matrices['Imac']),  Matrices['Imac'], norm=False))

Matrices ['CIE'] = np.array ([0.49000, 0.17697, 0.00000, 0.31000, 0.81240, 0.01000, 0.20000, 0.01063, 0.99000]).reshape(3,3)
# print (f'{Matrices ['CIE'][0,0]:+5.3e}  {Matrices ['CIE'][0,1]:+5.3e}  {Matrices ['CIE'][0,2]:+5.3e}  \n\
# {Matrices ['CIE'][1,0]:+5.3e}  {Matrices ['CIE'][1,1]:+5.3e}  {Matrices ['CIE'][1,2]:+5.3e}  \n\
# {Matrices ['CIE'][2,0]:+5.3e}  {Matrices ['CIE'][2,1]:+5.3e}  {Matrices ['CIE'][2,2]:+5.3e}  \n')

xyz=cie[ ['x', 'y', 'z']].to_numpy().T
RVB = getRVB (xyz, Matrices['CIE'])/255
# print (RVB.shape)
min_coords = np.min(RVB, axis=1)
max_coords = np.max(RVB, axis=1)

print (min_coords, max_coords)
# for index, row in cie.iterrows():
#     print (f'{int(row['lambda'])} {RVB[0, index]:+12.9e} {RVB[1, index]:+12.9e} {RVB[2, index]:+12.9e} ')




triangles = getTriangles (Matrices)
if show ==1:
    from bokeh.plotting import figure, show
    from bokeh.palettes import *
    from bokeh.models import ColumnDataSource, Text
    from palette import wavelength_to_rgb_hex

    segment_colors = [wavelength_to_rgb_hex(l) for l in cie['lambda'].to_numpy()[:-1]]
    colors = Category10[10]
    f = figure(width=1200, height=800, title='Gamut')
    # f = figure(width=1200, height=800, title='Gamut', x_range=(0,1), y_range=(0,1))
    f.segment( x0=cie['x'].to_numpy()[:-1], y0=cie['y'].to_numpy()[:-1], 
               x1=cie['x'].to_numpy()[ 1:], y1=cie['y'].to_numpy()[ 1:], 
               line_width=3, line_color=segment_colors,legend_label='Oeil')
    # f.line (cie['x'], cie['y'], line_color='black', legend_label='Oeil')
    f.scatter (cie['x'][::10], cie['y'][::10], line_color='black', marker='circle', legend_label='Oeil')
    df = cie[cie['lambda'].isin([360,  480, 510, 540, 600, 820])]
    source = ColumnDataSource(dict(x= df['x'], y= df['y'], text= df['lambda']))
    glyph = Text(x="x", y="y", text="text", angle=0., text_color="#3d413f")
    f.add_glyph(source, glyph)
    iC = 0
    for key, triangle in triangles.items():
        if key in {'Imac', 'Cinema', 'AdobeRGB', 'P3', 'Generic','sRGB'}:
            f.line (x=[triangle[0, 0], triangle[0, 1]], y= [triangle[1, 0], triangle[1, 1]], line_color=colors[iC%10], legend_label= key, line_dash='dotdash')
            f.line (x=[triangle[0, 1], triangle[0, 2]], y= [triangle[1, 1], triangle[1, 2]], line_color=colors[iC%10], legend_label= key, line_dash='dotdash')
            f.line (x=[triangle[0, 0], triangle[0, 2]], y= [triangle[1, 0], triangle[1, 2]], line_color=colors[iC%10], legend_label= key, line_dash='dotdash')
            f.scatter (wp[key][0], wp[key][1], color=colors[iC%10], legend_label= key, marker='circle')
            iC += 1

    f.scatter (x = [0.3127], y=[0.329], legend_label='Blanc - 6504K', marker='circle', color='blue')
    f.scatter (x = [0.3457], y=[0.3595], legend_label='Blanc - 5003k', marker='circle', color='red')

    f.legend.click_policy='hide'
    show(f)

if show ==2:
    from PIL import Image

    base = np.array([255, 140, 0], dtype=np.float32)  # orange
    delta = np.array([50, 50, 50], dtype=np.float32)

    width, height = 256, 256
    gradient = np.clip(base + delta * np.linspace(-1, 1, width)[:, None], 0, 255)
    # print (gradient)
    # data_imac = np.tile(gradient[None, :, :], (height, 1, 1)).astype(np.uint8).reshape (-1,3).T
    data_imac = np.array ((width*height)*[235, 10, 156]).astype(np.uint8).reshape (-1,3).T

    data_cinema = np.round (getRVB (getCIEXYZ (data_imac, Matrices['Imac']),  Matrices['Cinema'], norm=False)).astype(np.uint8)
    print (data_imac[:,0])
    print (data_cinema[:,0])

    # data= np.concatenate ([data_imac.T.reshape (256, 256, 3), data_cinema.T.reshape (256, 256, 3)], axis=0)
    img = Image.fromarray(data_cinema.T.reshape(256, 256, 3), 'RGB')
    # img = Image.fromarray(data_imac.T.reshape(256, 256, 3), 'RGB')
    # img = Image.fromarray(data.T.reshape(512, 256, 3), 'RGB')

    img.show() 

if show ==3:
    from bokeh.plotting import figure, show
    f = figure(width=1200, height=800, title='CIE 1931')

    f.line(cie[ 'lambda'].to_numpy(), RVB[0, :], line_color='red')
    f.line(cie[ 'lambda'].to_numpy(), RVB[1, :], line_color='green')
    f.line(cie[ 'lambda'].to_numpy(), RVB[2, :], line_color='blue')
    f.scatter (700, 0, color='red', marker='circle', size=10)
    f.scatter (546.1, 0, color='green', marker='circle', size=10)
    f.scatter (435.8, 0, color='blue', marker='circle', size=10)
    show (f)

if show == 4:
    import plotly.graph_objects as go
    import panel as pn
    from palette import wavelength_to_rgb_hex

    spectral_colors = [wavelength_to_rgb_hex(l) for l in cie[ 'lambda'].to_numpy()]


    
    fig = go.Figure(data=[
        go.Scatter3d(x= RVB[0, :], y= RVB[1, :],z= RVB[2, :], mode='lines',
            line=dict( color=spectral_colors,width=5))
        ])

    fig.update_layout( title='Courbe des longueurs d\'ondes en fonction des RVB',
                    scene=dict(xaxis_title='R', yaxis_title='V', zaxis_title='B',aspectmode='cube'),
                    height=600)


    pn.serve(pn.panel(fig), port=5006)
