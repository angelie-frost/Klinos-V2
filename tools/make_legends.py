"""Bake the keyboard legends into ui.html as KB_LEGENDS.

The atlas is a top-down greyscale map of the WHOLE keyboard: every legend drawn at
its true position on its cap, in the same plan millimetres the shader's keyboard
uses. LAPTOP_FRAG samples it at (p.xz - origin) * scale with no per-key logic, so
every placement rule lives here, where it is easy.

    python tools/make_legends.py --font PATH/Inter-Regular.ttf            dry run
    python tools/make_legends.py --font ... --preview out.png            + preview
    python tools/make_legends.py --font ... --write                      write ui.html

FONT. Inter 4.1 (SIL Open Font License 1.1, (c) The Inter Project Authors). Not
kept in the repo: fetch
    https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip
    sha256 9883fdd4a49d4fb66bd8177ba6625ef9a64aa45899767dde3d36aa425756b11e
and pass extras/ttf/Inter-Regular.ttf. Only the rendered bitmap ships. Inter
stands in for San Francisco, which is proprietary; both are neo-grotesques.

LAYOUT is MEASURED on references/macbook-keyboard-angle.png, rectified into plan
space by a homography fitted to six letter centres (residual <= 0.25mm), so
positions are +-0.15mm and sizes carry the photo's ~0.4mm blur, corrected for
here. The right-hand fifth of the keyboard is outside the photo; delete, return,
right shift, right option, F10-F12 and the arrows MIRROR their measured left-hand
counterparts or are ESTIMATES, marked below.

ICONS are drawn with PIL primitives, not a font: SF Symbols are Apple-licensed.
They are generic pictograms measured against the same photo.
"""
import argparse
import base64
import io
import math
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

UI = Path(__file__).resolve().parent.parent / 'ui.html'

ATLAS_W, ATLAS_H = 4096, 2048     # power of two: WebGL1 mipmaps need it
SS = 48.0                         # supersampled canvas, px per mm (>= 2.5x the atlas)

# Type roles: (reference glyph, its ink height in mm, extra weight in mm). The
# photo shows three sizes - letters and digits, everything small, the function-row
# numbers - and SF's small sizes are heavier than Inter Regular, hence the extra
# weight on the two smallest roles (matched on integrated ink against the letters).
BIG = ('H', 3.80, 0.0)            # letters, digits, lower punctuation
SMALL = ('x', 1.90, 0.0)          # words (x-height)
SMALL_SYM = ('@', 3.00, 0.04)     # upper glyph of a two-line key
TINY = ('F', 1.70, 0.05)          # F1-F12
MODSYM = ('\u2318', 3.40, 0.0)    # the command/option/control glyphs
ICON_SCALE = 1.50                 # function-row icons, about their centre
ARROW = ('\u25b2', 1.35, 0.0)     # ESTIMATE: outside the photo

# Baselines, mm from the cap centre toward the user (+z).
BASE_BIG = 1.93                   # single letters: cap height centred on the cap
BASE_LOW = 5.31                   # lower glyph of a two-line key
BASE_UP = -2.16                   # upper glyph of a two-line key
BASE_WORD = 5.21                  # words on wide keys and modifiers
BASE_MODSYM = -1.50               # the symbol above a modifier's word
BASE_FNUM = 5.17                  # F1-F12 label
ICON_Z = -2.40                    # function-row icon centre

MARGIN_L = 2.25                   # word inset from a left-side cap edge
MARGIN_SYM_L = 2.60               # modifier symbol inset, left side
MARGIN_R = 3.20                   # word inset from a right-side cap edge
MARGIN_SYM_R = 3.10               # modifier symbol inset, right side

STROKE = 0.38                     # icon line weight, mm (matched on integrated ink)

# Physical layout, same order as kbSlot. Widths in key units.
ROW_Z = [-88.90, -70.60, -52.00, -33.30, -15.10, 3.20]
SLOTS = [
    [1.5] + [1.0] * 12 + [1.0],
    [1.0] + [1.0] * 12 + [1.5],
    [1.5] + [1.0] * 12 + [1.0],
    [1.75] + [1.0] * 11 + [1.75],
    [2.25] + [1.0] * 10 + [2.25],
    [1.0, 1.0, 1.0, 1.25, 5.0, 1.25, 1.0, 1.0, 1.0, 1.0],
]

# One entry per slot. Strings: 'X' single, 'up|low' two-line, 'w:word' left
# word, 'r:word' right word, 'L:sym|word' / 'R:sym|word' modifiers, '@icon'
# function key, '' blank. Row 5's up/down slot is split in code.
LEGENDS = [
    ['w:esc'] + ['@F%d' % i for i in range(1, 13)] + [''],
    ['~|`', '!|1', '@|2', '#|3', '$|4', '%|5', '^|6', '&|7', '*|8', '(|9', ')|0',
     '_|-', '+|=', 'r:delete'],
    ['w:tab'] + list('QWERTYUIOP') + ['{|[', '}|]', '||' + '\\'],
    ['w:caps lock'] + list('ASDFGHJKL') + [':|;', '"|\'', 'r:return'],
    ['w:shift'] + list('ZXCVBNM') + ['<|,', '>|.', '?|/', 'r:shift'],
    ['fn', 'L:\u2303|control', 'L:\u2325|option', 'L:\u2318|command', '',
     'R:\u2318|command', 'R:\u2325|option', 'arrow:\u25c0', 'arrows', 'arrow:\u25b6'],
]
FN_ICONS = ['sun_small', 'sun_large', 'mission', 'search', 'mic', 'moon',
            'rewind', 'playpause', 'forward',
            'speaker0', 'speaker1', 'speaker3']       # F10-F12: ESTIMATE, not in photo


def geometry():
    """Keyboard constants read from LAPTOP_FRAG, so the atlas cannot drift from
    the shader. The row and slot tables above are checked against its source."""
    s = UI.read_text(encoding='utf-8')

    def const(name):
        m = re.findall(r'\b' + name + r' = (-?\d+\.\d+)', s)
        assert len(m) == 1, f'{name}: found {len(m)} definitions'
        return float(m[0])

    g = {n: const(n) for n in ('KB_X0', 'KB_P', 'KB_G', 'KB_HZ', 'KB_R', 'KB_SPAN',
                               'KB_XC', 'KB_BX', 'KB_ZC', 'KB_BZ')}
    row = re.search(r'float kbRowZ\(float k\) \{(.*?)\n\}', s, re.S).group(1)
    for z in ROW_Z:
        assert '%.2f' % z in row, f'row centre {z} not in kbRowZ - tables drifted'
    slot = re.search(r'void kbSlot\(.*?\n\}', s, re.S).group(0)
    for lit in ('1.50', '1.75', '2.25', '12.0', '11.0', '10.0',
                '4.25', '9.25', '10.50', '11.50', '12.50', '13.50'):
        assert lit in slot, f'{lit} not in kbSlot - tables drifted'
    for r in SLOTS:
        assert abs(sum(r) - g['KB_SPAN']) < 1e-9
    return g


class Canvas:
    def __init__(self, g, font_path):
        self.g = g
        self.x0, self.z0 = g['KB_XC'] - g['KB_BX'], g['KB_ZC'] - g['KB_BZ']
        self.w_mm, self.d_mm = 2 * g['KB_BX'], 2 * g['KB_BZ']
        self.im = Image.new('L', (round(self.w_mm * SS), round(self.d_mm * SS)), 0)
        self.d = ImageDraw.Draw(self.im)
        self.font_path = font_path
        self._fonts = {}

    frame = None                  # (cx, cz, s): icons scale about their centre

    def px(self, x, z):
        if self.frame:
            cx, cz, s = self.frame
            x, z = cx + (x - cx) * s, cz + (z - cz) * s
        return ((x - self.x0) * SS, (z - self.z0) * SS)

    def radius(self, r):
        return r * SS * (self.frame[2] if self.frame else 1.0)

    def font(self, role):
        """A font sized so the role's reference glyph has its measured ink height."""
        if role not in self._fonts:
            ref, mm, bold = role
            probe = ImageFont.truetype(self.font_path, 1000)
            box = probe.getbbox(ref, anchor='ls')
            # the extra weight grows the ink by `bold` on every side
            self._fonts[role] = ImageFont.truetype(
                self.font_path, round(1000 * (mm - 2 * bold) * SS / (box[3] - box[1])))
        return self._fonts[role]

    @staticmethod
    def weight(role):
        return {'stroke_width': round(role[2] * SS), 'stroke_fill': 255}

    def text(self, s, x, z, role, anchor):
        self.d.text(self.px(x, z), s, fill=255, font=self.font(role), anchor=anchor,
                    **self.weight(role))

    def text_hink(self, s, x, z_base, role):
        """Centre the INK horizontally on x, baseline at z_base. Inter's side
        bearings are asymmetric, so centring the advance width shifted every
        centred legend left by 0.2-0.6mm against the photo."""
        f = self.font(role)
        l, _, r, _ = f.getbbox(s, anchor='ls')
        px, pz = self.px(x, z_base)
        self.d.text((px - (l + r) / 2, pz), s, fill=255, font=f, anchor='ls', **self.weight(role))

    def text_centred_ink(self, s, x, z, role):
        """Centre the INK box on (x, z): for glyphs with no useful baseline."""
        f = self.font(role)
        l, t, r, b = f.getbbox(s, anchor='ls')
        px, pz = self.px(x, z)
        self.d.text((px - (l + r) / 2, pz - (t + b) / 2), s, fill=255, font=f, anchor='ls',
                    **self.weight(role))

    # -- icon primitives, all in mm ------------------------------------------------
    def line(self, pts, w=STROKE):
        self.d.line([self.px(*p) for p in pts], fill=255, width=max(1, round(w * SS)), joint='curve')
        for p in (pts[0], pts[-1]):                      # round caps
            self.dot(p, w / 2)

    def dot(self, p, r):
        x, z = self.px(*p)
        self.d.ellipse([x - r * SS, z - r * SS, x + r * SS, z + r * SS], fill=255)

    def ring(self, c, r, w=STROKE):
        x, z = self.px(*c)
        r = self.radius(r)
        self.d.ellipse([x - r, z - r, x + r, z + r], outline=255, width=max(1, round(w * SS)))

    def arc(self, c, r, a0, a1, w=STROKE, n=24):
        pts = [(c[0] + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
                c[1] + r * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]
        self.line(pts, w)

    def rrect(self, x0, z0, x1, z1, r, w=STROKE):
        a, b = self.px(x0, z0), self.px(x1, z1)
        self.d.rounded_rectangle([a, b], radius=self.radius(r), outline=255,
                                 width=max(1, round(w * SS)))

    def closed(self, pts, w=STROKE):
        self.line(pts + [pts[0], pts[1]], w)


# -- function-row icons, centred on (cx, cz) --------------------------------------
def sun(cv, cx, cz, core, r0, r1, dots):
    cv.ring((cx, cz), core)
    for i in range(8):
        a = math.radians(i * 45)
        if dots:
            cv.dot((cx + r0 * math.cos(a), cz + r0 * math.sin(a)), STROKE * 0.62)
        else:
            cv.line([(cx + r0 * math.cos(a), cz + r0 * math.sin(a)),
                     (cx + r1 * math.cos(a), cz + r1 * math.sin(a))])


def icon(cv, name, cx, cz):
    if name == 'sun_small':
        sun(cv, cx, cz, 0.52, 1.05, 1.05, True)
    elif name == 'sun_large':
        sun(cv, cx, cz, 0.60, 1.00, 1.45, False)
    elif name == 'mission':                              # two stacked, one tall
        cv.rrect(cx - 1.55, cz - 0.95, cx - 0.10, cz - 0.10, 0.22)
        cv.rrect(cx - 1.55, cz + 0.20, cx - 0.10, cz + 0.95, 0.22)
        cv.rrect(cx + 0.20, cz - 0.95, cx + 1.20, cz + 0.95, 0.22)
    elif name == 'search':
        cv.ring((cx - 0.25, cz - 0.25), 0.85)
        cv.line([(cx + 0.38, cz + 0.38), (cx + 1.05, cz + 1.05)])
    elif name == 'mic':
        cv.rrect(cx - 0.45, cz - 1.30, cx + 0.45, cz + 0.35, 0.45)
        cv.arc((cx, cz - 0.05), 0.85, 0, 180)
        cv.line([(cx, cz + 0.80), (cx, cz + 1.30)])
    elif name == 'moon':                                 # outline crescent, horns up-right
        cv.closed([(cx - 0.15 + x, cz + 0.10 + z) for x, z in crescent(1.10, (0.62, -0.42))])
    elif name in ('rewind', 'forward'):
        s = -1 if name == 'rewind' else 1
        for dx in (-0.70, 0.35):
            x0 = cx + s * dx
            cv.closed([(x0 - s * 0.55, cz - 0.72), (x0 + s * 0.55, cz), (x0 - s * 0.55, cz + 0.72)])
    elif name == 'playpause':
        cv.closed([(cx - 1.20, cz - 0.72), (cx - 0.05, cz), (cx - 1.20, cz + 0.72)])
        for x in (cx + 0.45, cx + 0.95):
            cv.line([(x, cz - 0.72), (x, cz + 0.72)])
    elif name.startswith('speaker'):
        waves = int(name[-1])
        sx = cx - 0.55 - 0.25 * waves
        cv.closed([(sx - 0.75, cz - 0.32), (sx - 0.30, cz - 0.32), (sx + 0.30, cz - 0.85),
                   (sx + 0.30, cz + 0.85), (sx - 0.30, cz + 0.32), (sx - 0.75, cz + 0.32)])
        for i in range(waves):
            cv.arc((sx + 0.30, cz), 0.50 + 0.42 * i, -48, 48)
    else:
        raise KeyError(name)


def crescent(R, off, n=40):
    """Outline of a disc of radius R at the origin minus an equal disc at `off`:
    the outer arc away from `off`, then back along the inner arc."""
    d = math.hypot(*off)
    ux, uz = off[0] / d, off[1] / d
    h = math.sqrt(R * R - (d / 2) ** 2)
    p1 = (off[0] / 2 - uz * h, off[1] / 2 + ux * h)      # the two horn tips
    p2 = (off[0] / 2 + uz * h, off[1] / 2 - ux * h)

    def sweep(c, a, b, away):
        """Arc of the circle at c from point a to point b, on the side facing `away`."""
        t0 = math.atan2(a[1] - c[1], a[0] - c[0])
        t1 = math.atan2(b[1] - c[1], b[0] - c[0])
        span = (t1 - t0) % (2 * math.pi)
        mid = t0 + span / 2
        if math.cos(mid) * away[0] + math.sin(mid) * away[1] < 0:
            span -= 2 * math.pi                          # take the other way round
        return [(c[0] + R * math.cos(t0 + span * i / n), c[1] + R * math.sin(t0 + span * i / n))
                for i in range(n + 1)]

    outer = sweep((0.0, 0.0), p1, p2, (-ux, -uz))
    inner = sweep(off, p2, p1, (-ux, -uz))
    return outer + inner[1:-1]


def globe(cv, cx, cz, r=1.55):
    cv.ring((cx, cz), r)
    cv.line([(cx - r, cz), (cx + r, cz)])
    for zz in (-0.62 * r, 0.62 * r):                     # two parallels
        hw = math.sqrt(r * r - zz * zz)
        cv.line([(cx - hw, cz + zz), (cx + hw, cz + zz)])
    cv.line([(cx, cz - r), (cx, cz + r)])
    x, z = cv.px(cx, cz)                                  # meridian ellipse
    cv.d.ellipse([x - 0.55 * r * SS, z - r * SS, x + 0.55 * r * SS, z + r * SS], outline=255,
                 width=max(1, round(STROKE * SS)))


def caps(g):
    """(row, slot index, label, cx, cz, hx, hz) for every cap, arrows split."""
    for k in range(6):
        u = 0.0
        for i, (w, lab) in enumerate(zip(SLOTS[k], LEGENDS[k])):
            cx = g['KB_X0'] + g['KB_P'] * (u + w / 2)
            hx = (g['KB_P'] * w - g['KB_G']) / 2
            if lab == 'arrows':
                hz = g['KB_HZ'] * 0.5 - 0.6
                yield k, i, 'arrow:\u25b2', cx, ROW_Z[k] - g['KB_HZ'] * 0.5, hx, hz
                yield k, i, 'arrow:\u25bc', cx, ROW_Z[k] + g['KB_HZ'] * 0.5, hx, hz
            else:
                yield k, i, lab, cx, ROW_Z[k], hx, g['KB_HZ']
            u += w


def render(font_path):
    g = geometry()
    cv = Canvas(g, font_path)
    fkey = 0
    for k, i, lab, cx, cz, hx, hz in caps(g):
        if not lab:
            continue
        if lab.startswith('@F'):                         # icon over the F number
            cv.frame = (cx, cz + ICON_Z, ICON_SCALE)
            icon(cv, FN_ICONS[fkey], cx, cz + ICON_Z)
            cv.frame = None
            cv.text_hink(lab[1:], cx, cz + BASE_FNUM, TINY)
            fkey += 1
        elif lab.startswith('w:'):
            cv.text(lab[2:], cx - hx + MARGIN_L, cz + BASE_WORD, SMALL, 'ls')
            if lab == 'w:caps lock':                     # the indicator light
                cv.dot((cx - hx + MARGIN_L + 0.65, cz - 4.60), 0.24)
        elif lab.startswith('r:'):                       # right-hand words: MIRRORED
            cv.text(lab[2:], cx + hx - MARGIN_R, cz + BASE_WORD, SMALL, 'rs')
        elif lab.startswith('L:') or lab.startswith('R:'):
            sym, word = lab[2:].split('|')
            if lab[0] == 'L':
                cv.text(sym, cx - hx + MARGIN_SYM_L, cz + BASE_MODSYM, MODSYM, 'ls')
                cv.text(word, cx - hx + MARGIN_L, cz + BASE_WORD, SMALL, 'ls')
            else:
                cv.text(sym, cx + hx - MARGIN_SYM_R, cz + BASE_MODSYM, MODSYM, 'rs')
                cv.text(word, cx + hx - MARGIN_R, cz + BASE_WORD, SMALL, 'rs')
        elif lab == 'fn':
            globe(cv, cx - hx + MARGIN_L + 1.55, cz + 3.45)
            cv.text('fn', cx + hx - 2.90, cz + BASE_WORD, SMALL, 'rs')
        elif lab.startswith('arrow:'):                   # ESTIMATE: outside the photo
            cv.text_centred_ink(lab[6:], cx, cz, ARROW)
        elif '|' in lab and len(lab) > 1:
            up, low = lab.rsplit('|', 1)                 # '||\' splits as '|', '\'
            cv.text_hink(up, cx, cz + BASE_UP, SMALL_SYM)
            cv.text_hink(low, cx, cz + BASE_LOW, BIG)
        else:                                            # a letter
            cv.text_hink(lab, cx, cz + BASE_BIG, BIG)
            if lab in 'FJ':                              # homing bar
                cv.line([(cx - 1.05, cz + 4.40), (cx + 1.05, cz + 4.40)], 0.30)
    return cv.im.resize((ATLAS_W, ATLAS_H), Image.BOX), g


def encode(atlas):
    b = io.BytesIO()
    atlas.save(b, 'WEBP', lossless=True, quality=100, method=6)
    return base64.b64encode(b.getvalue()).decode()


LINE_PREFIX = "const KB_LEGENDS = '"
INSERT_ANCHOR = '// Assets ship inside this file. Figma plugins are one JS file and one HTML file\n'
HEADER = ('// Keyboard legends: a top-down greyscale map of every cap\'s print, 4096x2048\n'
          '// WebP lossless over the cap bounding box. Generated by tools/make_legends.py\n'
          '// from Inter (SIL OFL 1.1, (c) The Inter Project Authors); never hand-edit.\n')


def write(b64):
    s = UI.read_text(encoding='utf-8')
    line = LINE_PREFIX + b64 + "';\n"
    lines = [l for l in s.split('\n') if l.startswith(LINE_PREFIX)]
    if lines:
        assert len(lines) == 1, f'{len(lines)} KB_LEGENDS lines'
        old = lines[0] + '\n'
        assert s.count(old) == 1
        s = s.replace(old, line)
    else:
        assert s.count(INSERT_ANCHOR) == 1, 'insertion anchor not unique'
        s = s.replace(INSERT_ANCHOR, HEADER + line + '\n' + INSERT_ANCHOR)
    UI.write_text(s, encoding='utf-8')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--font', required=True, help='Inter-Regular.ttf from Inter 4.1')
    ap.add_argument('--preview', help='also save the atlas as this PNG')
    ap.add_argument('--write', action='store_true', help='write KB_LEGENDS into ui.html')
    a = ap.parse_args()
    atlas, g = render(a.font)
    b64 = encode(atlas)
    print(f'atlas {ATLAS_W}x{ATLAS_H} over {2 * g["KB_BX"]:.3f} x {2 * g["KB_BZ"]:.3f} mm '
          f'({ATLAS_W / (2 * g["KB_BX"]):.1f} x {ATLAS_H / (2 * g["KB_BZ"]):.1f} px/mm), '
          f'{len(b64) / 1024:.0f} KB base64')
    if a.preview:
        atlas.save(a.preview)
    if a.write:
        write(b64)
        print('wrote KB_LEGENDS into', UI)


if __name__ == '__main__':
    main()
