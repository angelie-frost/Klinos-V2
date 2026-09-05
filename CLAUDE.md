# CLAUDE.md

Klinos — a Figma plugin rendering design frames onto a phone at
arbitrary 3D angles. Run locally via Plugins → Development → Import plugin from
manifest.

Domain detail — render modes, shader internals, Figma API constraints, past
decisions — lives in `.claude/skills/mockup-plugin/`. Don't restate it here.
This file is working agreements only.

## Layout

Plugin files sit at the repo root, not in a subfolder.

```
manifest.json, code.js, ui.html      the shipping plugin
jsconfig.json, package.json          config
README.md, SETUP.md                  docs
tools/                               inline_assets.py, extract_device.py, package.py
.vscode/                             word wrap off — leave it off
.claude/skills/mockup-plugin/        the rulebook
```

## How to work in this repo

**Patches, not whole files.** `ui.html` is ~640 KB. Never regenerate or print
it in full. Show the changed function or a diff, and edit in place.

**Never hand-edit the `BUILTIN` line.** ~600 KB of that file is one line of
base64 device assets. Regenerate with `tools/inline_assets.py`. Word wrap stays
off in `.vscode/settings.json`; searches matching inside base64 are expected.

**Assert before replacing.** Any string replacement in `ui.html` must confirm
the anchor exists and appears exactly once first. Replacing a string that
occurs twice has already shipped a silent bug here.

**Verify the parse after every edit:**

```
python3 -c "s=open('ui.html').read(); \
  open('/tmp/c.js','w').write(s.split('<script>')[1].split('</script>')[0])"
node --check /tmp/c.js
```

Don't report a change as done before this passes.

**Plan first on anything touching the shader or `fitPose`.** Propose the
approach and wait. Geometry regressions are expensive to spot after the fact —
render before and after side by side when changing the SDF.

**Prototype geometry offline before porting to GLSL.** Work the signed distance
function out in Python and confirm it against real measurements first. Shader
changes are hard to debug in place.

## Scope

Do only what was asked. No new files, no refactors, no dependencies, no
speculative features. `networkAccess` is `none` — nothing may be fetched at
runtime and any new asset must fit in the bundle.

Ask before: changing `manifest.json`, adding a build step, restructuring the
repo, or touching `package.py`'s versioning.

## Packaging

`python3 tools/package.py` builds the zip, auto-bumping the version number and
stamping it into `package.json` and `manifest.json`. The next number comes from
scanning the output folder, so deleting old zips rewinds the count.
