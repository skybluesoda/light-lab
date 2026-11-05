"""Geometry-only wave ceiling construction, usable without Blender for checks."""
import math


def wave_ceiling_mesh(width, front, back, base_height, amplitude=.22, wavelength=3.0,
                      thickness=.18, nx=192, ny=32):
    vertices = []
    for layer in range(2):
        for j in range(ny+1):
            y = front + (back-front)*j/ny
            for i in range(nx+1):
                x = -width/2 + width*i/nx
                z = base_height + amplitude*math.sin(2*math.pi*x/wavelength)
                vertices.append((x,y,z+layer*thickness))
    stride = nx+1
    layer_size = stride*(ny+1)
    faces = []
    for j in range(ny):
        for i in range(nx):
            a=j*stride+i; b=a+1; d=(j+1)*stride+i; c=d+1
            faces.append((a,d,c,b))  # Outward normal down, visible room surface.
            faces.append((a+layer_size,b+layer_size,c+layer_size,d+layer_size))
    boundary = list(range(nx+1))
    boundary += [j*stride+nx for j in range(1,ny+1)]
    boundary += [ny*stride+i for i in range(nx-1,-1,-1)]
    boundary += [j*stride for j in range(ny-1,0,-1)]
    for a,b in zip(boundary,boundary[1:]+boundary[:1]):
        faces.append((a,b,b+layer_size,a+layer_size))
    return vertices,faces
