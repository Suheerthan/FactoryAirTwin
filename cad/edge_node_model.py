"""FactoryAir Edge Node — parametric 3D model (mm).

Builds the enclosure assembly from real part dimensions (IP65 box, 35 mm DIN
rail, Mean Well HDR-15-24 supply, ESP32-S3 DevKitC, ADS1115, microSD, LM2596
buck, PG7 glands, G1/4 tee + ball valve + 4-20 mA transmitter), then:

* renders an isometric view and an exploded view (PNG)
* exports the assembly as a single STL mesh

Run:  python cad/edge_node_model.py
"""
from math import cos, pi, sin
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

OUT = Path(__file__).resolve().parent

# ----------------------------------------------------------------- geometry
def rounded_rect(w, d, r, n=6):
    pts = []
    for cx, cy, a0 in ((w/2-r, d/2-r, 0), (-w/2+r, d/2-r, 90), (-w/2+r, -d/2+r, 180), (w/2-r, -d/2+r, 270)):
        for k in range(n + 1):
            a = (a0 + 90 * k / n) * pi / 180
            pts.append((cx + r * cos(a), cy + r * sin(a)))
    return pts

def rect(w, d):
    return [(w/2, d/2), (-w/2, d/2), (-w/2, -d/2), (w/2, -d/2)]

def circle(dia, n=28):
    return [(dia/2 * cos(2*pi*k/n), dia/2 * sin(2*pi*k/n)) for k in range(n)]

def prism(poly, z0, z1, top=True, bottom=True):
    faces = []
    m = len(poly)
    for i in range(m):
        (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % m]
        faces.append([(x0, y0, z0), (x1, y1, z0), (x1, y1, z1), (x0, y0, z1)])
    if top:
        faces.append([(x, y, z1) for x, y in poly])
    if bottom:
        faces.append([(x, y, z0) for x, y in poly][::-1])
    return faces

def ring(outer, inner, z):
    return [[(*outer[i], z), (*outer[(i+1) % len(outer)], z), (*inner[(i+1) % len(inner)], z), (*inner[i], z)]
            for i in range(len(outer))]

def move(faces, dx=0, dy=0, dz=0):
    return [[(x + dx, y + dy, z + dz) for x, y, z in f] for f in faces]

def y_cylinder(dia, length, n=24):
    """Cylinder along -Y (cable glands)."""
    faces = []
    for k in range(n):
        a0, a1 = 2*pi*k/n, 2*pi*(k+1)/n
        p0 = (dia/2*cos(a0), dia/2*sin(a0)); p1 = (dia/2*cos(a1), dia/2*sin(a1))
        faces.append([(p0[0], 0, p0[1]), (p1[0], 0, p1[1]), (p1[0], -length, p1[1]), (p0[0], -length, p0[1])])
    faces.append([(dia/2*cos(2*pi*k/n), -length, dia/2*sin(2*pi*k/n)) for k in range(n)])
    return faces

# ----------------------------------------------------------------- parts
W, D, H, T, R = 200, 150, 75, 3, 8
outer, inner = rounded_rect(W, D, R), rounded_rect(W - 2*T, D - 2*T, R - T)

def box_body():
    f = prism(outer, 0, H, top=False)            # outer walls + bottom
    f += [fc[::-1] for fc in prism(inner, T, H, top=False, bottom=False)]
    f += [[(x, y, T) for x, y in inner]]          # inner floor
    f += ring(outer, inner, H)                    # rim
    return f

def din_rail():
    """Top-hat channel: 35 mm top plate on two 1 mm side walls, 7.5 mm tall."""
    wall = prism(rect(180, 1), 0, 6.5)
    return prism(rect(180, 35), 6.5, 7.5) + move(wall, dy=17) + move(wall, dy=-17)

PARTS = [
    # name, faces, colour, position (x, y, z), explode offset (x, y, z)
    ("Enclosure (PC/ABS IP65)", box_body(), "#d9dee0", (0, 0, 0), (0, 0, 0)),
    ("Lid", prism(outer, 0, 10), "#e9edee", (0, 0, H), (0, 0, 120)),
    ("DIN rail 35 mm", din_rail(), "#a8b3b7", (0, 25, T), (0, 0, 25)),
    ("24 V PSU (HDR-15-24)", prism(rect(17.5, 90), 0, 54.5), "#2f6fb0", (-75, 25, 10.5), (0, 0, 70)),
    ("Carrier board", prism(rect(110, 70), 0, 1.6), "#2e8b57", (30, -30, 13), (0, 0, 45)),
    ("ESP32-S3", prism(rect(69, 25.5), 0, 10), "#1f2a30", (10, -40, 14.6), (0, 0, 80)),
    ("ADS1115 ADC", prism(rect(28, 17), 0, 3), "#3b4fa0", (65, -5, 14.6), (0, 0, 80)),
    ("microSD", prism(rect(24, 42), 0, 3), "#1f2a30", (70, -40, 14.6), (0, 0, 80)),
    ("5 V buck", prism(rect(43, 21), 0, 14), "#2d5f8a", (5, -12, 14.6), (0, 0, 80)),
]
for k, x in enumerate((-50, 0, 50)):
    PARTS.append((f"PG7 gland {k+1}", y_cylinder(15, 20), "#222b2e", (x, -D/2, 30), (0, -45, 0)))
for (sx, sy) in ((-20, -60), (80, -60), (-20, 0), (80, 0)):
    PARTS.append(("Standoff", prism(circle(6, 12), 0, 10), "#c9a227", (sx, sy, T), (0, 0, 45)))
TAP_X = 175
PARTS += [
    ("G1/4 tee", prism(circle(16), 0, 30), "#b8913a", (TAP_X, 0, 0), (40, 0, 0)),
    ("Ball valve", prism(circle(22), 0, 40), "#c9a227", (TAP_X, 0, 30), (40, 0, 25)),
    ("Valve handle", prism(rect(50, 8), 0, 6), "#c0392b", (TAP_X, 0, 50), (40, 0, 25)),
    ("4-20 mA transmitter", prism(circle(27), 0, 80), "#9aa5a9", (TAP_X, 0, 70), (40, 0, 55)),
]

def assembled(exploded=False, hide=()):
    out = []
    for name, faces, colour, (x, y, z), (ex, ey, ez) in PARTS:
        if name in hide:
            continue
        k = 1 if exploded else 0
        out.append((name, move(faces, x + k*ex, y + k*ey, z + k*ez), colour))
    return out

# ----------------------------------------------------------------- render
LIGHT = np.array([0.35, -0.55, 0.75]); LIGHT = LIGHT / np.linalg.norm(LIGHT)

def shade(face, colour):
    p = np.array(face, dtype=float)
    nrm = np.cross(p[1] - p[0], p[2] - p[0])
    if np.linalg.norm(nrm) < 1e-9 and len(p) > 3:
        nrm = np.cross(p[2] - p[0], p[3] - p[0])
    nrm = nrm / (np.linalg.norm(nrm) + 1e-12)
    rgb = np.array(matplotlib.colors.to_rgb(colour))
    return np.clip(rgb * (0.55 + 0.45 * abs(nrm @ LIGHT)), 0, 1)

def render(parts, path, title, labels=None, elev=26, azim=-58, xlim=(-115, 205), ylim=(-140, 90), zlim=(0, 215)):
    fig = plt.figure(figsize=(10, 4.4), dpi=220)
    ax = fig.add_axes([0, 0, 1, 1], projection="3d")
    polys, cols = [], []
    for _, faces, colour in parts:
        for f in faces:
            polys.append(f); cols.append(shade(f, colour))
    pc = Poly3DCollection(polys, facecolors=cols, edgecolors=(0.12, 0.16, 0.18, 0.55), linewidths=0.25)
    ax.add_collection3d(pc)
    ax.set_xlim(*xlim); ax.set_ylim(*ylim); ax.set_zlim(*zlim)
    ax.set_box_aspect((xlim[1]-xlim[0], ylim[1]-ylim[0], zlim[1]-zlim[0]))
    ax.view_init(elev=elev, azim=azim)
    ax.set_axis_off()
    for text, xyz in (labels or []):
        ax.text(*xyz, text, fontsize=7.5, color="#0f2730", ha="center",
                bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="#d9dee0", lw=0.5))
    fig.savefig(path, facecolor="white")
    plt.close(fig)

def export_stl(parts, path):
    with open(path, "w", encoding="ascii") as fh:
        fh.write("solid factoryair_edge_node\n")
        for _, faces, _ in parts:
            for f in faces:
                for i in range(1, len(f) - 1):     # fan-triangulate each polygon
                    a, b, c = np.array(f[0]), np.array(f[i]), np.array(f[i + 1])
                    n = np.cross(b - a, c - a); n = n / (np.linalg.norm(n) + 1e-12)
                    fh.write(f" facet normal {n[0]:.4f} {n[1]:.4f} {n[2]:.4f}\n  outer loop\n")
                    for v in (a, b, c):
                        fh.write(f"   vertex {v[0]:.3f} {v[1]:.3f} {v[2]:.3f}\n")
                    fh.write("  endloop\n endfacet\n")
        fh.write("endsolid factoryair_edge_node\n")

def autocrop(path, pad=18):
    from PIL import Image, ImageChops
    im = Image.open(path).convert("RGB")
    box = ImageChops.difference(im, Image.new("RGB", im.size, "white")).getbbox()
    l, t, r, b = box
    return im.crop((max(0, l - pad), max(0, t - pad), min(im.width, r + pad), min(im.height, b + pad)))


def compose_sheet(iso, exp, out, aspect=7.5 / 1.6):
    """Both views side by side with small headers, shaped for the slide's CAD box."""
    from PIL import Image, ImageDraw, ImageFont
    a, b = autocrop(iso), autocrop(exp)
    h = 900
    a = a.resize((int(a.width * h / a.height), h)); b = b.resize((int(b.width * h / b.height), h))
    head = 70
    width = max(int((h + head) * aspect), a.width + b.width + 120)
    sheet = Image.new("RGB", (width, h + head), "white")
    gap = (width - a.width - b.width) // 3
    sheet.paste(a, (gap, head)); sheet.paste(b, (2 * gap + a.width, head))
    d = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("bahnschrift.ttf", 44)
    except OSError:
        font = ImageFont.load_default()
    d.text((gap, 10), "Isometric (lid removed)", fill="#0f2730", font=font)
    d.text((2 * gap + a.width, 10), "Exploded view", fill="#0f2730", font=font)
    sheet.save(out)


if __name__ == "__main__":
    render(assembled(hide=("Lid",)), OUT / "cad_isometric.png", "Assembly — isometric (lid removed)", elev=48,
           labels=[("24 V PSU", (-75, 25, 75)), ("ESP32-S3 + ADC", (30, -35, 38)), ("DIN rail", (40, 40, 20)),
                   ("PG7 glands", (0, -112, 52)), ("Pressure tap", (TAP_X, 0, 170))])
    render(assembled(exploded=True), OUT / "cad_exploded.png", "Assembly — exploded view",
           labels=[("Lid", (0, 0, 212)), ("Modules", (40, -30, 120)), ("Transmitter", (TAP_X + 40, 0, 215)),
                   ("Glands", (0, -150, 50))], elev=30, zlim=(0, 225))
    export_stl(assembled(), OUT / "factoryair_edge_node.stl")
    compose_sheet(OUT / "cad_isometric.png", OUT / "cad_exploded.png", OUT / "cad_sheet.png")
    print("wrote cad_isometric.png, cad_exploded.png, cad_sheet.png, factoryair_edge_node.stl")
