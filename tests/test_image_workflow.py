"""Exercise inline release scripts with mocked GitHub APIs, without YAML dependencies."""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/image.yml"
SHA = "e15893afdd980c7ea542462617efba7b56322867"
OBJECT = "eb99c2caa50ef3e9df04063a61a777d865db1a22"
OTHER = "a" * 40


def job(name):
    text = WORKFLOW.read_text(encoding="utf-8")
    block = text.split("\n  " + name + ":\n", 1)[1]
    # Top-level jobs have exactly two spaces; nested mappings have more.
    return _job_end(block)


def _job_end(block):
    lines = block.splitlines()
    end = next(
        (i for i, line in enumerate(lines) if line.startswith("  ") and not line.startswith("   ")),
        len(lines),
    )
    return "\n".join(lines[:end])


def js(name):
    block = job(name).split("          script: |\n", 1)[1]
    return textwrap.dedent(block)


HARNESS = r"""
const input = JSON.parse(process.env.TEST_INPUT);
const context = {repo: {owner: 'owner', repo: 'repo'}, eventName: input.event || 'workflow_dispatch',
  ref: input.ref || 'refs/heads/main', sha: input.context_sha || 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'};
const outputs = {}, calls = [];
const core = {setOutput: (k,v) => {outputs[k] = v;}};
let refs = 0;
const github = {rest: {
  git: {getRef: async args => {
    calls.push(['ref', args.ref]); refs++;
    if (input.missing) throw new Error('Tag missing');
    return {data: {object: {type: input.type || 'tag', sha:
      (input.move && refs > 1) ? 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb' : input.object}}};
  }},
  repos: {
    getCommit: async args => {
      calls.push(['commit',args.ref]);
      if (!args.ref.startsWith('refs/tags/')) throw new Error('Must use complete ref');
      return {data: {sha: input.commit}};
    },
    compareCommits: async args => {calls.push(['compare',args.base,args.head]);return {data:{status:input.ancestry || 'ahead'}};}
  },
  actions: {listWorkflowRuns: () => {}}
}, paginate: async (fn,args) => {
  calls.push(['ci',args]);
  return input.runs || [{id: 1,head_sha:input.commit,head_branch:'main',event:'push',
    head_repository:{full_name:'owner/repo'},status:'completed',conclusion:'success'}];
}};
const exec = {getExecOutput: async () => ({stdout:input.checkout || input.commit})};
"""


def execute(name, **changes):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required to execute inline workflow JavaScript")
    data = {"commit": SHA, "object": OBJECT, **changes}
    env = {
        **os.environ,
        "TEST_INPUT": json.dumps(data),
        "REQUESTED_TAG": data.get("tag", "v0.4.4"),
        "REQUESTED_PUSH": data.get("push", "true"),
        "SOURCE_SHA": data.get("source", SHA),
        "RELEASE_TAG": data.get("tag", "v0.4.4"),
        "TAG_OBJECT": data.get("expected_object", OBJECT),
    }
    source = (
        HARNESS
        + "\n(async () => { try {\n"
        + js(name)
        + "\nconsole.log(JSON.stringify({outputs,calls}));\n} catch (e) { console.log(JSON.stringify({error:e.message,calls})); } })();"
    )
    result = subprocess.run(
        [node, "-e", source], env=env, capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize("kind", ["commit", "tag"])
def test_manual_existing_release_resolves_full_ref(kind):
    result = execute("resolve", type=kind)
    assert "error" not in result
    assert result["outputs"] == {
        "tag": "v0.4.4",
        "tag_object": OBJECT,
        "source_sha": SHA,
        "image_tag": "0.4.4",
        "release": "true",
    }
    assert ["commit", "refs/tags/v0.4.4"] in result["calls"]


def test_push_uses_trigger_tag_and_commit():
    result = execute("resolve", event="push", ref="refs/tags/v0.4.4", tag="", context_sha=SHA)
    assert result["outputs"]["source_sha"] == SHA
    assert result["outputs"]["release"] == "true"
    assert "error" in execute(
        "resolve", event="push", ref="refs/tags/v0.4.4", tag="", context_sha=OTHER
    )


@pytest.mark.parametrize("event", ["workflow_dispatch", "pull_request"])
def test_edge_or_pr_does_not_resolve_tag(event):
    result = execute("resolve", event=event, tag="", push="false")
    assert result["outputs"]["source_sha"] == OTHER
    assert result["outputs"]["image_tag"] == "edge"
    assert result["outputs"]["release"] == "false"
    assert result["calls"] == []


@pytest.mark.parametrize("tag", ["v0.4", "v0.4.4-rc1", "v00.4.4", "main", " v0.4.4", "v0.4.4\n"])
def test_invalid_tag_rejected(tag):
    assert "error" in execute("resolve", tag=tag)


@pytest.mark.parametrize(
    "options", [{"push": "false"}, {"missing": True}, {"move": True}, {"type": "tree"}]
)
def test_resolver_fails_closed(options):
    assert "error" in execute("resolve", **options)


def test_edge_gate_uses_resolved_dispatch_sha_without_tag_api():
    result = execute("release-gate", tag="", commit=OTHER, source=OTHER)
    assert "error" not in result
    assert not any(call[0] in ("ref", "commit") for call in result["calls"])
    assert ["compare", OTHER, "main"] in result["calls"]


def test_gate_accepts_old_release_not_dispatch_sha():
    result = execute("release-gate", context_sha=OTHER)
    assert "error" not in result
    assert ["compare", SHA, "main"] in result["calls"]
    ci = next(c[1] for c in result["calls"] if c[0] == "ci")
    assert ci["head_sha"] == SHA and ci["branch"] == "main" and ci["event"] == "push"


@pytest.mark.parametrize(
    "options",
    [
        {"move": True},
        {"expected_object": OTHER},
        {"commit": OTHER},
        {"checkout": OTHER},
        {"ancestry": "diverged"},
        {"ancestry": "behind"},
        {"runs": []},
    ],
)
def test_gate_rejects_changed_source_or_missing_ci(options):
    assert "error" in execute("release-gate", **options)


@pytest.mark.parametrize(
    "latest",
    [
        {"status": "in_progress", "conclusion": None},
        {"status": "completed", "conclusion": "failure"},
        {"head_repository": {"full_name": "foreign/repo"}},
        {"head_sha": OTHER},
        {"head_branch": "other"},
        {"event": "pull_request"},
    ],
)
def test_gate_requires_matching_successful_latest_run(latest):
    good = {
        "id": 1,
        "head_sha": SHA,
        "head_branch": "main",
        "event": "push",
        "head_repository": {"full_name": "owner/repo"},
        "status": "completed",
        "conclusion": "success",
    }
    bad = {**good, "id": 2, **latest}
    # Nonmatching runs alone must fail; matching newer unsuccessful runs cannot
    # be hidden by an older successful run.
    runs = [good, bad] if "status" in latest else [bad]
    assert "error" in execute("release-gate", runs=runs)


def test_version_gate_uses_release_checkout(tmp_path, monkeypatch):
    block = (
        job("release-gate").split("          python - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
    )
    source = textwrap.dedent(block)
    # The workflow runs Python 3.12. Host tests also run on 3.10, where tomllib
    # is absent: provide only the parser surface needed by this exact snippet.
    import sys
    import types

    monkeypatch.setitem(
        sys.modules,
        "tomllib",
        types.SimpleNamespace(loads=lambda text: {"project": {"version": text.strip()}}),
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RELEASE_TAG", "v0.4.4")
    (tmp_path / "pyproject.toml").write_text("0.4.4")
    exec(compile(source, "<version-gate>", "exec"), {})
    (tmp_path / "pyproject.toml").write_text("0.4.5")
    with pytest.raises(SystemExit):
        exec(compile(source, "<version-gate>", "exec"), {})


def test_workflow_source_dependencies_and_permissions():
    text = WORKFLOW.read_text()
    smoke = job("smoke")
    assert "needs: resolve" in smoke
    assert "if: github.event_name == 'pull_request'\n      - uses: actions/checkout@v4" in smoke
    assert "ref: ${{ needs.resolve.outputs.source_sha }}" in smoke
    assert "needs: [resolve, smoke]" in job("release-gate")
    assert "needs: [resolve, smoke, release-gate]" in job("publish")
    assert "needs: [resolve, publish, release-gate]" in job("anonymous-pull-smoke")
    for name in ("publish", "anonymous-pull-smoke", "release-gate"):
        assert "ref: ${{ needs.resolve.outputs.source_sha }}" in job(name)
    for name in ("publish", "anonymous-pull-smoke"):
        assert "IMAGE_TAG: ${{ needs.resolve.outputs.image_tag }}" in job(name)
        assert "tag = os.environ['IMAGE_TAG']" in job(name)
    assert "value=${{ needs.resolve.outputs.tag }}" in job("publish")
    metadata = (
        job("publish")
        .split("- uses: docker/metadata-action@v5", 1)[1]
        .split("- name: Log in to GHCR", 1)[0]
    )
    assert "labels: |" in metadata
    assert "org.opencontainers.image.revision=${{ needs.resolve.outputs.source_sha }}" in metadata
    assert "org.opencontainers.image.version=${{ needs.resolve.outputs.image_tag }}" in metadata
    assert (
        "org.opencontainers.image.url=${{ github.server_url }}/${{ github.repository }}"
        "/tree/${{ needs.resolve.outputs.source_sha }}" in metadata
    )
    assert "labels: ${{ steps.meta.outputs.labels }}" in job("publish")
    assert "packages: write" not in job("resolve") + smoke + job("release-gate") + job(
        "anonymous-pull-smoke"
    )
    assert text.count("packages: write") == 1 and text.count("actions: read") == 1
    assert "permissions: {}" in text
    assert "inputs.push == true" in job("publish") and "inputs.push == true" in job("release-gate")
    for name in ("smoke", "anonymous-pull-smoke"):
        assert "runner: ubuntu-latest" in job(name) and "runner: ubuntu-24.04-arm" in job(name)
    assert "docker/login-action" not in job("anonymous-pull-smoke")
    assert "DOCKER_CONFIG=config" in job("anonymous-pull-smoke")
    assert "refs/tags/${tag}" in js("resolve") and "refs/tags/${tag}" in js("release-gate")
    # Embedded Python remains valid on the supported minimum interpreter grammar.
    for part in text.split("<<'PY'\n")[1:]:
        ast.parse(textwrap.dedent(part.split("\n          PY", 1)[0]), feature_version=(3, 10))
