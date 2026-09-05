# Reference — Figma Plugin API constraints, packaging, debugging

Load this when touching transforms, image handling, storage, packaging or when
something is failing at the Figma boundary. Not needed for shader or UI work.

## Transform constraints

`node.relativeTransform` is the top two rows of a 3×3 matrix with the bottom
row assumed `[0, 0, 1]` — an **affine** transform. Translation, rotation and
skew only. There is no perspective, no four-corner warp, no trapezoid, and no
homography support in Figma itself. This is why the plugin renders its own
output and writes back an image rather than transforming a live frame.

**The gotcha that trips everyone:** Figma enforces unit-length axis vectors in
the matrix. Setting scale through the matrix components silently fails.
Compose the skew/rotate matrix, normalise the columns to length 1, then call
`resize()` separately for size.

Skew applied this way does not appear in the properties panel. That is
expected, not a bug.

## Images

- Figma downscales images over **4096 px** on the longest side. Cap export
  resolution accordingly or the output is quietly softened.
- Pipeline is `exportAsync()` → bytes to the iframe → render → bytes back →
  `figma.createImage(bytes)` → set as an image fill.
- Output is a flat image. Store the source frame's node id with
  `setPluginData()` so a Refresh button can re-render when the design changes.

## Storage

- `figma.clientStorage` — per-user, per-plugin, **on that machine**. Not in the
  file, not shared with teammates, no version-history entry. Right place for
  cached photo corner coordinates keyed by an image hash. Keep it small; store
  coordinates, not images.
- `figma.root.setPluginData` — travels with the file. Right place for shared
  team angle presets.
- `setSharedPluginData` requires a namespace. Renaming a namespace orphans
  anything already saved, so pick it before rollout, not after.

## Manifest

- `networkAccess` is `none`. Nothing can be fetched at runtime; every asset
  must ship in the bundle. This is the constraint that killed OpenCV.js.
- The `id` is a development placeholder. Change it before any publish; Figma
  issues a real one on first publish. Changing the id makes Figma treat it as a
  different plugin, so an already-imported copy will appear twice under
  Plugins → Development — remove the old entry and re-import.

## Debugging

- Plugins → Development → **Use developer VM** gives far better stack traces.
- Verify the script parses before claiming a patch works:

  ```
  python3 -c "s=open('ui.html').read(); \
    open('/tmp/c.js','w').write(s.split('<script>')[1].split('</script>')[0])"
  node --check /tmp/c.js
  ```

- Plugins need edit access. They are restricted in view-only files.

## Packaging

`tools/package.py` zips the plugin and bumps a version number each run. The
next number comes from scanning the output folder, so deleting old zips rewinds
the count. Version is stamped into `package.json` and `manifest.json` so a
stray zip can be traced back.

Shipped: `manifest.json`, `code.js`, `ui.html`, `README.md`, `SETUP.md`,
`package.json`, `jsconfig.json`, plus the `tools/` and `.vscode/` directories.

## If ui.html becomes painful to edit

The option not yet taken: keep a source file without the inlined assets and
have `inline_assets.py` produce the shipping `ui.html`. That is a real build
step, so it is only worth it if UI edits become frequent.
