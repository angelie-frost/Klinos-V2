---
name: Klinos Figma Plugin
description: Work on the Klinos Figma plugin - studio/photoreal/photo render modes, ui.html shader and geometry, device presets, angle extraction, packaging. Use for any change to this plugin.
---

# Klinos — Figma plugin

A Figma plugin that places a design frame onto a phone rendered at an
arbitrary 3D angle. Built in-house to replace a paid mockup plugin
subscription. Runs locally via Plugins → Development → Import plugin from
manifest; not published.

**Before editing anything, ask the user to upload the current `ui.html` and
`code.js`.** This skill describes the shape of the project, not its current
contents. The files change constantly and any copy held here would be stale.

## The two halves

Figma plugins are split across two runtimes and mixing them up is the most
common error:

- `code.js` — main thread. Sees the Figma document. Watches selection, exports
  frames to PNG, writes images back to canvas, stores the source frame id on
  each mockup via `setPluginData`. No DOM.
- `ui.html` — an iframe. A real browser with WebGL and canvas, but **cannot see
  the Figma file**. No `figma` object.

`figma is not defined` means main-thread code ended up in the iframe.
`document is not defined` means the reverse.

## The three render modes

Selected by `state.mode`, listed in `MODES`. All three share the screen fitting
path (`fitUV`), so a change there affects all of them.

| Mode | Device body comes from | Use |
|---|---|---|
| `studio` | Generated in the fragment shader from rounded-rect distance fields. No mesh, no image. | Clean standalone shots, any angle, any finish |
| `photoreal` | Pre-rendered device photographs, base64-inlined in `BUILTIN` | Highest fidelity, fixed set of angles |
| `photo` | The user's own uploaded photograph | Compositing a design into someone else's scene |

`photo` mode has two distinct outcomes and they are easy to confuse:

- **Warp** — the design is warped straight into the four-point quad the user
  places on the photo. Output composites into their image.
- **Copy / angle extraction** — `fitPose` recovers tilt/turn/roll and camera
  distance from the quad, and those angles are applied to the **studio** phone.
  Output is a clean phone at their angle.

The user chose angle extraction as the primary path. Don't silently swap them.

## Key functions in ui.html

- `solveHomography` — the 8×8 solve. The most reusable piece in the file.
- `render` — projects the phone outline, draws the extruded shell in canvas 2D,
  hands the front face to WebGL.
- `FRAG` — the fragment shader. Runs the inverse homography per pixel,
  evaluates rounded-rect distance fields for body and screen, samples the design
  inside the screen region.
- `fitPose` — recovers 3D pose from a photo quad. Verified against synthetic
  quads at known angles (worst error 0.1°) and calibrated renders (0.2°).
- `renderPhoto`, `drawHandles`, `seedQuad` — the photo-mode picker and the
  draggable corner handles. Handles are drawn in the preview only so they never
  reach the export.
- `DEVICES` — device dimensions in millimetres. Adding a device is one entry,
  no other code changes.
- `PRESETS` — angle presets. This is also the shared-presets feature: replacing
  these with team-agreed values gives everyone the same angles.

## Hard rules

**Never blind string-replace in `ui.html`.** This has already caused a real
bug: patches replaced `state.arx = 0;` and `state.scale = 2;` without checking
occurrence count, both appeared twice, and every appended state line was
injected into the Reset handler *and* the settings-restore handler. Reset
silently wiped layout/finish/trim, and refreshing a photoreal mockup reverted
it to clay. Assert the anchor exists and appears exactly once before replacing.

**Never hand-edit the `BUILTIN` line.** `ui.html` is ~640 KB and ~600 KB of
that is one line of base64 device assets. Regenerate it with
`tools/inline_assets.py` after changing device bundles. Word wrap is off in
`.vscode/settings.json` for this reason — leave it off. Searches will match
inside the base64; nonsense hits are expected.

**Verify geometry offline before porting to GLSL.** Shader changes are hard to
debug in place. The established practice is to prototype the signed distance
function in Python, render old and new side by side, confirm the change, then
port. Real measurements beat eyeballing: side buttons are stadium-shaped
(semicircular ends), not rounded rectangles, and that was only obvious in a
side-by-side.

**Check the parse after every patch.** Extract the script block and run
`node --check` before declaring anything done.

## Where to make a change

| Goal | Edit |
|---|---|
| Add or adjust a preset angle | `PRESETS` in `ui.html` |
| Add a generated device | `DEVICES` in `ui.html` |
| Panel styling | the `<style>` block in `ui.html` |
| What lands on the Figma canvas | `code.js` |
| Add photoreal angles | `tools/extract_device.py`, then `tools/inline_assets.py` |

## Known gaps

Phones only — laptops need hinged geometry (lid and base at an angle) and are
deliberately deferred until after the photo path. The studio body is generated,
so no camera bump or material reflections. One mockup at a time; batch apply is
later work.

## Decisions already made

Don't reopen these without a reason:

- **OpenCV.js was rejected** for auto-detecting screen corners. It is ~8 MB of
  wasm, `networkAccess` is `none`, so it would ship inside the bundle. Manual
  four-point placement is exact and never fails. A hand-rolled Sobel plus Hough
  intersection is ~200 lines if auto-detect is ever wanted.
- **Not publishing yet.** Locally imported plugins run indefinitely. Community
  publishing caps the bundle at 15 MB; currently ~640 KB.
- **Clay is a body finish, not a screen finish.** The design stays live on it.

See `REFERENCE.md` for Figma Plugin API constraints, packaging, and debugging.
