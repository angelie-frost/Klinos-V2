# Setting up in VS Code and Figma

There is no build step. This is plain JavaScript and HTML — no TypeScript, no
bundler, no compile. VS Code is just the editor, and Figma reads the files
straight off disk.

## Requirements

- **Figma desktop app.** Development plugins do not run in the browser.
- VS Code.
- Node and npm, only if you want API autocomplete. Everything works without it.

## Setup

1. Unzip somewhere permanent. Figma remembers the path, so if you move the
   folder later you have to re-import.

2. In VS Code: File → Open Folder → pick `Perspective Mockup`.

3. Optional, for autocomplete on `code.js`:

       npm install

   That pulls `@figma/plugin-typings`. `jsconfig.json` is already wired to it, so
   typing `figma.` will suggest the real API and flag misspellings. Nothing else
   depends on `node_modules`.

4. In Figma desktop: Plugins → Development → Import plugin from manifest, and
   choose `manifest.json`.

5. Select a frame, then Plugins → Development → Klinos.

## The edit loop

Save in VS Code, then re-run the plugin in Figma. There is no watcher.

`Cmd`/`Ctrl` + `Option`/`Alt` + `P` re-runs the last plugin, which makes this
fast enough. Figma reads the files fresh each run, so a save plus a re-run is
the whole cycle.

If a change appears not to take, check you saved, then close the plugin window
fully before re-running.

## Seeing errors

Plugins → Development → Show/Hide console.

This is where errors surface. Worth knowing which half you are looking at:

- `code.js` runs on the main thread. It can reach the Figma document but has no
  DOM, no canvas, no images.
- `ui.html` runs in an iframe. It is a real browser with WebGL, but cannot see
  your Figma file.

A `figma is not defined` error means main-thread code ended up in the iframe. A
`document is not defined` error means the reverse.

Also under Plugins → Development, **Use developer VM** gives better stack traces
while you are working.

## Editing ui.html

`ui.html` is around 640 KB, and roughly 600 KB of that is one line: the base64
device assets in `const BUILTIN`. Everything else is normal.

Practical notes:

- Word wrap is off in `.vscode/settings.json`. Leave it off, or that one line
  will render as thousands of rows and the editor will crawl.
- Search will match inside base64. If a search returns nonsense hits, that is
  why.
- Never hand-edit the `BUILTIN` line. Regenerate it with
  `tools/inline_assets.py` after changing the device bundles.

If it becomes annoying, split it: keep a source file without the assets, and
have `inline_assets.py` produce the shipping `ui.html`. That is a real build
step, so it is only worth it if you are editing the UI often.

## What to change where

| You want to | Edit |
|---|---|
| Add or adjust a generated preset angle | `PRESETS` in `ui.html` |
| Add a generated device | `DEVICES` in `ui.html` |
| Change panel styling | the `<style>` block in `ui.html` |
| Change what lands on the canvas | `code.js` |
| Add photoreal angles | run `tools/extract_device.py`, then `tools/inline_assets.py` |

## Publishing later

Community publishing caps the bundle at 15 MB. This is currently about 640 KB,
so there is room for roughly twenty more angles before that matters.

Before publishing, change the `id` in `manifest.json` — the current one is a
development placeholder. Figma issues a real id when you first publish.
