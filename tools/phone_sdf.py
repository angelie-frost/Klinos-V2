"""
CPU prototype of the procedural photoreal path.

Everything here is written the way a fragment shader is written — per pixel,
vectorised, no meshes — so it ports to GLSL almost line for line. Building it on
the CPU first means the result can actually be looked at before any of it goes
somewhere it can only fail silently.

Geometry is a signed distance field: a rounded-rect prism with a rounded edge,
which gives the chamfer where the rail meets the glass. Lighting is a generated
studio environment — a dark surround with a few soft bright rectangles — so
there is no HDRI to ship. Materials are GGX metal for the rail and fresnel glass
over the screen.
"""
import numpy as np


# --------------------------------------------------------------------- maths --

def norm(v):
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-9)


def dot(a, b):
    return np.sum(a * b, axis=-1, keepdims=True)


def rot_matrix(rx, ry, rz):
    x, y, z = np.radians([rx, ry, rz])
    cx, sx, cy, sy, cz, sz = np.cos(x), np.sin(x), np.cos(y), np.sin(y), np.cos(z), np.sin(z)
    return np.array([
        [cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx],
        [sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx],
        [-sy,     cy * sx,                cy * cx]])


def smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a + 1e-9), 0.0, 1.0)
    return t * t * (3 - 2 * t)


# ------------------------------------------------------------------ geometry --

# Millimetres, iPhone 13 sized.
W, H, D = 71.5, 146.7, 7.65
CORNER = 12.0          # outline radius
EDGE = 1.15            # rail chamfer — small, and most of what reads as real
BEZEL = 2.4
SCREEN_R = 9.6
BUMP_W, BUMP_H, BUMP_D = 34.0, 34.0, 1.9
BUMP_R, BUMP_OX, BUMP_OY = 8.0, -14.0, 48.0


def sd_rrect(p2, half, r):
    q = np.abs(p2) - half + r
    return (np.minimum(np.maximum(q[..., 0], q[..., 1]), 0.0)
            + np.linalg.norm(np.maximum(q, 0.0), axis=-1) - r)


def sd_prism(p, half_wh, corner, half_d, edge):
    """Rounded-rectangle prism with rounded edges. The 2D outline distance is
    combined with the slab distance, then offset — that offset is the chamfer."""
    d2 = sd_rrect(p[..., :2], np.array(half_wh) - edge, max(corner - edge, 0.01))
    wz = np.abs(p[..., 2]) - (half_d - edge)
    a = np.maximum(d2, wz)
    outside = np.stack([np.maximum(d2, 0.0), np.maximum(wz, 0.0)], axis=-1)
    return np.minimum(a, 0.0) + np.linalg.norm(outside, axis=-1) - edge


# Side hardware, iPhone 13 layout. y measured up from the device centre.
# (side, centre_y, half_length). Lengths off the iPhone 15 Pro: action button
# short, volume pair long and closely spaced, side button longest.
BUTTONS = [
    (-1, 46.5, 4.0),    # action button
    (-1, 33.5, 6.0),    # volume up
    (-1, 19.5, 6.0),    # volume down
    (+1, 28.0, 12.5),   # side button
]
BTN_HZ = 1.5      # half depth across the rail
BTN_HX = 0.55     # half thickness; the rail surface cuts through this
BTN_OUT = 0.15    # how far the centre sits outside the rail
# Bottom edge hardware, measured off a real device. Left to right the edge runs
# grille / pentalobe screw / USB-C / pentalobe screw / grille, so it is symmetric
# about x and the domain can still fold on |x|.
PORT_HW, PORT_HZ, PORT_DEPTH = 4.25, 1.30, 5.0     # USB-C is 8.5 x 2.6mm
SCREW_X, SCREW_R, SCREW_DEPTH = 7.3, 0.65, 0.35    # shallow dish, not a hole
HOLE_R, HOLE_SP, HOLE_N, HOLE_X0, HOLE_DEPTH = 0.48, 2.05, 6.0, 9.7, 1.2


def sd_buttons(p):
    """Stadium profile, not a rounded rectangle.

    On the real device the button outline on the rail face is a pill: the ends
    are exact semicircles of the button's own half-depth. Building it as a
    rounded rect with a small corner radius, which is what this did before,
    reads as a slab. The trick is to put the rounded rectangle in the y-z plane
    and extrude along x, with the corner radius equal to the half depth.
    """
    d = None
    for side, cy, half_len in BUTTONS:
        q = p.copy()
        q[..., 0] -= side * (W / 2 - BTN_OUT)
        q[..., 1] -= cy
        yzx = np.stack([q[..., 1], q[..., 2], q[..., 0]], axis=-1)
        b = sd_prism(yzx, (half_len, BTN_HZ), BTN_HZ, BTN_HX, 0.18)
        d = b if d is None else np.minimum(d, b)
    return d


def _finite_y(y, depth):
    """Extent of a cut along y: from `depth` up inside the body to 0.3 below the
    bottom face, so the mouth always opens cleanly through it."""
    yc = -H / 2 + (depth - 0.3) / 2
    return np.abs(y - yc) - (depth + 0.3) / 2


def _cyl(radial, axial):
    return (np.minimum(np.maximum(radial, axial), 0.0)
            + np.hypot(np.maximum(radial, 0.0), np.maximum(axial, 0.0)))


def bottom_cuts(p):
    """Port, screws and grille, subtracted from the bottom face.

    Returns (cut, radial). `radial` is the distance in the plane of the face;
    because every cut here is prismatic that is exactly the local aperture, which
    is what lets cavity_vis be analytic.

    Two things changed from the first version. The holes were spheres centred on
    the face, which cut a shallow dish rather than a hole — they are finite
    cylinders now, so they have depth. And the row was bounded by masking the
    range to 1e3 outside it, which is a cliff in the field that the raymarcher
    can step straight through; clamping the cell index instead keeps it
    continuous at the ends.
    """
    x, y, z = p[..., 0], p[..., 1], p[..., 2]
    hx = np.abs(x)

    port_r = sd_rrect(np.stack([x, z], -1), np.array([PORT_HW, PORT_HZ]), PORT_HZ)
    port = _cyl(port_r, _finite_y(y, PORT_DEPTH))

    i = np.clip(np.floor((hx - HOLE_X0) / HOLE_SP + 0.5), 0.0, HOLE_N - 1.0)
    hole_r = np.hypot(hx - (HOLE_X0 + i * HOLE_SP), z) - HOLE_R
    holes = _cyl(hole_r, _finite_y(y, HOLE_DEPTH))

    screw_r = np.hypot(hx - SCREW_X, z) - SCREW_R
    screws = _cyl(screw_r, _finite_y(y, SCREW_DEPTH))

    return (np.minimum(np.minimum(port, holes), screws),
            np.minimum(np.minimum(port_r, hole_r), screw_r))


def sd_bottom_cuts(p):
    return bottom_cuts(p)[0]


def cavity_vis(p):
    """Fraction of the hemisphere a point inside a cut can still see.

    Nothing else in this renderer occludes anything, so without this a 5mm deep
    USB-C socket is lit exactly like an exposed face and reads as a bright plug
    rather than a hole. The solid angle of an aperture of half-width `ap` seen
    from depth `h` falls off as ap^2/(ap^2+h^2), and for these prismatic cuts
    `ap` is just the in-plane distance, so it costs no extra field evaluations.

    Generic SDF ambient occlusion cannot express this: it probes for geometry
    near the shaded point, but what darkens a socket is depth through a narrow
    mouth some distance away.
    """
    cut, radial = bottom_cuts(p)
    ap = np.maximum(-radial, 0.02)
    depth = np.maximum(p[..., 1] + H / 2, 0.0)
    return np.where(cut > 0.05, 1.0,
                    np.clip(ap * ap / (ap * ap + depth * depth), 0.0, 1.0))


def sdf(p):
    body = sd_prism(p, (W / 2, H / 2), CORNER, D / 2, EDGE)
    q = p.copy()
    q[..., 0] -= BUMP_OX
    q[..., 1] -= BUMP_OY
    q[..., 2] += D / 2 + BUMP_D / 2 - 0.4
    bump = sd_prism(q, (BUMP_W / 2, BUMP_H / 2), BUMP_R, BUMP_D / 2, 0.8)
    d = np.minimum(np.minimum(body, bump), sd_buttons(p))
    return np.maximum(d, -sd_bottom_cuts(p))


def normal_at(p, eps=0.035):
    e = np.array([eps, 0.0, 0.0])
    dx = sdf(p + e) - sdf(p - e)
    dy = sdf(p + e[[1, 0, 2]]) - sdf(p - e[[1, 0, 2]])
    dz = sdf(p + e[[1, 2, 0]]) - sdf(p - e[[1, 2, 0]])
    return norm(np.stack([dx, dy, dz], axis=-1))


# --------------------------------------------------------------- environment --

class Softbox:
    def __init__(self, direction, half_u, half_v, colour, intensity, softness=0.35):
        self.axis = norm(np.array(direction, float))
        up = np.array([0.0, 1.0, 0.0])
        if abs(self.axis @ up) > 0.95:
            up = np.array([1.0, 0.0, 0.0])
        self.t = norm(np.cross(up, self.axis))
        self.b = np.cross(self.axis, self.t)
        self.hu, self.hv = half_u, half_v
        self.colour = np.array(colour, float)
        self.intensity = intensity
        self.softness = softness


# A dark studio with a big key above and in front, a dim fill from the right and
# a rim from behind. This is the whole "HDRI", and it costs nothing to ship.
SOFTBOXES = [
    # key: large, above and in front
    Softbox((-0.34, 0.70, 0.63), 0.66, 0.46, (1.00, 0.98, 0.95), 5.2, 0.40),
    # fill: dimmer, from the right, keeps the far rail from going black
    Softbox((0.88, 0.12, 0.36), 0.40, 0.62, (0.86, 0.90, 1.00), 2.6, 0.48),
    # rim: behind and above, separates the device from the background
    Softbox((0.14, 0.58, -0.82), 0.50, 0.28, (1.00, 0.95, 0.88), 3.0, 0.42),
    # screen box: sits where the glass reflects it. Without this the display is
    # physically correct and looks like a sticker, because the reflection points
    # into the dark surround. Product photographers place a box here too.
    Softbox((-0.30, 0.34, 0.89), 0.50, 0.70, (1.00, 1.00, 1.00), 3.4, 0.55),
]

FLOOR = np.array([0.075, 0.078, 0.086])
CEIL = np.array([0.17, 0.175, 0.195])


def environment(d, rough):
    """Radiance arriving from direction d. Roughness widens the softbox edges,
    which stands in for prefiltering the map."""
    up = np.clip(d[..., 1:2] * 0.5 + 0.5, 0.0, 1.0)
    col = FLOOR + (CEIL - FLOOR) * up

    soft = np.asarray(rough)
    if soft.ndim and soft.shape[-1] != 1:
        soft = soft[..., None]

    for s in SOFTBOXES:
        ca = d @ s.axis
        ca = ca[..., None] if ca.ndim == d.ndim - 1 else ca
        front = ca > 0.05
        safe = np.where(front, ca, 1.0)
        u = (d @ s.t)[..., None] / safe
        v = (d @ s.b)[..., None] / safe
        edge = s.softness * (0.25 + 1.75 * soft)
        m = (smoothstep(s.hu + edge, s.hu - edge, np.abs(u)) *
             smoothstep(s.hv + edge, s.hv - edge, np.abs(v)))
        col = col + np.where(front, m, 0.0) * s.colour * s.intensity
    return col


# ------------------------------------------------------------------ materials --

def fresnel(f0, cos_t):
    return f0 + (1.0 - f0) * np.power(np.clip(1.0 - cos_t, 0.0, 1.0), 5.0)


RING = [(np.cos(i * np.pi / 3), np.sin(i * np.pi / 3)) for i in range(6)]


def env_spec(d, rough):
    """Rough specular lobe, by sampling the environment on a ring around the
    reflection vector.

    environment() only widens the softbox *edges* with roughness, so a single tap
    can never be more than a soft-edged mirror however rough you claim it is.
    Spreading the taps spreads the highlight itself, which is the whole
    difference between gloss and satin.
    """
    up = np.where(np.abs(d[..., 1:2]) > 0.95,
                  np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0]))
    t = norm(np.cross(up, d))
    b = np.cross(d, t)
    a = rough * rough * 1.7
    s = environment(d, rough) * 2.0        # centre tap carries double weight
    for c, sn in RING:
        s = s + environment(norm(d + (t * c + b * sn) * a), rough)
    return s / 8.0


# Anodised aluminium, not bare metal: a dark absorbing layer over a metal core.
# Tinting the specular by the finish, which is what a plain metal f0 does, means
# a dark finish also darkens its own highlights — so the rails can never show the
# soft bright sheen the reference has. Splitting it into a near-neutral coat plus
# a tinted body term is what lets the chassis be genuinely dark and still catch
# the light at grazing angles.
ROUGH_BODY = 0.38      # satin; uniform over the whole chassis
COAT_F0 = 0.05
COAT_MIX = 0.55
BODY_K = 0.22

# --- outer chassis rim -------------------------------------------------------
# The 1.15mm EDGE chamfer around the front face is real geometry that has always
# been there; it just never showed, because the glass/metal split was `n.z > 0.5`
# and that sweeps the first 60 degrees of the chamfer arc — about 87% of its
# head-on projection — into the glass path, where it is painted black. Splitting
# in-plane instead hands the chamfer back to the metal path.
#
# LIP_W is the inset from the silhouette at which the glass starts, so it is the
# single dial for how wide the rim reads. It is spent out of the existing 2.4mm
# BEZEL: the display does not move, the black bezel just gets thinner.
RIM = True
LIP_W = 1.30           # 1.15 puts the glass edge exactly at the chamfer top
ROUGH_LIP = 0.16       # polished, against the satin rails — a diamond-cut chamfer
HAIR_W = 0.10          # dark hairline where the glass meets the band
HAIR_K = 0.30
LIP_R, LIP_Z = 0.26, 0.14   # optional cove; unused unless sd_lip_cove is wired in


def lip_fields(p, n, aa):
    """In-plane glass boundary, glass coverage and the polished-lip mask.

    `d_glass` is in-plane rather than normal-based so the split sits at the same
    place on the device at every view angle instead of sliding around the
    chamfer as the camera moves. `aa` is the pixel footprint in millimetres:
    the rim is 3-4px in the preview and the raymarch is one sample per pixel,
    so without an analytic edge it crawls.
    """
    nz = n[..., 2:3]
    pz = p[..., 2:3]
    d_glass = sd_rrect(p[..., :2], np.array([W / 2 - LIP_W, H / 2 - LIP_W]),
                       max(CORNER - LIP_W, 0.01))[..., None]
    on_front = smoothstep(0.30, 0.55, nz) * (pz > D / 2 - EDGE - 0.35)
    glass_a = on_front * (1.0 - smoothstep(-aa, aa, d_glass))
    # Polish only the front-facing part of the chamfer: it fades out as the
    # surface turns into the side rail, which is where the real diamond cut ends.
    # The outer edge is aa + 0.30 rather than a bare 0.30 so the pair stays
    # ordered however coarse the pixel gets — GLSL smoothstep with edge0 > edge1
    # is undefined, and at a wide zoom-out aa alone comes within a hair of 0.30.
    lip_a = (smoothstep(-0.05, 0.35, nz) * smoothstep(-aa, aa + 0.30, d_glass)
             * (pz > D / 2 - EDGE - 0.5))
    return d_glass, glass_a, on_front, lip_a


def sd_lip_cove(p):
    """Shallow cove at the glass edge — a tube of radius LIP_R swept along the
    rounded-rect centreline at inset LIP_W, sitting LIP_Z above the front face.

    Only needed if a wide LIP_W leaves a flat shoulder with nothing to catch the
    light. Watch two things: normal_at uses eps=0.035, so a cove this shallow is
    only a few eps deep and the normals can come out mushy; and sd_rrect
    under-estimates distance inside the rect, so the corner arcs are where
    step-through speckle would appear.
    """
    ring = sd_rrect(p[..., :2], np.array([W / 2 - LIP_W, H / 2 - LIP_W]),
                    max(CORNER - LIP_W, 0.01))
    return np.hypot(ring, p[..., 2] - (D / 2 + LIP_Z)) - LIP_R


def shade_metal(n, v, base, rough):
    r = norm(2.0 * dot(n, v) * n - v)
    ndv = np.clip(dot(n, v), 0.0, 1.0)
    f = fresnel(COAT_F0 + (base - COAT_F0) * COAT_MIX, ndv)
    spec = env_spec(r, rough) * f
    body = env_spec(n, np.full_like(rough, 0.95)) * base * (1.0 - f) * BODY_K
    return spec + body


def glass_reflection(n, v, rough):
    """Just the reflected component. The design underneath is composited after
    tone mapping, not before — running it through the HDR curve is what turned
    saturated UI colours into pastels."""
    r = norm(2.0 * dot(n, v) * n - v)
    refl = environment(r, rough)
    f = fresnel(0.085, np.clip(dot(n, v), 0.0, 1.0))
    return refl, f


# --------------------------------------------------------------------- render --

def screen_uv(p):
    sw, sh = W / 2 - BEZEL, H / 2 - BEZEL
    u = (p[..., 0:1] + sw) / (2 * sw)
    v = (sh - p[..., 1:2]) / (2 * sh)
    d = sd_rrect(p[..., :2], np.array([sw, sh]), SCREEN_R)
    return u, v, d


# Notch, in millimetres so radii come out circular. Working in screen uv made
# them elliptical, because u spans the width and v the height and the display is
# 2.17:1.
NOTCH_W, NOTCH_H = 25.0, 6.9
NOTCH_R = 3.4       # the notch's own bottom corners
NOTCH_F = 2.1       # concave fillet where the screen curves into the shoulder
BLACK = 0.018


def apply_notch(base, p, sh):
    """Black cutout plus the hardware inside it. The shoulder fillets are the
    detail that makes this read as an iPhone rather than a rectangle."""
    x, y = p[..., 0:1], p[..., 1:2]
    hw = NOTCH_W * 0.5
    top, bot = sh + 4.0, sh - NOTCH_H          # extends past the top edge so
    cy = (top + bot) * 0.5                     # only the lower corners round
    d = sd_rrect(np.concatenate([x, y - cy], axis=-1),
                 np.array([hw, (top - bot) * 0.5]), NOTCH_R)[..., None]
    notch = d < 0.0

    ax = np.abs(x)
    in_corner = (ax >= hw) & (ax <= hw + NOTCH_F) & (y >= sh - NOTCH_F) & (y <= sh)
    outside_arc = np.hypot(ax - (hw + NOTCH_F), y - (sh - NOTCH_F)) > NOTCH_F
    notch = notch | (in_corner & outside_arc)

    out = np.where(notch, BLACK, base)

    # speaker slot
    sp = sd_rrect(np.concatenate([x, y - (sh - 1.75)], axis=-1),
                  np.array([4.7, 0.42]), 0.42)[..., None]
    out = np.where(sp < 0, np.array([0.115, 0.118, 0.125]), out)

    # front camera: dark ring with a faint blue element
    cd = np.hypot(x + 8.6, y - (sh - 3.7))
    out = np.where(cd < 1.15, np.array([0.055, 0.058, 0.070]), out)
    out = np.where(cd < 0.62, np.array([0.075, 0.115, 0.235]), out)
    return out


def render(size=(560, 760), angles=(-8, -24, -6), cam_z=520.0, fov=None,
           finish=(0.095, 0.100, 0.110), screen_img=None, steps=90, fit=0.86):
    Wp, Hp = size
    aspect = Wp / Hp
    if fov is None:
        # Frame the object's bounding sphere. cam_z alone then governs how strong
        # the perspective is, and framing stops fighting it.
        radius = np.hypot(np.hypot(W / 2, H / 2), D / 2)
        half = np.degrees(np.arcsin(np.clip(radius / cam_z, 0, 0.99)))
        fov = 2 * half / fit
        if aspect < 1:
            fov = fov  # portrait canvas: vertical fov already the limit
    yy, xx = np.mgrid[0:Hp, 0:Wp]
    ndc_x = (xx + 0.5) / Wp * 2 - 1
    ndc_y = 1 - (yy + 0.5) / Hp * 2
    tf = np.tan(np.radians(fov) * 0.5)
    dirs = norm(np.stack([ndc_x * tf * aspect, ndc_y * tf, -np.ones_like(ndc_x)], axis=-1))
    origin = np.array([0.0, 0.0, cam_z])

    R = rot_matrix(*angles)
    Rt = R.T
    o_obj = origin @ Rt.T
    d_obj = dirs @ Rt.T

    t = np.full(dirs.shape[:2], cam_z - 140.0)
    hit = np.zeros(dirs.shape[:2], bool)
    for _ in range(steps):
        p = o_obj + d_obj * t[..., None]
        dist = sdf(p)
        newly = (dist < 0.02) & ~hit
        hit |= newly
        t = np.where(hit, t, t + np.maximum(dist, 0.01) * 0.9)
        t = np.minimum(t, cam_z + 300)

    p = o_obj + d_obj * t[..., None]
    n_obj = normal_at(p)
    v_obj = -d_obj

    u, vv, sd_screen = screen_uv(p)
    sw, sh = W / 2 - BEZEL, H / 2 - BEZEL

    n_world = n_obj @ R.T
    v_world = v_obj @ R.T

    if RIM:
        # Pixel footprint in millimetres at the hit point, from the same camera
        # algebra the projection uses. Resolution-relative, so the soft edge is
        # one pixel wide at preview size and at export size alike.
        p_world = p @ R.T
        px_mm = 2.0 * tf * np.maximum(cam_z - p_world[..., 2:3], 1.0) / Hp
        aa = px_mm * 0.75
        d_glass, glass_a, on_front, lip_a = lip_fields(p, n_obj, aa)
        on_screen = (n_obj[..., 2:3] > 0.0) & (sd_screen[..., None] < 0)
    else:
        front = (n_obj[..., 2:3] > 0.5) & (p[..., 2:3] > D / 2 - EDGE - 0.35)
        glass_a = front.astype(float)
        lip_a = np.zeros_like(glass_a)
        on_screen = front & (sd_screen[..., None] < 0)

    if screen_img is None:
        base = np.zeros(u.shape[:-1] + (3,))
    else:
        ih, iw = screen_img.shape[:2]
        sx = np.clip((u[..., 0] * iw).astype(int), 0, iw - 1)
        sy = np.clip((vv[..., 0] * ih).astype(int), 0, ih - 1)
        base = screen_img[sy, sx].astype(float) / 255.0
    base = base * on_screen
    base = apply_notch(base, p, sh)

    # One finish over the whole chassis — rails, back, camera bump and the bottom
    # edge all resolve through the identical path; the front chamfer differs only
    # in roughness, so the rim is the same material catching the light harder.
    rough_body = np.full(n_obj[..., 2:3].shape, ROUGH_BODY)
    rough = rough_body + (ROUGH_LIP - ROUGH_BODY) * lip_a

    metal = shade_metal(n_world, v_world, np.array(finish), rough)
    metal = metal * cavity_vis(p)[..., None]
    if RIM and HAIR_K < 1.0:
        # Fine dark gap where the glass meets the band. Real, and it also hides
        # any residual aliasing left at the boundary.
        seam = (1.0 - smoothstep(0.0, HAIR_W + aa, np.maximum(d_glass, 0.0))) * on_front
        metal = metal * (1.0 + (HAIR_K - 1.0) * seam)
    refl, f = glass_reflection(n_world, v_world, np.full_like(rough_body, 0.045))

    # Tone map the lit components only.
    def encode(c):
        return np.power(np.clip(c / (1.0 + c), 0, 1), 1 / 2.2)

    metal_ldr = encode(metal)
    refl_ldr = encode(refl)

    # Glass covers the front face out to the chassis lip, not out to the
    # silhouette: the design inside the display, black across the bezel, and the
    # chamfer beyond it left to the metal path. A float coverage rather than a
    # boolean, because the boundary is only a few pixels from the outline and a
    # hard test crawls along it.
    glass_ldr = base * (1.0 - f) + refl_ldr * f
    col = metal_ldr + (glass_ldr - metal_ldr) * glass_a

    alpha = hit[..., None].astype(float)
    return np.concatenate([col, alpha], axis=-1)


# ---------------------------------------------------------- ground shadow --

def camera(size, cam_z, fit=0.86):
    Wp, Hp = size
    radius = np.hypot(np.hypot(W / 2, H / 2), D / 2)
    half = np.arcsin(np.clip(radius / cam_z, 0, 0.99))
    tan_half = np.tan(half / fit)
    return tan_half, Wp / Hp


def project_world(P, cam_z, tan_half, aspect, Wp, Hp):
    d = np.maximum(cam_z - P[..., 2], 1e-3)
    nx = P[..., 0] / (d * tan_half * aspect)
    ny = P[..., 1] / (d * tan_half)
    return np.stack([(nx + 1) * 0.5 * Wp, (1 - ny) * 0.5 * Hp], axis=-1)


def outline_3d(steps=18):
    """The device silhouette, front and back rings, in object space."""
    hw, hh = W / 2 - CORNER, H / 2 - CORNER
    pts = []
    for cx, cy, a0 in [(hw, hh, 0), (-hw, hh, np.pi / 2),
                       (-hw, -hh, np.pi), (hw, -hh, 3 * np.pi / 2)]:
        for i in range(steps + 1):
            a = a0 + np.pi / 2 * (i / steps)
            pts.append((cx + CORNER * np.cos(a), cy + CORNER * np.sin(a)))
    p = np.array(pts)
    front = np.column_stack([p, np.full(len(p), D / 2)])
    back = np.column_stack([p, np.full(len(p), -D / 2)])
    return np.vstack([front, back])


def shadow_polygon(angles, size, cam_z, light=(0.28, -1.0, -0.30), gap=0.5, fit=0.86):
    """Project the silhouette onto a ground plane along the light direction.

    The old shadow was the front silhouette nudged down a fixed fraction of the
    canvas, so it never changed shape as the device tilted. This one is the real
    thing: every silhouette point is cast onto the plane the device is standing
    on, so laying the phone down stretches the shadow the way it should.
    """
    R = rot_matrix(*angles)
    pts = outline_3d() @ R.T
    ground = pts[:, 1].min() - gap
    L = np.array(light, float)
    L = L / np.linalg.norm(L)
    t = (ground - pts[:, 1]) / L[1]
    hit = pts + t[:, None] * L
    tan_half, aspect = camera(size, cam_z, fit)
    return project_world(hit, cam_z, tan_half, aspect, *size), pts, ground
