# Klinos — test build

A Figma plugin that puts your frames on a phone at a real 3D angle, with
converging perspective rather than a 2D skew.

## Install

1. Put `manifest.json`, `code.js` and `ui.html` in the same folder.
2. Figma desktop app → menu → Plugins → Development → Import plugin from manifest
3. Pick `manifest.json`.

Desktop app only. Development plugins don't run in the browser.

## Two modes

**Generated** builds the phone live in 3D. Free angles via the sliders, no
assets needed, but the body has no camera bump or material reflection.

**Photoreal** warps your design into a real device render. Sharp and convincing,
but the angle is whatever the render was — the sliders don't apply.

Three angles ship inside the plugin — Front, Left and Right. Nothing to load,
nothing to pick. Switch to Photoreal and they're there.

*Add your own angles* takes extra bundles from disk for the current session.
Select each angle's `-body.png`, `-glare.png` and `.json` together; they're
matched by filename.

## Test it

1. Select any frame — one of your mobile screens.
2. Plugins → Development → Klinos.
3. Pick an angle preset. Drag the sliders. Pick a device.
4. Insert mockup. An image appears next to your frame.

Then test the refresh loop, which is the part worth checking carefully:

5. Edit the original frame — change a colour, move something.
6. Click the **mockup image** and run the plugin again.
7. It should say "Refreshing mockup from …" and restore your exact settings.
8. Insert. The image updates in place without moving or resizing.

## What each file does

- `manifest.json` — declares the two halves and states that no network access
  is needed.
- `code.js` — main thread. Watches selection, exports frames to PNG, writes
  images back to the canvas, stores the source frame id on each mockup.
- `ui.html` — everything graphical. 3D projection, the homography solve, the
  WebGL fragment shader, and the controls.

## Match nearest

Each built-in render was fitted to a camera pose offline, by optimising tilt,
turn, roll and camera distance until a projected rectangle matched the detected
screen quad. Residuals came in around 0.1%, and the Left and Right renders fitted
as exact mirrors of each other, which is the check that the numbers are real.

    Front   tilt  -1.6   turn  -2.2   roll   0.1
    Left    tilt -33.9   turn  27.6   roll  26.9
    Right   tilt -34.0   turn -27.6   roll -26.9

The button compares the pose you dialled in against those three, switches to the
closest, and hands the leftover rotation to the Nudge sliders. Above 25 degrees
off it says so rather than pretending the match is good.

Fitting the poses turned up a real bug: every render tilts backwards, top edge
away from camera, and the original generated presets tilted forwards. Every
preset was matching to Front because none of them resembled the angled renders
at all. The presets now derive from the measured poses, so Laid left and Laid
right land on their renders at zero degrees.

## Chroma keying

Renders arrive with the display filled with a flat key colour, currently
`#00FF95`. `chroma_extract.py` detects the key itself rather than assuming one,
so a set keyed on magenta needs no code change.

Thresholds were read off the distance histogram, not guessed. Across the nine
renders the key core sits under 20, edge pixels scatter between 30 and 130, and
no device pixel comes closer than 140 — the 130-140 band is literally empty. A
cutoff below about 120 leaves a half-keyed fringe one pixel wide down the
display edge, which is exactly what an earlier cutoff of 90 produced.

Edge pixels are part key, part bezel. The key is unpremultiplied back out of
them, then a spill-suppression pass clamps the key's dominant channel inside a
three-pixel band. That band matters: the bezel colours themselves project onto
the key's chroma direction, so a global pass would damage them. Together the two
steps took green-dominant pixels from 3,521 to 93 on the front render, about
0.01% of the visible area.

Keying on a saturated colour beats the old black threshold in three ways. The
notch survives on its own, because it isn't the key colour — the geometric
carving is gone. The mask lands on the actual display rather than the black
glass around it, which moved the measured aspect from 2.085 to 2.173 against a
true iPhone 13 ratio of 2.164. And the flat key means there is no baked sheen,
so glare is generated in the shader and there is one fewer file per angle.

## Calibration

Corner radius and display aspect are device constants, measured once and applied
to every angle. Both are unreliable on an angled render:

- The radius is fitted at all four corners. On the front render they agree
  within 6%; on angled renders they scatter from 0.089 to 0.132 for the same
  device, because the far corners are foreshortened into too few pixels to
  trace. That is a resolution problem, not a colour one — the chroma key did not
  fix it.
- Aspect cannot be read off a projected quad at all. Perspective shortens the
  receding edges, so the angled renders report 2.091 for a display that is
  really 2.173.

The extractor picks its own reference: whichever render has the tightest
agreement between its four corner fits, which finds the frontal one without
being told. For this set it elected `Real-Blue-Front`, spread 0.0084 against
0.043 for the angled ones, giving radius 0.1381 and aspect 2.1727.

## Screen corner clipping

The screen quad circumscribes a rounded display, so its sharp corners fall
outside the glass. Clipping is a rounded-rectangle distance field evaluated in
the screen's own uv space inside the shader, which means the perspective carries
it correctly to any angle for free. Overhang measured against the device
silhouette is zero on all nine renders.

## How the assets ship

Nine bundles — three finishes by three angles — inlined into `ui.html` as base64
WebP: 1.32 MB raw, 1.81 MB as base64, about 12% of Figma's 15 MB ceiling. Figma plugins are one JS file and one
HTML file with no static assets, so the only alternatives are inlining or
fetching from a server — and fetching would mean network permission in the
manifest, a review disclosure, and a plugin that breaks offline.

`clientStorage` was the obvious alternative and is the wrong tool: 5 MB
officially, but developers report quota errors far below that, and it's wiped
when a user clears their browser cache.

Regenerate with `tools/rebuild_assets.py` after re-running `chroma_extract.py`.

## Where the interesting code is

In `ui.html`:

- `solveHomography` — the 8×8 solve. This is the piece to keep if you later
  switch from generated device bodies to pre-rendered device photographs.
- `render` — projects the phone outline, draws the extruded shell in canvas 2D,
  then hands the front face to WebGL.
- `FRAG` — the shader. Runs the inverse homography per pixel, evaluates a
  rounded-rectangle distance field for the body and screen, and samples your
  design inside the screen region.

## Layout

A segmented control above the preview switches between Stacked and Side by side.
Side by side pins the preview on the left and scrolls the controls on the right,
which suits a wide monitor; stacked is better on a laptop.

Switching also resizes the plugin window — 480x760 stacked, 940x660 side by side
— because the two-column grid needs the width or the controls get squeezed. The
sizes live in `WINDOW`, defined identically in `code.js` and `ui.html`; change
both together.

The choice is remembered in `figma.clientStorage`. A few bytes is what that API
is actually for, unlike the device assets, which are inlined instead.

## Screen fit

Fill, Fit or Stretch, defaulting to Fill. Previously the design was mapped
straight across the display rect whatever its proportions, so any frame that was
not 19.5:9 came out distorted. Fill centre-crops to the display, Fit letterboxes
with black bars, Stretch is the old behaviour kept as an escape hatch.

Handled in the shader via `fitUV`, so it applies identically in all three modes.

## Clay

Clay is a body finish. It previously blanked the screen as well, which turned
the device into a featureless slab; the design now stays live on a clay body,
which is what a clay mockup is for.

## Glare

A softbox reflection — an elongated rounded rectangle fading down the glass plus
a lift at the top edge. The previous version was one linear expression through
two smoothsteps, which read as a stripe across the screen rather than a
reflection. Studio mode does not use this: its glare is a real environment
reflection off the glass.

## Shadow

Cast onto a ground plane rather than offset down the canvas. Every silhouette
point is projected along the light direction onto the plane the device rests on,
then a tighter, darker, less blurred core is added where the device meets it.

The old shadow was the front silhouette pushed down 2-3% of canvas height and
blurred, so it never changed shape with tilt. Measured on the studio geometry,
the ground footprint now runs 52 mm deep upright, 137 mm tilted and 161 mm laid
flat.

Clay and Studio project their own outlines directly. Photoreal has no geometry,
but its pose is known, so a rectangle is rotated, cast, and mapped back through
the placement that put the body on screen.

## Hardware

Studio's SDF carries the side hardware: action button, volume pair, side button,
plus the port slot and speaker grille cut from the bottom edge.

Buttons use a stadium profile, not a rounded rectangle. On the real device the
outline on the rail face is a pill whose ends are exact semicircles of its own
half depth; a rounded rect with a small corner radius reads as a slab. Built by
putting the rounded rectangle in the y-z plane and extruding along x, with the
corner radius equal to that half depth. They stand a fraction proud of the rail
so the union leaves a crease, which is enough to read them.

The grille folds the domain rather than unrolling twelve spheres.

## Perspective matching

Load a reference photo, drag four handles onto its screen corners top-left
first, and Copy recovers the camera angle and applies it to Studio.

Automatic screen detection in an arbitrary photo is a computer-vision problem
that does not fit in a plugin, so the corners are placed by hand. Everything
after that is exact: `fitPose` fits a projected rectangle to the four corners up
to scale and position, recovering tilt, turn, roll and camera distance. Tested
against synthetic quads at six known angles it recovers all three to within 0.17
degrees, and against the three photoreal renders calibrated offline in Python it
agrees to within 0.2 degrees.

The angle is fitted against `STUDIO_ASPECT`, the display proportions of the
Studio phone it will be applied to. Fitting against any other ratio biases the
recovered tilt, since tilt and aspect trade off against each other under
projection. Releasing a handle reports the recovered angles and the fit residual
immediately, so a bad corner placement shows up before committing.

The handles are drawn only in the preview, never in the exported image.

## Trim to device

Off by default. On, the render is cropped to the device's own bounding box, so
the inserted rectangle wraps the phone exactly instead of sitting inside a
padded frame.

The box comes from the device layer alone, never from the finished composite —
measuring the composite would include the shadow and, on an opaque background,
would find nothing to trim at all. Studio and Photoreal already isolate the
device on a scratch canvas for the shadow pass, so the box is a pixel scan of
that. Clay draws straight onto the background with no isolated layer, so its box
is computed analytically from the projected outline instead.

Preview and export share one path, so what you see is what gets inserted. Two
consequences: the frame ratio no longer applies, since the output size is
whatever the device measures, and the shadow is clipped at the edges. Typical
saving is 40-60% of the image area.

## Packaging

    python3 tools/package.py

Writes `dist/klinos_v1.zip`, then `_v2`, and so on. The next number
comes from scanning the output folder, so nothing is stored between runs and
deleting old zips rewinds the count. Versions are counted per name, so
`--name export` starts its own series. The version is also written into
`package.json` and the manifest name, so a stray download can be traced back.
`--version N` forces a number and `--dry-run` shows what would happen.

## Known gaps

- Angles added from disk last for the session only. The three built-ins are
  always present, so mockups made with those refresh normally.
- Photoreal's Nudge is a flat warp of a solid object. Valid because a phone is
  8.25 mm thick against 146.6 mm long, so the error from ignoring depth stays
  under a percent of the silhouette at small angles. The sliders stop at 10
  degrees of turn and tilt because past that the buttons and speaker holes
  visibly stretch — they are 3D features being treated as flat paint. Roll is
  exact, but the lighting rotates with the image, hence the 25 degree cap.
- Photoreal ignores perspective and clay. Body colour is now a finish picker
  instead — Blue, Midnight or Pink — since these are separate renders.
- Bundle renders ship without a shadow pass, so the shadow is derived from the
  device silhouette. Reads fine as a drop shadow, won't give a long cast shadow.
- Phones only. Laptops need different geometry (a lid and a base at an angle to
  each other) — that's the next piece of work.
- The device body is generated, not rendered, so it has no camera bump, no
  buttons, no material reflections. Fine for clay-style shots and for testing
  the pipeline; not a match for a photoreal render.
- Screen aspect is stretched to fit the device, so a frame far off 19.5:9 will
  distort slightly.
- One mockup at a time. Batch apply is the Phase 5 work.

## Tuning notes

- Device dimensions live in `DEVICES` at the top of the script, in millimetres.
  Add a device by adding an entry — no other code changes needed.
- Angle presets live in `PRESETS` right below. Once your team settles on angles
  they like, replace these with those values and you have shared presets.
- Antialiasing quality is the `uAA` uniform. Raise the multiplier for softer
  edges, lower it for crisper ones.
