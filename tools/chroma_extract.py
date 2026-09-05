"""
Turn an angled device render into the asset bundle the plugin needs.

Input : one PNG per angle, device on transparency, display filled with a flat
        chroma key.
Output: body.png   - the device with the display keyed out
        <name>.json - the four display corners, corner radius, key colour

Keying on a saturated colour rather than black buys three things over the old
luminance threshold: the notch survives on its own because it isn't the key
colour, the mask lands on the actual display rather than the black glass around
it, and the corner radius can be measured at any angle instead of only head on.
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage
from scipy.optimize import least_squares
from scipy.spatial import ConvexHull

# Distances in RGB from the key colour. Below T_IN a pixel is pure key, above
# T_OUT it's pure device, between the two it's an edge pixel that gets a partial
# alpha and has the key colour subtracted back out.
# Thresholds read off the distance histogram rather than guessed. Across all
# nine renders the key core sits under 20, edge pixels scatter between 30 and
# 130, and no device pixel comes closer than 140 — there are literally zero
# pixels in the 130-140 band. T_OUT below about 120 leaves a half-keyed fringe
# one pixel wide down the display edge.
T_IN, T_OUT = 25.0, 135.0
PAD = 0.06


def detect_key(rgba, sample=400000):
    """Most common strongly saturated colour. Works for any key, not just this
    one, so a future set keyed on magenta needs no code change."""
    op = rgba[..., 3] > 200
    rgb = rgba[..., :3][op].astype(int)
    if len(rgb) > sample:
        rgb = rgb[np.random.default_rng(0).choice(len(rgb), sample, replace=False)]
    mx, mn = rgb.max(axis=1), rgb.min(axis=1)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1), 0)
    cand = rgb[(sat > 0.6) & (mx > 120)]
    if len(cand) < 100:
        return None
    q = (cand // 8).astype(np.int32)
    codes = q[:, 0] * 1024 + q[:, 1] * 32 + q[:, 2]
    vals, counts = np.unique(codes, return_counts=True)
    win = vals[np.argmax(counts)]
    sel = codes == win
    return np.median(cand[sel], axis=0).astype(float)


def key_alpha(rgba, key):
    """Per-pixel screen coverage: 1 = pure key, 0 = untouched device."""
    d = np.linalg.norm(rgba[..., :3].astype(float) - key, axis=2)
    a = 1.0 - np.clip((d - T_IN) / (T_OUT - T_IN), 0.0, 1.0)
    return a * (rgba[..., 3] > 8)


def largest_blob(binary):
    lab, n = ndimage.label(binary)
    if n == 0:
        raise SystemExit('no keyed region found')
    sizes = ndimage.sum(binary, lab, range(1, n + 1))
    return lab == (int(np.argmax(sizes)) + 1)


def line_isect(p1, p2, p3, p4):
    (x1, y1), (x2, y2), (x3, y3), (x4, y4) = p1, p2, p3, p4
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(den) < 1e-9:
        return None
    a, b = x1 * y2 - y1 * x2, x3 * y4 - y3 * x4
    return np.array([(a * (x3 - x4) - (x1 - x2) * b) / den,
                     (a * (y3 - y4) - (y1 - y2) * b) / den])


def cross2(a, b):
    return a[0] * b[1] - a[1] * b[0]


def min_area_quad(pts):
    v = list(pts)
    while len(v) > 4:
        best = bi = bp = None
        n = len(v)
        for i in range(n):
            p = line_isect(v[(i - 1) % n], v[i], v[(i + 1) % n], v[(i + 2) % n])
            if p is None:
                continue
            area = abs(cross2(v[i] - p, v[(i + 1) % n] - p)) / 2
            if best is None or area < best:
                best, bi, bp = area, i, p
        nv = [v[j] for j in range(len(v)) if j != bi and j != (bi + 1) % len(v)]
        nv.insert(min(bi, len(nv)), bp)
        v = nv
    return np.array(v)


def solve_h(src, dst):
    A, b = [], []
    for (x, y), (X, Y) in zip(src, dst):
        A.append([x, y, 1, 0, 0, 0, -x * X, -y * X]); b.append(X)
        A.append([0, 0, 0, x, y, 1, -x * Y, -y * Y]); b.append(Y)
    return np.append(np.linalg.solve(np.array(A, float), np.array(b, float)), 1).reshape(3, 3)


def rectify(mask, quad, n=700):
    w = (np.linalg.norm(quad[1] - quad[0]) + np.linalg.norm(quad[2] - quad[3])) / 2
    h = (np.linalg.norm(quad[3] - quad[0]) + np.linalg.norm(quad[2] - quad[1])) / 2
    m = int(n * h / w)
    H = solve_h([(0, 0), (1, 0), (1, 1), (0, 1)], [tuple(p) for p in quad])
    u, v = np.meshgrid(np.linspace(0, 1, n), np.linspace(0, 1, m))
    den = H[2, 0] * u + H[2, 1] * v + H[2, 2]
    xs = ((H[0, 0] * u + H[0, 1] * v + H[0, 2]) / den).round().astype(int)
    ys = ((H[1, 0] * u + H[1, 1] * v + H[1, 2]) / den).round().astype(int)
    ok = (xs >= 0) & (xs < mask.shape[1]) & (ys >= 0) & (ys < mask.shape[0])
    out = np.zeros((m, n), bool)
    out[ok] = mask[ys[ok], xs[ok]]
    return out, n, h / w


def _corner_fit(rect, n, flip_h, flip_v):
    r = rect[::-1] if flip_v else rect
    r = r[:, ::-1] if flip_h else r
    pts = []
    for row in range(1, int(r.shape[0] * 0.30)):
        cols = np.where(r[row])[0]
        if len(cols):
            pts.append((cols.min() / n, row / n))
    if len(pts) < 20:
        return None
    pts = np.array(pts)
    pts = pts[pts[:, 0] > pts[:, 0].min() + 0.004]
    if len(pts) < 12:
        return None

    def res(x):
        return np.hypot(pts[:, 0] - x[0], pts[:, 1] - x[0]) - x[0]

    sol = least_squares(res, [0.12], bounds=([0.01], [0.45]))
    return float(sol.x[0])


def screen_radius(mask, quad):
    """Fit all four corner arcs in rectified display space.

    Only trustworthy on a near-frontal render. At an angle the far corners are
    foreshortened into too few pixels to trace, and the four fits scatter — on
    these renders the front agrees to within 6% across its corners while the
    angled ones spread from 0.089 to 0.132 for the same device. The spread is
    returned so the caller can pick its calibration reference automatically.
    """
    rect, n, _ = rectify(mask, quad)
    vals = [v for v in (_corner_fit(rect, n, fh, fv)
                        for fh, fv in [(False, False), (True, False),
                                       (False, True), (True, True)]) if v]
    if len(vals) < 3:
        return None
    return float(np.median(vals)), float(np.max(vals) - np.min(vals))


def order_corners(quad, rgba, mask):
    """Index 0 becomes the display's top-left, found from the notch: the one
    sizeable non-key island inside the display."""
    c0 = quad.mean(axis=0)
    inner = c0 + (quad - c0) * 0.94
    h, w = mask.shape
    yy, xx = np.mgrid[0:h, 0:w]
    ins = np.ones((h, w), bool)
    sign = cross2(quad[1] - quad[0], quad[2] - quad[1]) > 0
    for i in range(4):
        a, b = inner[i], inner[(i + 1) % 4]
        side = (b[0] - a[0]) * (yy - a[1]) - (b[1] - a[1]) * (xx - a[0])
        ins &= side >= 0 if sign else side <= 0
    notch = ins & ~mask & (rgba[..., 3] > 200)
    if notch.sum() < 200:
        return quad
    notch = largest_blob(notch)
    ys, xs = np.where(notch)
    c = np.array([xs.mean(), ys.mean()])
    d = []
    for i in range(4):
        a, b = quad[i], quad[(i + 1) % 4]
        t = b - a
        d.append(abs(cross2(t, c - a)) / np.linalg.norm(t))
    top = int(np.argmin(d))
    return np.array([quad[(top + k) % 4] for k in range(4)])


def projected_aspect(quad):
    w = (np.linalg.norm(quad[1] - quad[0]) + np.linalg.norm(quad[2] - quad[3])) / 2
    h = (np.linalg.norm(quad[3] - quad[0]) + np.linalg.norm(quad[2] - quad[1])) / 2
    return float(h / w)


def build(path, outdir, calib=None):
    name = Path(path).stem
    rgba = np.array(Image.open(path).convert('RGBA'))
    key = detect_key(rgba)
    if key is None:
        raise SystemExit(f'{name}: no chroma key found')

    cover = key_alpha(rgba, key)
    mask = largest_blob(cover > 0.5)

    ys, xs = np.where(mask)
    pts = np.column_stack([xs, ys]).astype(float)
    quad = min_area_quad(pts[ConvexHull(pts).vertices])
    c = quad.mean(axis=0)
    quad = quad[np.argsort(np.arctan2(quad[:, 1] - c[1], quad[:, 0] - c[0]))]
    quad = order_corners(quad, rgba, mask)

    rad = screen_radius(mask, quad)

    # Pad, since the renders sit flush against their canvas edges.
    h, w = mask.shape
    px, py = int(w * PAD), int(h * PAD)
    canvas = np.zeros((h + 2 * py, w + 2 * px, 4), np.uint8)
    canvas[py:py + h, px:px + w] = rgba
    cov = np.zeros(canvas.shape[:2], float)
    cov[py:py + h, px:px + w] = cover
    quad = quad + np.array([px, py])

    # Knock the display out. Edge pixels are part key, part bezel, so the key is
    # subtracted back out of them — without this a green fringe survives all the
    # way round the screen.
    out = canvas.astype(float)
    a0 = out[..., 3] / 255.0
    keep = np.clip(1.0 - cov, 0.0, 1.0)
    edge = (cov > 0.01) & (cov < 0.99)
    if edge.any():
        k = key.reshape(1, 3)
        denom = np.maximum(keep[edge], 1e-3)[:, None]
        out[..., :3][edge] = np.clip(
            (out[..., :3][edge] - cov[edge][:, None] * k) / denom, 0, 255)
    # Spill suppression along the matte edge. Unpremultiplying assumes coverage
    # is linear in colour distance, which is close but not exact, and what
    # survives is a one-pixel rim still tinted toward the key. Clamping the
    # key's dominant channel to what the other channels support is the standard
    # fix. Confined to a three-pixel band, because the bezel colours themselves
    # project onto the key's chroma direction and would be damaged by a global
    # pass.
    solid = cov > 0.5
    band = ndimage.binary_dilation(solid, iterations=3) & ~solid & (out[..., 3] > 8)
    if band.any():
        dom = int(np.argmax(key))
        others = [i for i in range(3) if i != dom]
        px = out[..., :3][band]
        limit = px[:, others].max(axis=1)
        px[:, dom] = np.minimum(px[:, dom], limit)
        out[..., :3][band] = px

    out[..., 3] = np.clip(a0 * keep, 0, 1) * 255
    Image.fromarray(out.astype(np.uint8)).save(Path(outdir) / f'{name}-body.png')

    meta = {
        'name': name,
        'size': [canvas.shape[1], canvas.shape[0]],
        'screen': [[round(float(x), 2), round(float(y), 2)] for x, y in quad],
        'corner_order': 'top-left, top-right, bottom-right, bottom-left',
        'key': [int(v) for v in key],
        'screen_radius': round(calib['radius'] if calib else (rad[0] if rad else 0.12), 4),
        # True display aspect. Never derived from an angled quad: perspective
        # shortens the receding edges, so a projected quad reports the wrong
        # ratio. Taken from the calibration render for the whole set.
        'aspect': round(calib['aspect'] if calib else projected_aspect(quad), 4),
        'measured': {
            'radius': round(rad[0], 4) if rad else None,
            'radius_spread': round(rad[1], 4) if rad else None,
            'projected_aspect': round(projected_aspect(quad), 4)
        }
    }
    with open(Path(outdir) / f'{name}.json', 'w') as f:
        json.dump(meta, f, indent=2)
    return meta


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    out = Path(args[0])
    out.mkdir(parents=True, exist_ok=True)

    # Pass one: measure everything, then elect a calibration render. The winner
    # is whichever has the tightest agreement between its four corner fits,
    # which is the most frontal one without needing to be told which that is.
    first = [build(p, out) for p in args[1:]]
    ref = min((m for m in first if m['measured']['radius_spread'] is not None),
              key=lambda m: m['measured']['radius_spread'])
    calib = {'radius': ref['measured']['radius'],
             'aspect': ref['measured']['projected_aspect']}
    print('calibration render: %s  (corner spread %.4f)  radius %.4f  aspect %.4f\n'
          % (ref['name'], ref['measured']['radius_spread'], calib['radius'], calib['aspect']))

    for p in args[1:]:
        m = build(p, out, calib)
        d = m['measured']
        print('%-24s key %-15s aspect %.4f  radius %.4f   [own fit %.4f spread %.4f, projected aspect %.4f]'
              % (m['name'], str(tuple(m['key'])), m['aspect'], m['screen_radius'],
                 d['radius'], d['radius_spread'], d['projected_aspect']))
