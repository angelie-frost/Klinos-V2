// Main thread. Can touch the Figma document, cannot touch pixels.
// Everything graphical happens in ui.html and comes back here as PNG bytes.

const DATA_KEY = 'perspectiveMockup';
const LAYOUT_KEY = 'layout';

// Window sizes must match WINDOW in ui.html.
const WINDOW = { stacked: [480, 760], side: [940, 660] };

figma.showUI(__html__, { width: 480, height: 760, themeColors: true });

// A layout preference is a few bytes, which is what clientStorage is actually
// for — unlike the device assets, which are inlined instead.
async function restoreLayout() {
  let layout = 'stacked';
  try {
    const saved = await figma.clientStorage.getAsync(LAYOUT_KEY);
    if (saved === 'side' || saved === 'stacked') layout = saved;
  } catch (e) {
    // storage can be cleared or unavailable; the default is fine
  }
  if (layout !== 'stacked') {
    const [w, h] = WINDOW[layout];
    figma.ui.resize(w, h);
  }
  figma.ui.postMessage({ type: 'layout', layout });
}

let currentSourceId = null;
let currentMockupId = null;

function isExportable(node) {
  return node && typeof node.exportAsync === 'function' && 'width' in node;
}

function readMockupData(node) {
  if (!node || typeof node.getPluginData !== 'function') return null;
  const raw = node.getPluginData(DATA_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch (e) {
    return null;
  }
}

async function sendSource(sourceNode, opts) {
  const bytes = await sourceNode.exportAsync({
    format: 'PNG',
    constraint: { type: 'SCALE', value: 2 }
  });
  figma.ui.postMessage({
    type: 'source',
    bytes,
    width: sourceNode.width,
    height: sourceNode.height,
    name: sourceNode.name,
    settings: opts.settings || null,
    mode: opts.mode
  });
}

async function syncSelection() {
  const sel = figma.currentPage.selection;

  if (sel.length === 0) {
    currentSourceId = null;
    currentMockupId = null;
    figma.ui.postMessage({ type: 'empty', reason: 'Select a frame to build a mockup from.' });
    return;
  }

  const node = sel[0];

  // Case 1: an existing mockup this plugin made. Offer to refresh it in place.
  const stored = readMockupData(node);
  if (stored && stored.sourceId) {
    let sourceNode = null;
    try {
      sourceNode = await figma.getNodeByIdAsync(stored.sourceId);
    } catch (e) {
      sourceNode = null;
    }
    if (sourceNode && isExportable(sourceNode)) {
      currentSourceId = stored.sourceId;
      currentMockupId = node.id;
      await sendSource(sourceNode, { settings: stored.settings, mode: 'refresh' });
      return;
    }
    currentSourceId = null;
    currentMockupId = null;
    figma.ui.postMessage({
      type: 'empty',
      reason: 'This mockup points at a frame that no longer exists. Select a frame to start again.'
    });
    return;
  }

  // Case 2: a normal node. Treat it as the source for a new mockup.
  if (isExportable(node)) {
    currentSourceId = node.id;
    currentMockupId = null;
    await sendSource(node, { mode: 'new' });
    return;
  }

  currentSourceId = null;
  currentMockupId = null;
  figma.ui.postMessage({ type: 'empty', reason: 'That selection cannot be exported. Pick a frame or group.' });
}

// The UI disables its button and shows "Rendering" the moment it posts an
// insert, and only hears back on success. Every failure has to say so, or the
// button stays dead for the rest of the session.
function failInsert(reason) {
  figma.notify(reason);
  figma.ui.postMessage({ type: 'insert-failed', reason });
}

async function insertMockup(msg) {
  if (!currentSourceId) {
    failInsert('Select a frame first.');
    return;
  }

  const sourceNode = await figma.getNodeByIdAsync(currentSourceId);
  if (!sourceNode) {
    failInsert('The source frame has gone missing.');
    return;
  }

  const image = figma.createImage(new Uint8Array(msg.bytes));
  const displayW = msg.width / msg.scale;
  const displayH = msg.height / msg.scale;
  const payload = JSON.stringify({ sourceId: currentSourceId, settings: msg.settings });

  const fill = {
    type: 'IMAGE',
    scaleMode: 'FILL',
    imageHash: image.hash
  };

  // Refresh in place if we opened from an existing mockup.
  if (currentMockupId) {
    const existing = await figma.getNodeByIdAsync(currentMockupId);
    if (existing && 'fills' in existing) {
      existing.resize(displayW, displayH);
      existing.fills = [fill];
      existing.setPluginData(DATA_KEY, payload);
      figma.currentPage.selection = [existing];
      figma.notify('Mockup refreshed.');
      figma.ui.postMessage({ type: 'inserted' });
      return;
    }
  }

  const rect = figma.createRectangle();
  rect.name = sourceNode.name + ' — mockup';
  rect.resize(displayW, displayH);
  rect.fills = [fill];
  rect.x = sourceNode.x + sourceNode.width + 80;
  rect.y = sourceNode.y;
  rect.setPluginData(DATA_KEY, payload);

  const parent = sourceNode.parent && sourceNode.parent.type !== 'DOCUMENT'
    ? sourceNode.parent
    : figma.currentPage;
  parent.appendChild(rect);

  currentMockupId = rect.id;
  figma.currentPage.selection = [rect];
  figma.viewport.scrollAndZoomIntoView([rect]);
  figma.notify('Mockup inserted.');
  figma.ui.postMessage({ type: 'inserted' });
}

figma.ui.onmessage = async (msg) => {
  if (msg.type === 'layout') {
    const size = WINDOW[msg.layout] || WINDOW.stacked;
    figma.ui.resize(msg.w || size[0], msg.h || size[1]);
    try {
      await figma.clientStorage.setAsync(LAYOUT_KEY, msg.layout);
    } catch (e) {
      // not worth interrupting the user over
    }
  } else if (msg.type === 'ready') {
    await restoreLayout();
    await syncSelection();
  } else if (msg.type === 'insert') {
    // Catch-all rather than a guard around createImage specifically: whatever
    // goes wrong in here, the UI has to be told so it can release the button.
    try {
      await insertMockup(msg);
    } catch (e) {
      failInsert('Could not insert the mockup: ' + ((e && e.message) || String(e)));
    }
  }
};

figma.on('selectionchange', () => {
  syncSelection();
});
