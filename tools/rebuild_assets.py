import base64, io, json, re
from pathlib import Path
import numpy as np
from PIL import Image

SRC = Path('/home/claude/cbundle')
poses = json.load(open('/home/claude/poses_new.json'))

builtin, total = {}, 0
for f in sorted(SRC.glob('*-body.png')):
    stem = f.name[:-len('-body.png')]
    meta = json.load(open(SRC / f'{stem}.json'))
    _, colour, angle = stem.split('-')

    im = Image.open(f).convert('RGBA')
    r = 3000 / max(im.size)
    if r < 1:
        im = im.resize((round(im.width * r), round(im.height * r)), Image.LANCZOS)
    else:
        r = 1.0
    b = io.BytesIO(); im.save(b, 'WEBP', quality=90, method=6)
    total += len(b.getvalue())

    builtin[f'{colour}-{angle}'] = {
        'meta': {
            'colour': colour,
            'angle': angle,
            'pose': poses[angle],
            'aspect': meta['aspect'],
            'screen_radius': meta['screen_radius'],
            'size': [im.width, im.height],
            'screen': [[round(x * r, 2), round(y * r, 2)] for x, y in meta['screen']]
        },
        'body': base64.b64encode(b.getvalue()).decode()
    }

p = Path('/home/claude/plugin/ui.html')
s = p.read_text()

# ------------------------------------------------------- swap the asset blob --
s = re.sub(r'const BUILTIN = \{.*?\};\n',
           'const BUILTIN = ' + json.dumps(builtin, separators=(',', ':')) + ';\n',
           s, flags=re.S)

# ------------------------------------------------------------- loader, glare --
s = s.replace('''// Greyscale -> white with the grey as alpha, so it composites normally instead
// of needing an additive blend that would wreck transparent exports.
async function greyToGlare(blob) {
  const bmp = await createImageBitmap(blob);
  const c = document.createElement('canvas');
  c.width = bmp.width; c.height = bmp.height;
  const x = c.getContext('2d');
  x.drawImage(bmp, 0, 0);
  const d = x.getImageData(0, 0, c.width, c.height);
  const p = d.data;
  for (let i = 0; i < p.length; i += 4) {
    p[i + 3] = p[i];
    p[i] = p[i + 1] = p[i + 2] = 255;
  }
  x.putImageData(d, 0, 0);
  return createImageBitmap(c);
}

''', '''// The chroma-keyed renders have a perfectly flat screen — measured standard
// deviation 0.09 — so unlike the old black-screen set there is no baked sheen
// to lift out. Glare is generated in the shader instead, which costs nothing
// and drops a file per angle.

''')

s = s.replace('''      const bodyBmp = await createImageBitmap(b64ToBlob(a.body, 'image/webp'));
      const glareBmp = await greyToGlare(b64ToBlob(a.glare, 'image/webp'));
      BUNDLES[name] = {
        meta: a.meta, body: bodyBmp, glare: glareBmp,
        bodyTex: makeTexture(bodyBmp), glareTex: makeTexture(glareBmp)
      };''', '''      const bodyBmp = await createImageBitmap(b64ToBlob(a.body, 'image/webp'));
      BUNDLES[name] = { meta: a.meta, body: bodyBmp, bodyTex: makeTexture(bodyBmp) };''')

s = s.replace("  if (!state.bundle) state.bundle = Object.keys(BUNDLES)[0] || null;\n  refreshBundleChips();",
              "  if (!state.bundle) state.bundle = Object.keys(BUNDLES)[0] || null;\n  refreshAssetChips();")

# ------------------------------------------------------------------- shader --
s = s.replace('''uniform float uAA;       // one pixel, in those same width units''',
              '''uniform float uAA;       // one pixel, in those same width units
uniform float uGlare;''')

s = s.replace('''    float d = sdRound(p, vec2(0.5, uAspect * 0.5), uRadius);
    al *= 1.0 - smoothstep(-uAA, uAA, d);
  }''', '''    float d = sdRound(p, vec2(0.5, uAspect * 0.5), uRadius);
    al *= 1.0 - smoothstep(-uAA, uAA, d);
    if (uGlare > 0.5) {
      float band = uv.x * 0.7 + uv.y * 0.9;
      c.rgb += (smoothstep(0.42, 0.52, band) - smoothstep(0.56, 0.78, band)) * 0.09;
    }
  }''')

s = s.replace('''    gl.uniform1f(u('uAA'), 1.1 / Math.max(wpx, 1));''',
              '''    gl.uniform1f(u('uAA'), 1.1 / Math.max(wpx, 1));
    gl.uniform1f(u('uGlare'), state.glare ? 1 : 0);''')

# --------------------------------------------------------------- renderAsset --
s = s.replace('''  const sw = (Math.hypot(b.meta.screen[1][0] - b.meta.screen[0][0], b.meta.screen[1][1] - b.meta.screen[0][1]) +
              Math.hypot(b.meta.screen[2][0] - b.meta.screen[3][0], b.meta.screen[2][1] - b.meta.screen[3][1])) / 2;
  const shh = (Math.hypot(b.meta.screen[3][0] - b.meta.screen[0][0], b.meta.screen[3][1] - b.meta.screen[0][1]) +
               Math.hypot(b.meta.screen[2][0] - b.meta.screen[1][0], b.meta.screen[2][1] - b.meta.screen[1][1])) / 2;
  const roundOpts = {
    radius: b.meta.screen_radius != null ? b.meta.screen_radius : 0.15,
    aspect: shh / Math.max(sw, 1e-6)
  };''',
'''  // Aspect comes from the metadata, never from the quad. Perspective shortens
  // the receding edges, so an angled quad reports 2.09 for a display that is
  // really 2.17 — enough to skew the corner mask on exactly the angles where it
  // is most visible.
  const roundOpts = {
    radius: b.meta.screen_radius != null ? b.meta.screen_radius : 0.138,
    aspect: b.meta.aspect != null ? b.meta.aspect : 2.17
  };''')

s = s.replace('''  if (scratch) ctx.drawImage(scratch, 0, 0);
  if (state.glare && b.glareTex) {
    const g = warpTexture(b.glareTex, imgQuad, W, H, false);
    if (g) ctx.drawImage(g, 0, 0);
  }
}''', '''  if (scratch) ctx.drawImage(scratch, 0, 0);
}''')

# ------------------------------------------------ transparency: keep shadows --
s = s.replace("  if (state.shadow && !state.transparent && scratch) {",
              "  if (state.shadow && scratch) {")
s = s.replace("  if (state.shadow && !state.transparent) {",
              "  if (state.shadow) {")

p.write_text(s)
print('assets swapped: %d bundles, %.0f KB raw, %.0f KB base64' %
      (len(builtin), total / 1024, total * 1.37 / 1024))
