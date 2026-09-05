"""Composite a screen into a device bundle. This is the Python twin of what the
plugin does in WebGL, and exists so the corners can be checked by eye."""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


def solve_h(src, dst):
    A, b = [], []
    for (x, y), (X, Y) in zip(src, dst):
        A.append([x, y, 1, 0, 0, 0, -x * X, -y * X]); b.append(X)
        A.append([0, 0, 0, x, y, 1, -x * Y, -y * Y]); b.append(Y)
    h = np.linalg.solve(np.array(A, float), np.array(b, float))
    return np.append(h, 1).reshape(3, 3)


def warp(screen, quad, out_size):
    W, H = out_size
    sw, sh = screen.size
    Hm = solve_h([(0, 0), (sw, 0), (sw, sh), (0, sh)], [tuple(p) for p in quad])
    Hi = np.linalg.inv(Hm)

    x0, y0 = np.floor(quad.min(axis=0)).astype(int)
    x1, y1 = np.ceil(quad.max(axis=0)).astype(int)
    x0, y0 = max(x0, 0), max(y0, 0)
    x1, y1 = min(x1, W), min(y1, H)

    yy, xx = np.mgrid[y0:y1, x0:x1]
    ones = np.ones_like(xx, float)
    p = np.stack([xx + 0.5, yy + 0.5, ones])
    q = np.tensordot(Hi, p, axes=(1, 0))
    u, v = q[0] / q[2], q[1] / q[2]

    src = np.array(screen.convert('RGBA'), float)
    ui = np.clip(u, 0, sw - 1.001); vi = np.clip(v, 0, sh - 1.001)
    x_, y_ = np.floor(ui).astype(int), np.floor(vi).astype(int)
    fx, fy = (ui - x_)[..., None], (vi - y_)[..., None]
    tex = (src[y_, x_] * (1 - fx) * (1 - fy) + src[y_, x_ + 1] * fx * (1 - fy) +
           src[y_ + 1, x_] * (1 - fx) * fy + src[y_ + 1, x_ + 1] * fx * fy)

    inside = (u >= 0) & (u < sw) & (v >= 0) & (v < sh)
    tex[..., 3] *= inside

    out = np.zeros((H, W, 4), np.uint8)
    out[y0:y1, x0:x1] = np.clip(tex, 0, 255).astype(np.uint8)
    return Image.fromarray(out)


def over(base, top):
    return Image.alpha_composite(base, top)


def compose(bundle_dir, name, screen_path, out_path, mark=False):
    d = Path(bundle_dir)
    meta = json.load(open(d / f'{name}.json'))
    quad = np.array(meta['screen'], float)
    W, H = meta['size']

    body = Image.open(d / f'{name}-body.png').convert('RGBA')
    glare = Image.open(d / f'{name}-glare.png').convert('RGBA')
    screen = Image.open(screen_path)

    canvas = Image.new('RGBA', (W, H), (245, 245, 247, 255))
    canvas = over(canvas, warp(screen, quad, (W, H)))
    canvas = over(canvas, body)
    canvas = over(canvas, glare)

    if mark:
        dr = ImageDraw.Draw(canvas)
        r = max(W, H) // 90
        for (x, y), lab, col in zip(quad, ['TL', 'TR', 'BR', 'BL'],
                                    [(255, 0, 128), (0, 200, 255), (255, 190, 0), (0, 220, 90)]):
            dr.ellipse([x - r, y - r, x + r, y + r], fill=col + (255,))
            dr.text((x + r * 1.4, y - r), lab, fill=(20, 20, 20, 255))
    canvas.save(out_path)
    return canvas.size


def test_screen(w=1170, h=2532):
    """A grid with a strong diagonal. Any perspective error shows up as a bend."""
    im = Image.new('RGBA', (w, h), (255, 255, 255, 255))
    d = ImageDraw.Draw(im)
    step = w // 9
    for x in range(0, w + 1, step):
        d.line([(x, 0), (x, h)], fill=(205, 208, 214), width=4)
    for y in range(0, h + 1, step):
        d.line([(0, y), (w, y)], fill=(205, 208, 214), width=4)
    d.line([(0, 0), (w, h)], fill=(255, 60, 120), width=10)
    d.line([(w, 0), (0, h)], fill=(40, 120, 255), width=10)
    # Edge-to-edge markers only: anything cropped or inset shows up immediately.
    d.rectangle([0, 0, w - 1, h - 1], outline=(255, 60, 120), width=16)
    c = w // 7
    for x0, y0, col in [(0, 0, (0, 200, 255)), (w - c, 0, (255, 190, 0)),
                        (w - c, h - c, (0, 220, 90)), (0, h - c, (120, 60, 255))]:
        d.rectangle([x0, y0, x0 + c, y0 + c], fill=col)
    return im


if __name__ == '__main__':
    bundle, out = sys.argv[1], Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    ts = test_screen()
    ts.save(out / 'test-screen.png')
    for name in sys.argv[3:]:
        print(name, compose(bundle, name, out / 'test-screen.png',
                            out / f'{name}-proof.png', mark=True))
