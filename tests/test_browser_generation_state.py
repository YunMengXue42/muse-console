"""Execute production CHAT_STATE offline with localized Stop DOM fixtures."""

import json
import shutil
import subprocess

import pytest

from muse_console.drivers.browser import dom

# Minimal attribute-selector DOM, not a stub returning true for STOP_BUTTON.
# Unknown selector syntax fails the test instead of silently matching.
FIXTURE = r"""
const elements = ELEMENTS;
function matches(element, selector) {
  if (selector === '*') return true;
  const m = selector.match(/^(\w+)?\[([\w-]+)(\*=|\^=|=)"([^"]*)"( i)?\]$/);
  if (!m) throw new Error('unsupported fixture selector');
  if (m[1] && element.tag.toLowerCase() !== m[1].toLowerCase()) return false;
  let actual = element.attrs[m[2]];
  if (actual === undefined) return false;
  let expected = m[4];
  if (m[5]) { actual = actual.toLowerCase(); expected = expected.toLowerCase(); }
  return m[3] === '=' ? actual === expected :
    (m[3] === '*=' ? actual.includes(expected) : actual.startsWith(expected));
}
globalThis.document = {
  body: {innerText: ''},
  querySelectorAll(selector) {
    return elements.filter(e => selector.split(',').some(s => matches(e, s.trim())));
  },
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
};
globalThis.getComputedStyle = () => ({overflowY: 'visible'});
"""


@pytest.mark.parametrize(
    "elements,generating",
    [
        (
            [
                {
                    "tag": "BUTTON",
                    "attrs": {
                        "data-testid": "hatch-composer-stop-button",
                        "aria-label": "停止",
                    },
                    "disabled": False,
                }
            ],
            True,
        ),
        ([{"tag": "BUTTON", "attrs": {"aria-label": "Stop generating"}}], True),
        ([{"tag": "BUTTON", "attrs": {"aria-label": "sToP"}}], True),
        ([], False),
        ([{"tag": "BUTTON", "attrs": {"aria-label": "Send"}}], False),
        ([{"tag": "BUTTON", "attrs": {"data-testid": "hatch-composer-send-button"}}], False),
        ([{"tag": "DIV", "attrs": {"aria-label": "Stop"}}], False),
    ],
    ids=[
        "localized-testid",
        "legacy-English",
        "legacy-case",
        "absent",
        "send",
        "other-testid",
        "not-button",
    ],
)
def test_production_chat_state_detects_generation(elements, generating):
    node = shutil.which("node")
    if not node:
        pytest.fail("Node is required for the offline generation-state script tests")
    script = FIXTURE.replace("ELEMENTS", json.dumps(elements, ensure_ascii=True))
    script += f"\nconsole.log(JSON.stringify(eval({json.dumps(dom.CHAT_STATE)})));"
    result = subprocess.run(
        [node, "-e", script], capture_output=True, text=True, check=True, timeout=10
    )
    assert json.loads(result.stdout) == {
        "agentCount": 0,
        "lastText": "",
        "generating": generating,
        "approval": False,
        "attachments": [],
        "tail": "",
    }
