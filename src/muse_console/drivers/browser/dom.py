"""DOM selectors and in-page scripts for the muse.ai web client.

All knowledge about the page structure lives here. When muse.ai ships a UI
change, this is normally the only file that needs updating. Scripts return
JSON-serialisable values and never throw (errors are reported in the payload).
"""

from __future__ import annotations

import json

INPUT = "textarea"
AGENT_BUBBLE = 'div[class*="hatch-agent-bubble-bg"]'
ATTACHMENT = '[data-testid^="hatch-chat-attachment-presentation-"]'
STOP_BUTTON = '[data-testid="hatch-composer-stop-button"],button[aria-label*="Stop" i]'
# The agent asks before acting on the user's behalf (e.g. uploading the result to
# muse.ai/files as a public link). Nobody answers it here, so it would block for minutes.
APPROVAL_CARD = '[data-testid="hatch-inline-approval-card"]'
FILE_INPUT = 'input[type="file"]'

LOGIN_HINTS = ("log in", "sign in", "create an account", "use another account")
QUOTA_HINTS = ("out of credits", "usage limit", "limit reached", "token limit")
STALL_HINTS = ("Still sending", "Connecting...")


def q(value: str) -> str:
    """JSON-quote a Python string for safe interpolation into JS."""
    return json.dumps(value)


PAGE_STATE = f"""(() => {{
  const body = document.body ? document.body.innerText : '';
  const hydration = document.querySelector('[data-hatch-shell-hydration-state]');
  return {{
    ready: document.readyState === 'complete' && !!document.querySelector({q(INPUT)})
           && (!hydration || hydration.getAttribute('data-hatch-shell-hydration-state') === 'hydrated')
           && !body.includes('Connecting...'),
    hasInput: !!document.querySelector({q(INPUT)}),
    path: location.pathname,
    head: body.slice(0, 800),
  }};
}})()"""

# Snapshot of the conversation used to detect new output after sending.
CHAT_STATE = f"""(() => {{
  const scrollers = [...document.querySelectorAll('*')].filter(e => {{
    const s = getComputedStyle(e);
    return (s.overflowY === 'auto' || s.overflowY === 'scroll') && e.scrollHeight > e.clientHeight + 50;
  }});
  scrollers.forEach(e => {{ e.scrollTop = e.scrollHeight; }});
  const bubbles = [...document.querySelectorAll({q(AGENT_BUBBLE)})];
  const last = bubbles.length ? (bubbles[bubbles.length - 1].innerText || '').trim() : '';
  const atts = [...document.querySelectorAll({q(ATTACHMENT)})].map(a => {{
    const v = a.querySelector('video'), i = a.querySelector('img');
    const m = v || i;
    return {{
      tid: a.getAttribute('data-testid') || '',
      kind: v ? 'video' : (i ? 'image' : 'unknown'),
      src: m ? (m.currentSrc || m.src || '') : '',
      w: m ? (m.videoWidth || m.naturalWidth || 0) : 0,
      h: m ? (m.videoHeight || m.naturalHeight || 0) : 0,
    }};
  }});
  const tail = document.body ? document.body.innerText.slice(-600) : '';
  return {{ agentCount: bubbles.length, lastText: last,
            generating: !!document.querySelector({q(STOP_BUTTON)}),
            approval: !!document.querySelector({q(APPROVAL_CARD)}),
            attachments: atts, tail }};
}})()"""


# Click "Deny" on every pending approval card; the agent then carries on without
# the action (and the result stays private instead of becoming a public link).
DENY_APPROVALS = f"""(() => {{
  const denied = [];
  for (const card of document.querySelectorAll({q(APPROVAL_CARD)})) {{
    const btn = [...card.querySelectorAll('button,[role=button]')]
      .find(b => /^(deny|decline|reject|don.t allow)$/i.test((b.innerText || '').trim()));
    if (btn && !btn.disabled) {{
      btn.click();
      denied.push(card.getAttribute('aria-label') || 'approval request');
    }}
  }}
  return denied;
}})()"""


# Media links that the assistant delivered as a text bubble / anchor / <video>
# element rather than as an inline attachment presentation. Scans the last agent
# bubble for every candidate URL (anchor hrefs, media element srcs incl. blob:,
# and bare URLs in the text) and returns the bubble HTML for diagnostics.
LAST_BUBBLE_MEDIA = f"""(() => {{
  const bubbles = [...document.querySelectorAll({q(AGENT_BUBBLE)})];
  const b = bubbles.length ? bubbles[bubbles.length - 1] : null;
  if (!b) return {{ links: [], html: '' }};
  const urls = [];
  const push = (u) => {{ if (u && !urls.includes(u)) urls.push(u); }};
  b.querySelectorAll('video, source').forEach(m => push(m.currentSrc || m.src || ''));
  b.querySelectorAll('a[href]').forEach(a => push(a.href || ''));
  b.querySelectorAll('img').forEach(m => push(m.currentSrc || m.src || ''));
  const text = b.innerText || '';
  const re = /https?:\\/\\/[^\\s)<>"']+/g;
  let mm; while ((mm = re.exec(text))) push(mm[0]);
  return {{ links: urls, html: (b.outerHTML || '').slice(0, 2000) }};
}})()"""


# Private, document-local references only: never serialize React values or page content.
# React internals are a versioned adapter, not a fallback when the schema changes.
_COMPOSER = r"""
  const key = Symbol.for('muse-console.composer');
  const empty = () => ({supported: false, ready: false, stable: false});
  const visible = e => e.isConnected && e.offsetParent !== null;
  const enabled = e => !e.disabled && !e.readOnly &&
    !e.closest('[inert],[aria-disabled="true"]');
  const own = (o, k) => {
    const d = Object.getOwnPropertyDescriptor(o, k);
    return d && Object.prototype.hasOwnProperty.call(d, 'value') ? d : null;
  };
  const chain = start => {
    const result = [], seen = new Set();
    for (let f = start; f; f = f.return) {
      if (result.length >= 1024 || seen.has(f)) return null;
      seen.add(f); result.push(f);
    }
    const root = result[result.length - 1];
    if (!root || root.tag !== 3 || !root.stateNode || root.stateNode.current !== root)
      return null;
    // return pointers alone can reach current through a stale branch. Prove
    // each child is actually linked from its parent in the committed tree.
    let budget = 4096;
    for (let i = 0; i + 1 < result.length; i++) {
      let child = result[i + 1].child;
      const siblings = new Set();
      while (child && child !== result[i]) {
        if (--budget < 0 || siblings.has(child)) return null;
        siblings.add(child); child = child.sibling;
      }
      if (!child || --budget < 0) return null;
    }
    return result;
  };
  const inspect = () => {
    const inputs = [...document.querySelectorAll('textarea')].filter(visible);
    if (inputs.length !== 1 || !enabled(inputs[0])) return empty();
    const ta = inputs[0];
    const keys = Object.keys(ta).filter(k => k.startsWith('__reactFiber$'));
    if (keys.length !== 1) return empty();
    const fiber = ta[keys[0]];
    // A DOM node may retain the alternate fiber after a commit. Accept only a
    // branch reaching root.current, including the same host node at its leaf.
    let fibers = chain(fiber);
    if (!fibers) fibers = chain(fiber && fiber.alternate);
    if (!fibers || fibers[0].stateNode !== ta) return empty();
    let chat = null, rpc = null, chatReady = false, rpcReady = false;
    for (const f of fibers) {
      if (f.tag !== 10) continue; // ContextProvider; unknown React tags fail closed.
      const props = f.memoizedProps;
      const d = props && own(props, 'value');
      const v = d && d.value;
      if (!v || typeof v !== 'object') continue;
      const chatKeys = ['chatSubscriptionReady', 'activeHistoryReady', 'approvalSafetyReady'];
      const has = k => Object.prototype.hasOwnProperty.call(v, k);
      // Ancestors are ordered nearest first. A partial inner family must fail
      // closed, not borrow readiness from a complete outer scope.
      if (!chat && chatKeys.some(has)) {
        if (!chatKeys.every(k => own(v, k) && typeof own(v, k).value === 'boolean'))
          return empty();
        chat = f;
        chatReady = chatKeys.every(k => own(v, k).value === true);
      }
      const rpcKeys = ['connectionState', 'isReady', 'connectionAuthority',
                       'sendRequest', 'ensureLiveConnection'];
      const rpcFamily = has('connectionAuthority') ||
        (has('sendRequest') && has('ensureLiveConnection'));
      if (!rpc && rpcFamily) {
        const state = own(v, 'connectionState'), ready = own(v, 'isReady');
        if (!rpcKeys.every(has) || !state || !ready || typeof ready.value !== 'boolean' ||
            !own(v, 'connectionAuthority') ||
            typeof (own(v, 'sendRequest') || {}).value !== 'function' ||
            typeof (own(v, 'ensureLiveConnection') || {}).value !== 'function' ||
            !['connected', 'connecting', 'disconnected'].includes(state.value)) return empty();
        rpc = f;
        rpcReady = state.value === 'connected' && ready.value === true;
      }
    }
    if (!chat || !rpc) return empty();
    const shell = document.querySelector('[data-hatch-shell-hydration-state]');
    // Send is conditionally rendered only after input. Readiness must not
    // require it: bind to the observed composer root instead.
    const root = ta.closest('[data-hatch-composer-root]');
    if (!root || !root.isConnected) return empty();
    const ownedInputs = [...root.querySelectorAll('textarea')];
    if (ownedInputs.length !== 1 || ownedInputs[0] !== ta) return empty();
    return {supported: true, ready: !!shell &&
      shell.getAttribute('data-hatch-shell-hydration-state') === 'hydrated' &&
      chatReady && rpcReady, ta, chat, rpc, root};
  };
  const sameFiber = (a, b) => a === b || !!(a && b && a.alternate === b && b.alternate === a);
  const sample = () => {
    let s;
    try { s = inspect(); } catch (_) { s = empty(); }
    const prev = globalThis[key];
    const stable = !!(s.ready && prev && prev.ready && prev.ta === s.ta &&
      prev.root === s.root &&
      sameFiber(prev.chat, s.chat) && sameFiber(prev.rpc, s.rpc));
    s.filled = !!(stable && prev.filled);
    s.clicked = !!(prev && prev.clicked);
    globalThis[key] = s;
    return {supported: s.supported, ready: !!s.ready, stable};
  };
"""


def composer_probe(*, reset: bool = False) -> str:
    """Return only readiness booleans; require a subsequent stable observation."""
    reset_script = "delete globalThis[key];" if reset else ""
    return "(() => {" + _COMPOSER + reset_script + "return sample();})()"


def _textarea_text(text: str) -> str:
    # native textarea values normalize line endings; preserve all other whitespace.
    return text.replace("\r\n", "\n").replace("\r", "\n")


def fill_input(text: str) -> str:
    """Fill only the same stable, ready composer using React's native setter path."""
    text = _textarea_text(text)
    return (
        "(() => {"
        + _COMPOSER
        + f"""
  const status = sample(), s = globalThis[key];
  if (s.clicked) return {{ok: false, clicked: true}};
  if (!status.stable) return {{ok: false}};
  s.ta.focus();
  const focused = inspect();
  if (!focused.ready || focused.ta !== s.ta || focused.root !== s.root ||
      !sameFiber(focused.chat, s.chat) || !sameFiber(focused.rpc, s.rpc))
    return {{ok: false}};
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
  setter.call(s.ta, {q(text)});
  s.ta.dispatchEvent(new Event('input', {{bubbles: true}}));
  s.filled = true;
  return {{ok: true}};
}})()"""
    )


def click_send(text: str) -> str:
    """Recheck readiness, binding, value and button immediately before one click."""
    text = _textarea_text(text)
    return (
        "(() => {"
        + _COMPOSER
        + f"""
  const status = sample(), s = globalThis[key];
  if (s.clicked) return 'already-clicked';
  if (!status.supported) return 'unsupported';
  if (!status.stable || !s.filled || s.ta.value !== {q(text)}) return 'refill';
  const propKeys = Object.keys(s.ta).filter(k => k.startsWith('__reactProps$'));
  if (propKeys.length !== 1) return 'unsupported';
  const props = s.ta[propKeys[0]], value = props && own(props, 'value');
  if (!value || typeof value.value !== 'string') return 'unsupported';
  if (value.value !== {q(text)}) return 'pending-input';
  const buttons = [...s.root.querySelectorAll('button,[role="button"]')].filter(b => {{
    const slot = b.closest('[data-hatch-composer-action-slot]');
    return visible(b) && b.closest('[data-hatch-composer-root]') === s.root &&
      slot && slot.closest('[data-hatch-composer-root]') === s.root &&
      /^(send|send message|发送|发送消息)$/i.test((b.getAttribute('aria-label') || '').trim());
  }});
  if (buttons.length === 0) return 'disabled';
  if (buttons.length !== 1) return 'unsupported';
  const button = buttons[0];
  if (!enabled(button)) return 'disabled';
  // Mark before invoking application code: an exception must never cause replay.
  s.clicked = true;
  button.click();
  return 'clicked';
}})()"""
    )


def attach_file(b64: str, mime: str, filename: str) -> str:
    return f"""(() => {{
  try {{
    const input = document.querySelector({q(FILE_INPUT)});
    if (!input) return {{ ok: false, err: 'no-file-input' }};
    const bin = atob({q(b64)});
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    const dt = new DataTransfer();
    dt.items.add(new File([bytes], {q(filename)}, {{ type: {q(mime)} }}));
    input.files = dt.files;
    input.dispatchEvent(new Event('change', {{ bubbles: true }}));
    return {{ ok: true }};
  }} catch (e) {{ return {{ ok: false, err: String(e) }}; }}
}})()"""


def fetch_as_base64(url: str, max_bytes: int = 33554432) -> str:
    """Read a media stream with a byte cap before constructing a base64 result."""
    return """(async () => {
      const controller = new AbortController();
      let reader;
      try {
        const r = await fetch(URL, {signal: controller.signal});
        if (!r.ok) throw new Error('media HTTP ' + r.status);
        const limit = LIMIT;
        const declared = Number(r.headers.get('content-length'));
        if (declared > limit) throw new Error('media exceeds byte limit');
        if (!r.body) throw new Error('streaming media body unavailable');
        reader = r.body.getReader();
        const chunks = [];
        let total = 0;
        while (true) {
          const {done, value} = await reader.read();
          if (done) break;
          total += value.byteLength;
          if (total > limit) throw new Error('media exceeds byte limit');
          chunks.push(value);
        }
        let binary = '';
        for (const chunk of chunks) {
          for (let i = 0; i < chunk.length; i += 0x8000)
            binary += String.fromCharCode.apply(null, chunk.subarray(i, i + 0x8000));
        }
        return {ok: true, mime: r.headers.get('content-type') || '', b64: btoa(binary)};
      } catch (e) {
        return {ok: false, err: String(e)};
      } finally {
        controller.abort();
        if (reader) { try { await reader.cancel(); } catch (_) {} }
      }
    })()""".replace("LIMIT", str(max_bytes)).replace("URL", q(url))
