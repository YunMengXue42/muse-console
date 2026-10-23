"""Execute the production scripts offline against bounded DOM/React fixtures."""

import asyncio
import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from muse_console.drivers.browser import dom
from muse_console.drivers.browser import driver as mod
from muse_console.errors import UpstreamError, UpstreamTimeout

FIXTURE = r"""
let clicks = 0;
class Element {
  constructor(attrs = {}) {
    this.attrs = attrs; this.isConnected = true; this.offsetParent = {};
    this.disabled = false; this.readOnly = false; this.blocked = false;
  }
  getAttribute(k) { return this.attrs[k] ?? null; }
  closest(selector) {
    if (selector === '[inert],[aria-disabled="true"]') return this.blocked ? this : null;
    for (let e = this; e; e = e.parentElement) {
      if (Object.prototype.hasOwnProperty.call(e.attrs, selector.slice(1, -1))) return e;
    }
    return null;
  }
  matches() { return true; }
  focus() {}
  dispatchEvent() {}
  click() { clicks++; }
}
class HTMLTextAreaElement extends Element {
  get value() { return this._value || ''; }
  set value(v) { this._value = String(v).replace(/\r\n?/g, '\n'); }
}
globalThis.HTMLTextAreaElement = HTMLTextAreaElement;
let ta = new HTMLTextAreaElement();
const button = new Element({'aria-label': 'Send message'});
const shell = new Element({'data-hatch-shell-hydration-state': 'hydrated'});
const body = new Element();
const container = new Element({'data-hatch-composer-root': ''});
const slot = new Element({'data-hatch-composer-action-slot': ''});
container.parentElement = body;
slot.parentElement = container;
button.parentElement = slot;
container.querySelectorAll = s => s === 'textarea' ? [ta] :
  (s === 'button,[role="button"]' && ta.value ? [button] : []);
ta.parentElement = container;
const root = {tag: 3, return: null, stateNode: {}};
root.stateNode.current = root;
const rpc = {tag: 10, return: root, memoizedProps: {value: {
  connectionState: 'connected', isReady: true, connectionAuthority: {},
  sendRequest() {}, ensureLiveConnection() {}
}}};
const chat = {tag: 10, return: rpc, memoizedProps: {value: {
  chatSubscriptionReady: true, activeHistoryReady: true, approvalSafetyReady: true
}}};
const host = {tag: 5, return: chat, stateNode: ta};
root.child = rpc; rpc.child = chat; chat.child = host;
ta.__reactFiber$fixture = host;
ta.__reactProps$fixture = {value:''};
ta.dispatchEvent = () => {ta.__reactProps$fixture = {value:ta.value};};
globalThis.document = {
  body,
  querySelectorAll: s => s === 'textarea' ? [ta] : [],
  querySelector: () => shell
};
"""


def javascript(body):
    node = shutil.which("node")
    if not node:
        pytest.fail("Node is required for the offline composer script tests")
    result = subprocess.run(
        [node, "-"],
        input=FIXTURE + "\n" + body,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        # The first node start on a cold Windows runner can take well over 10s.
        timeout=60,
    )
    return json.loads(result.stdout)


def execute(script):
    return f"eval({json.dumps(script)})"


def test_stable_fill_and_single_click():
    value = javascript(
        "const before=container.querySelectorAll('button,[role=\"button\"]').length;"
        f"const a={execute(dom.composer_probe(reset=True))};"
        f"const b={execute(dom.composer_probe())};"
        f"const c={execute(dom.fill_input('hello'))};"
        f"const d={execute(dom.click_send('hello'))};"
        f"const e={execute(dom.click_send('hello'))};"
        "const after=container.querySelectorAll('button,[role=\"button\"]').length;"
        "console.log(JSON.stringify({a,b,c,d,e,clicks,before,after}));"
    )
    assert value["before"] == 0
    assert value["after"] == 1
    assert value["a"] == {"supported": True, "ready": True, "stable": False}
    assert value["b"]["stable"] is True
    assert value["c"] == {"ok": True}
    assert value["d"] == "clicked"
    assert value["e"] == "already-clicked"
    assert value["clicks"] == 1


@pytest.mark.parametrize(
    "text,expected",
    [
        pytest.param("first\r\nsecond", "first\nsecond", id="crlf"),
        pytest.param("first\rsecond", "first\nsecond", id="cr"),
        pytest.param("first\nsecond", "first\nsecond", id="lf"),
        pytest.param("a\r\nb\rc\nd", "a\nb\nc\nd", id="mixed"),
        pytest.param(
            " \t中文\r\n\r\n第二行\r\n尾行\n \t",
            " \t中文\n\n第二行\n尾行\n \t",
            id="chinese-blank-lines-surrounding-whitespace",
        ),
    ],
)
def test_newlines_fill_and_single_click(text, expected):
    value = javascript(
        f"{execute(dom.composer_probe(reset=True))};"
        f"{execute(dom.composer_probe())};"
        f"const fill={execute(dom.fill_input(text))};"
        f"const sent={execute(dom.click_send(text))};"
        f"const replay={execute(dom.click_send(text))};"
        "console.log(JSON.stringify({fill,sent,replay,clicks,"
        "text:ta.value,committed:ta.__reactProps$fixture.value}));"
    )
    assert value == {
        "fill": {"ok": True},
        "sent": "clicked",
        "replay": "already-clicked",
        "clicks": 1,
        "text": expected,
        "committed": expected,
    }


@pytest.mark.parametrize(
    "changed",
    [
        pytest.param(" \t中文\n\n已修改\n \t", id="changed-text"),
        pytest.param("中文\n\n尾行\n \t", id="removed-leading-whitespace"),
        pytest.param(" \t中文\n\n尾行", id="removed-trailing-whitespace"),
        pytest.param(" \t中文\n尾行\n \t", id="removed-blank-line"),
        pytest.param("  中文\n\n尾行\n \t", id="tab-to-spaces"),
    ],
)
def test_newlines_changed_content_refuses_send(changed):
    text = " \t中文\r\n\r尾行\n \t"
    value = javascript(
        f"{execute(dom.composer_probe(reset=True))};"
        f"{execute(dom.composer_probe())};"
        f"const fill={execute(dom.fill_input(text))};"
        f"ta.value={json.dumps(changed)}; ta.dispatchEvent();"
        f"const result={execute(dom.click_send(text))};"
        "console.log(JSON.stringify({fill,result,clicks}));"
    )
    assert value == {"fill": {"ok": True}, "result": "refill", "clicks": 0}


@pytest.mark.parametrize(
    "change",
    [
        "delete chat.memoizedProps.value.activeHistoryReady;",
        "chat.memoizedProps.value.activeHistoryReady = 'true';",
        "rpc.memoizedProps.value.isReady = 1;",
        "rpc.memoizedProps.value.connectionState = 'unknown';",
        "host.return = host;",
        "root.stateNode.current = {};",
        "chat.child = {};",
        "chat.child = {sibling: null}; chat.child.sibling = chat.child;",
        "host.tag = 5; host.stateNode = {};",
        "chat.tag = 99;",
        "delete rpc.memoizedProps.value.sendRequest;",
        "rpc.memoizedProps.value.sendRequest=true;",
        "rpc.memoizedProps.value.ensureLiveConnection=null;",
        "Object.defineProperty(rpc.memoizedProps.value,'connectionAuthority',"
        "{get(){throw Error('PRIVATE_SENTINEL')}});",
        "const absent=host; chat.child={}; let p=chat.child;"
        "for(let i=0;i<4097;i++){p.sibling={};p=p.sibling;} p.sibling=absent;",
        "delete container.attrs['data-hatch-composer-root'];",
        "container.querySelectorAll = s => s === 'textarea' ? [ta, ta] : [];",
        "for(let i=0;i<1024;i++) host.return = {tag:5,return:host.return};",
        "Object.defineProperty(chat.memoizedProps.value, 'activeHistoryReady', "
        "{get(){throw Error('PRIVATE_SENTINEL')}});",
    ],
)
def test_unsupported_fails_closed(change):
    value = javascript(change + f"console.log(JSON.stringify({execute(dom.composer_probe())}));")
    assert value == {"supported": False, "ready": False, "stable": False}


@pytest.mark.parametrize("depth,supported", [(300, True), (1024, True), (1025, False)])
def test_ancestry_budget_with_committed_deep_tree(depth, supported):
    value = javascript(
        "let branch=rpc;"
        f"for(let i=0;i<{depth - 4};i++){{"
        "const parent={tag:5,child:branch,return:root};"
        "branch.return=parent; branch=parent;} root.child=branch;"
        f"console.log(JSON.stringify({execute(dom.composer_probe())}));"
    )
    assert value == {"supported": supported, "ready": supported, "stable": False}


@pytest.mark.parametrize("siblings,supported", [(4093, True), (4094, False)])
def test_child_link_budget_boundary(siblings, supported):
    value = javascript(
        "let branch=host;"
        f"for(let i=0;i<{siblings};i++) branch={{sibling:branch}};"
        "chat.child=branch;"
        f"console.log(JSON.stringify({execute(dom.composer_probe())}));"
    )
    assert value == {"supported": supported, "ready": supported, "stable": False}


@pytest.mark.parametrize(
    "inner_change,expected_supported,expected_ready",
    [
        ("", True, True),
        ("rpc.memoizedProps.value.isReady=false;", True, False),
        ("chat.memoizedProps.value.activeHistoryReady=false;", True, False),
        ("rpc.memoizedProps.value.isReady='true';", False, False),
        ("chat.memoizedProps.value.activeHistoryReady=1;", False, False),
        ("delete chat.memoizedProps.value.chatSubscriptionReady;", False, False),
        ("delete chat.memoizedProps.value.activeHistoryReady;", False, False),
        ("delete chat.memoizedProps.value.approvalSafetyReady;", False, False),
        ("chat.memoizedProps.value={chatSubscriptionReady:true};", False, False),
        ("delete rpc.memoizedProps.value.connectionState;", False, False),
        ("delete rpc.memoizedProps.value.isReady;", False, False),
        ("delete rpc.memoizedProps.value.connectionAuthority;", False, False),
        ("delete rpc.memoizedProps.value.sendRequest;", False, False),
        ("delete rpc.memoizedProps.value.ensureLiveConnection;", False, False),
        ("rpc.memoizedProps.value={connectionAuthority:{}};", False, False),
        (
            "rpc.memoizedProps.value={sendRequest(){},ensureLiveConnection(){}};",
            False,
            False,
        ),
    ],
)
def test_nearest_complete_nested_providers_shadow_outer_scopes(
    inner_change, expected_supported, expected_ready
):
    value = javascript(
        "const outerRpc={tag:10,return:root,memoizedProps:{value:{...rpc.memoizedProps.value}}};"
        "const unrelated={tag:10,return:outerRpc,memoizedProps:{value:{connectionState:'connected'}}};"
        "const outerChat={tag:10,return:unrelated,memoizedProps:{value:{...chat.memoizedProps.value}}};"
        "root.child=outerRpc; outerRpc.child=unrelated; unrelated.child=outerChat;"
        "outerChat.child=rpc; rpc.return=outerChat;"
        + inner_change
        + f"console.log(JSON.stringify({execute(dom.composer_probe())}));"
    )
    assert value == {
        "supported": expected_supported,
        "ready": expected_ready,
        "stable": False,
    }


def test_unrelated_nearer_connection_provider_is_not_rpc():
    value = javascript(
        "const unrelated={tag:10,return:rpc,child:chat,"
        "memoizedProps:{value:{connectionState:'connected'}}};"
        "chat.return=unrelated; rpc.child=unrelated;"
        f"console.log(JSON.stringify({execute(dom.composer_probe())}));"
    )
    assert value == {"supported": True, "ready": True, "stable": False}


@pytest.mark.parametrize(
    "change",
    [
        "chat.memoizedProps.value.chatSubscriptionReady = false;",
        "chat.memoizedProps.value.activeHistoryReady = false;",
        "chat.memoizedProps.value.approvalSafetyReady = false;",
        "rpc.memoizedProps.value.isReady = false;",
        "rpc.memoizedProps.value.connectionState = 'connecting';",
        "shell.attrs['data-hatch-shell-hydration-state'] = 'loading';",
    ],
)
def test_pending_is_supported_not_ready(change):
    value = javascript(change + f"console.log(JSON.stringify({execute(dom.composer_probe())}));")
    assert value == {"supported": True, "ready": False, "stable": False}


def test_committed_alternate_selected_not_stale_ready_branch():
    value = javascript(
        "const oldRoot = {tag:3,return:null,stateNode:root.stateNode};"
        "const oldChat = {tag:10,return:oldRoot,memoizedProps:chat.memoizedProps};"
        "const oldHost = {tag:5,return:oldChat,stateNode:ta,alternate:host};"
        "host.alternate=oldHost; ta.__reactFiber$fixture=oldHost;"
        "chat.memoizedProps={value:{chatSubscriptionReady:false,activeHistoryReady:true,"
        "approvalSafetyReady:true}};"
        f"console.log(JSON.stringify({execute(dom.composer_probe())}));"
    )
    assert value == {"supported": True, "ready": False, "stable": False}


@pytest.mark.parametrize(
    "change,expected",
    [
        ("button.blocked=true;", "disabled"),
        ("button.disabled=true;", "disabled"),
        ("delete slot.attrs['data-hatch-composer-action-slot'];", "disabled"),
        ("button.attrs['aria-label']='Resend';", "disabled"),
        ("button.offsetParent=null;", "disabled"),
        ("button.parentElement=body;", "disabled"),
        (
            "container.querySelectorAll=s=>s==='textarea'?[ta]:[button,button];",
            "unsupported",
        ),
        ("ta.value='';", "refill"),
        ("chat.memoizedProps.value.activeHistoryReady=false;", "refill"),
        (
            "const old=ta; ta=new HTMLTextAreaElement(); ta.parentElement=container;"
            "ta.__reactFiber$fixture={...host,stateNode:ta}; chat.child=ta.__reactFiber$fixture;",
            "refill",
        ),
    ],
)
def test_click_rechecks_without_replay(change, expected):
    value = javascript(
        f"{execute(dom.composer_probe(reset=True))};"
        f"{execute(dom.composer_probe())};"
        f"{execute(dom.fill_input('hello'))};"
        + change
        + f"const result={execute(dom.click_send('hello'))};"
        "console.log(JSON.stringify({result,clicks}));"
    )
    assert value == {"result": expected, "clicks": 0}


def test_click_exception_disarms_before_application_code():
    value = javascript(
        f"{execute(dom.composer_probe(reset=True))};"
        f"{execute(dom.composer_probe())};"
        f"{execute(dom.fill_input('hello'))};"
        "button.click=()=>{clicks++;throw Error('PRIVATE_SENTINEL')};"
        f"try{{{execute(dom.click_send('hello'))}}}catch(_){{}}"
        f"const result={execute(dom.click_send('hello'))};"
        "console.log(JSON.stringify({result,clicks}));"
    )
    assert value == {"result": "already-clicked", "clicks": 1}


def test_click_waits_for_react_controlled_input_commit():
    value = javascript(
        f"{execute(dom.composer_probe(reset=True))};"
        f"{execute(dom.composer_probe())};"
        "ta.dispatchEvent=()=>{};"
        f"{execute(dom.fill_input('hello'))};"
        f"const pending={execute(dom.click_send('hello'))};"
        "ta.__reactProps$fixture={value:'hello'};"
        f"const accepted={execute(dom.click_send('hello'))};"
        "console.log(JSON.stringify({pending,accepted,clicks}));"
    )
    assert value == {"pending": "pending-input", "accepted": "clicked", "clicks": 1}


def test_uncommitted_input_rerender_recovers_without_click_replay():
    value = javascript(
        f"{execute(dom.composer_probe(reset=True))};"
        f"{execute(dom.composer_probe())};"
        f"{execute(dom.fill_input('hello'))};"
        "chat.child={};"
        f"const blocked={execute(dom.click_send('hello'))};"
        "chat.child=host;"
        f"{execute(dom.composer_probe())};"
        f"{execute(dom.composer_probe())};"
        f"const fill={execute(dom.fill_input('hello'))};"
        f"const clicked={execute(dom.click_send('hello'))};"
        "console.log(JSON.stringify({blocked,fill,clicked,clicks}));"
    )
    assert value == {
        "blocked": "unsupported",
        "fill": {"ok": True},
        "clicked": "clicked",
        "clicks": 1,
    }


def test_focus_losing_readiness_does_not_fill_old_node():
    value = javascript(
        f"{execute(dom.composer_probe(reset=True))};"
        f"{execute(dom.composer_probe())};"
        "ta.focus=()=>{chat.memoizedProps.value.activeHistoryReady=false;};"
        f"const result={execute(dom.fill_input('hello'))};"
        "console.log(JSON.stringify({result,chars:ta.value.length,clicks}));"
    )
    assert value == {"result": {"ok": False}, "chars": 0, "clicks": 0}


def test_stale_branch_reaching_current_root_is_rejected():
    value = javascript(
        "const stale={...host}; ta.__reactFiber$fixture=stale;"
        f"console.log(JSON.stringify({execute(dom.composer_probe())}));"
    )
    assert value == {"supported": False, "ready": False, "stable": False}


def test_unrelated_context_content_is_never_read_or_returned():
    value = javascript(
        "Object.defineProperty(chat.memoizedProps.value,'transcript',"
        "{get(){throw Error('PRIVATE_SENTINEL')}});"
        f"console.log(JSON.stringify({execute(dom.composer_probe())}));"
    )
    assert value == {"supported": True, "ready": True, "stable": False}


@pytest.fixture
def clock(monkeypatch):
    now = [0.0]

    async def sleep(delay):
        now[0] += delay

    monkeypatch.setattr(mod.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(mod.asyncio, "sleep", sleep)
    return now


class Session:
    def __init__(self, statuses, clicks=None):
        self.statuses = list(statuses)
        self.clicks = list(clicks or ["clicked"])
        self.calls = []

    async def evaluate(self, script, *, timeout):
        assert 0 < timeout <= 1
        self.calls.append(script)
        if script in (dom.composer_probe(), dom.composer_probe(reset=True)):
            return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        if script == dom.fill_input("hello"):
            return {"ok": True}
        assert script == dom.click_send("hello")
        return self.clicks.pop(0)


READY = {"supported": True, "ready": True, "stable": True}
PENDING = {"supported": True, "ready": False, "stable": False}


@pytest.mark.asyncio
async def test_driver_waits_and_clicks_once(clock):
    session = Session([PENDING, READY])
    driver = mod.BrowserDriver(SimpleNamespace(page_ready_timeout=1))
    await driver._send(SimpleNamespace(session=session), "hello")
    assert session.calls.count(dom.fill_input("hello")) == 1
    assert session.calls.count(dom.click_send("hello")) == 1
    assert clock[0] > 0


@pytest.mark.asyncio
async def test_driver_refills_only_before_click(clock):
    session = Session([READY], ["refill", "clicked"])
    driver = mod.BrowserDriver(SimpleNamespace(page_ready_timeout=1))
    await driver._send(SimpleNamespace(session=session), "hello")
    assert session.calls.count(dom.fill_input("hello")) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("result", ["already-clicked", None, "unknown"])
async def test_driver_never_retries_uncertain_submission(clock, result):
    session = Session([READY], [result])
    driver = mod.BrowserDriver(SimpleNamespace(page_ready_timeout=1))
    with pytest.raises(UpstreamError) as error:
        await driver._send(SimpleNamespace(session=session), "hello")
    assert session.calls.count(dom.click_send("hello")) == 1
    assert error.value.retryable is False


@pytest.mark.asyncio
async def test_driver_rechecks_uncommitted_branch_before_any_click(clock):
    session = Session([READY], ["unsupported", "clicked"])
    driver = mod.BrowserDriver(SimpleNamespace(page_ready_timeout=1))
    await driver._send(SimpleNamespace(session=session), "hello")
    assert session.calls.count(dom.fill_input("hello")) == 2
    assert session.calls.count(dom.click_send("hello")) == 2


@pytest.mark.asyncio
async def test_driver_unsupported_never_fills(clock):
    session = Session([{"supported": False, "ready": False, "stable": False}])
    driver = mod.BrowserDriver(SimpleNamespace(page_ready_timeout=1))
    with pytest.raises(UpstreamTimeout, match="composer_ready_timeout"):
        await driver._send(SimpleNamespace(session=session), "hello")
    assert clock[0] == 1
    assert dom.fill_input("hello") not in session.calls
    assert dom.click_send("hello") not in session.calls


@pytest.mark.asyncio
async def test_driver_schema_can_appear_during_hydration(clock):
    session = Session([{"supported": False, "ready": False, "stable": False}, READY])
    driver = mod.BrowserDriver(SimpleNamespace(page_ready_timeout=1))
    await driver._send(SimpleNamespace(session=session), "hello")
    assert session.calls.count(dom.fill_input("hello")) == 1
    assert session.calls.count(dom.click_send("hello")) == 1


@pytest.mark.asyncio
async def test_driver_timeout_is_bounded_without_submission(clock):
    session = Session([PENDING])
    driver = mod.BrowserDriver(SimpleNamespace(page_ready_timeout=1))
    with pytest.raises(UpstreamTimeout, match="composer_ready_timeout"):
        await driver._send(SimpleNamespace(session=session), "hello")
    assert clock[0] == 1
    assert dom.fill_input("hello") not in session.calls


@pytest.mark.asyncio
async def test_driver_cancellation_never_submits(clock):
    class CancelledSession:
        async def evaluate(self, script, *, timeout):
            raise asyncio.CancelledError

    driver = mod.BrowserDriver(SimpleNamespace(page_ready_timeout=1))
    with pytest.raises(asyncio.CancelledError):
        await driver._send(SimpleNamespace(session=CancelledSession()), "hello")


@pytest.mark.asyncio
async def test_driver_evaluate_timeout_sanitized(clock):
    class TimeoutSession:
        async def evaluate(self, script, *, timeout):
            raise TimeoutError("PRIVATE_SENTINEL")

    driver = mod.BrowserDriver(SimpleNamespace(page_ready_timeout=1))
    with pytest.raises(UpstreamTimeout, match="composer_ready_timeout") as error:
        await driver._send(SimpleNamespace(session=TimeoutSession()), "hello")
    assert "PRIVATE_SENTINEL" not in str(error.value)
    assert error.value.retryable is False


@pytest.mark.asyncio
async def test_driver_click_cdp_uncertainty_does_not_failover(clock):
    class UncertainSession(Session):
        async def evaluate(self, script, *, timeout):
            if script == dom.click_send("hello"):
                raise mod.CDPError("PRIVATE_SENTINEL")
            return await super().evaluate(script, timeout=timeout)

    driver = mod.BrowserDriver(SimpleNamespace(page_ready_timeout=1))
    with pytest.raises(UpstreamError, match="composer_cdp_uncertain") as error:
        await driver._send(SimpleNamespace(session=UncertainSession([READY])), "hello")
    assert error.value.retryable is False
    assert "PRIVATE_SENTINEL" not in str(error.value)
