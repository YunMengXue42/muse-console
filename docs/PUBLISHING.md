# Public release checklist

[简体中文](PUBLISHING.zh-CN.md) · [README](../README.md)

This checklist prepares source publication. It does not change repository visibility, publish a package, rotate credentials or modify a running deployment.

## 1. Source and history

From the repository root:

```bash
python tools/check_publication.py
python tools/check_publication.py --history
```

The guard scans tracked working-tree files, or every blob and commit/tag metadata reachable from **local** refs with `--history`. It reports paths, object IDs and line numbers, never matched values. Patterns cover selected token/key formats, long session cookies, private AI-session references and private runtime paths. It is not a complete secret scanner, a security audit or proof that a value is inactive.

- Review false positives manually; use short, explicitly synthetic credentials in tests.
- Scan all branches, tags and deleted historical files, not just the latest tree. Fetch relevant remote refs before an offline audit if authorized; an incomplete local clone cannot certify remote history.
- Review commit/tag messages, author/committer identities, tagger emails and Git notes. The guard reports hashed non-noreply commit/tag email identities for review; it does not rewrite them or decide which identities may be public.
- Review images, fonts, archives, generated JavaScript, source maps and third-party license notices manually. Binary contents are not comprehensively decoded by the guard.
- Do not remove upstream attribution or rewrite third-party authorship merely to make history look new.

## 2. Private data boundary

Do not commit or attach `.env`, `data/`, `data-preview/`, SQLite databases/sidecars, cookies, HAR captures, browser profiles, generated media, deployment keys, backups or live acceptance logs. `.gitignore` only prevents accidental new tracking; it cannot erase history or make a previously tracked secret safe.

Use `git archive` for a tracked source export instead of zipping the working directory. Inspect the actual archive before uploading it. A complete stopped data backup contains secrets even when database payloads are encrypted: keep it separate from all public artifacts.

If a real secret has ever been committed:

1. Revoke/rotate the credential with the owner's approval; rewriting history is not revocation.
2. Prepare a private backup and a clean candidate copy.
3. Obtain approval before rewriting shared branches/tags or force-pushing. Communicate changed commit IDs and deployment/rollback references to collaborators.
4. Re-scan the cleaned history. Check remote refs, releases, attachments, CI logs/artifacts and caches; old clones/forks may still retain the secret.

Do not delete ignored local operational data as a substitute for cleaning publication contents.

## 3. Verification and documentation

- Follow [CONTRIBUTING.md](../CONTRIBUTING.md) for locked installs, formatting, linting, backend/frontend/extension tests and packaging checks.
- Keep English/Chinese README, deployment, contribution and security guides aligned. Verify relative links and version references.
- Confirm no example contains a personal production hostname/IP, account email or local absolute path. Use `example.com`, loopback and explicit placeholders.
- Preserve [LICENSE](../LICENSE), upstream attribution, bundled font licenses and dependency notices.
- Inspect wheel/source archive contents. No data directories, profiles, secrets, virtual environments or `node_modules` should be included.
- Distinguish offline/mock checks from real authentication/generation and renewal acceptance; do not advertise unverified capabilities.

## 4. Hosting settings and final approval

While the repository is still private, review GitHub releases (including drafts/assets), Actions logs/artifacts, issue/PR attachments, wiki, Pages, repository description/homepage and enabled integrations separately. The local guard cannot inspect these surfaces.

Review Actions permissions (read-only by default), untrusted pull-request behavior, secret scanning/push protection where available, branch protection and the private vulnerability reporting entry point. Confirm [SECURITY.md](../SECURITY.md) matches the features actually enabled. Do not enable new integrations or permissions without approval.

Obtain the repository owner's explicit final approval before changing visibility. GitHub warns that visibility changes affect forks, Actions history and other repository features: read the current confirmation screen and official documentation before submitting. Source publication does not publish a deployment, a PyPI package, a container image or a browser extension.
