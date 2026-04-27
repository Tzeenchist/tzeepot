# Depot Skill — TODOs & Roadmap

## Completed (v1)
- [x] **Tags Fallback**: Support repositories that use Git Tags instead of GitHub Releases (e.g., Django).
- [x] **Label Override**: Add `--label-override owner/repo "New Label"` command to easily rename watched repos.
- [x] **Security Advisories (CVE)**: Integrate `gh api /repos/{owner}/{repo}/security-advisories` to flag security updates.
- [x] **Upgrade Effort Estimation**: Use Claude to analyze changelogs and estimate "risk" and "time" for the upgrade.
- [x] **Multi-project scan (`--all`)**: Scan multiple project directories at once.
- [x] **`--no-summary` flag**: Skip LLM summarization for high-volume checking.
- [x] **Caching**: Cache PyPI/npm resolution results to speed up `--scan`.

## Completed (v2 — Intelligence Sprint)
- [x] **B0 Hardening**: Fix 8 bare `except:` with specific exception classes; log to stderr format `[depot warn] ExceptionClass: message`.
- [x] **B1 Version delta**: `last_seen_version` + `delta` field in `new_releases` entries.
- [x] **B2 installed_version**: Cross-reference requirements.txt/pyproject.toml/package.json; pinned only; extras strip; first-file-wins priority.
- [x] **B3 MemPalace upgrade log**: Ask for note after acknowledge; store as `depot-{owner}-{name}-upgrade-note` in KG.
- [x] **B4 Digest mode**: `--digest [--days N]` — filtered, prioritized weekly view.
- [x] **B5 Classifier**: SAFE/REVIEW/BREAKING per release based on semver diff.
- [x] **E1 category field**: `"deps"|"tools"|"monitoring"` in config.json; migration on first read; `--set-category`.
- [x] **E2 Multi-release gap**: Paginate GitHub up to 30 releases; show ALL new releases since last_seen.
- [x] **E4 /depot report**: Write DEPOT-STATUS.md with atomic write; default path from config.projects[0].
- [x] **E5 MemPalace warnings**: Query before each release; show ⚠️ if past upgrade note found.
- [x] **E6 /depot stats**: Read-only stats from state.json; no schema change.

## v3 Roadmap

- [ ] **E3: /tzeepot cron** — `gstack CronCreate` triggers `depot --check` daily; writes to `~/.gstack/tzeepot-notifications.log`. Blocked by: need non-tty, non-interactive execution path (no SKILL.md prompts in cron context). Design: Python outputs a digest JSON, separate notifier script formats and prints it.

- [ ] **Most active repo stat** — Add to `--stats`: "version changed most times in last N months." Requires adding `upgrade_history: {repo: [{version, acknowledged_at}]}` to state.json schema. Consider this a v3 schema migration with a one-time migration step.

- [ ] **Auto-PR for SAFE upgrades** — When a SAFE patch is available, auto-open git branch + `pip install --upgrade {pkg}` + PR. Needs: `gh` auth scoping to the project repo, test runner integration to verify the upgrade doesn't break tests, user opt-in flag in config.

- [ ] **Dockerfile/lockfile deep scan** — FROM lines, `poetry.lock`, `pnpm-lock.yaml`, `Cargo.toml`. Extend `scan_project()` with new parsers for each format.

- [ ] **Cross-project aggregate** — `--all` scans all registered project dirs and aggregates into one report. Deduplicates repos that appear in multiple projects.

- [ ] **avg CVE response time stat** — "Average days between CVE release and your acknowledge." Requires `first_seen_at` timestamp per release in state.json. Bundle with Most Active stat (both need schema change).

- [ ] **Poetry format support** — `[tool.poetry.dependencies]` in pyproject.toml uses caret constraints (`^2.0`). Currently not parsed (only `[project.dependencies]` PEP 508 format is supported). Add `poetry.lock` as authoritative pinned version source.
