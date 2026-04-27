# Tzeepot — GitHub Release Tracker

Track new releases of libraries used in your project.

## Commands

- `/tzeepot` — Check for new releases in the current project.
- `/tzeepot digest [--days N]` — Weekly top-5 prioritized view (default 7 days).
- `/tzeepot stats` — Show upgrade statistics from state.json.
- `/tzeepot report` — Write DEPOT-STATUS.md to the project folder.
- `/tzeepot category <owner/repo> <deps|tools|monitoring>` — Reclassify a repo.
- `/tzeepot list` — List all watched repos with health indicators and descriptions.
- `/tzeepot versions` — Compact one-line list with versions and links.
- `/tzeepot info` — Show project names and descriptions only.
- `/tzeepot scan` — Auto-detect repos from requirements.txt / package.json / pyproject.toml.
- `/tzeepot add <owner/repo> [--description "Text"]` — Manually add a repository.
- `/tzeepot remove <owner/repo>` — Remove a repo from the watch list.
- `/tzeepot label <owner/repo> <new_label>` — Rename a watched repo.
- `/tzeepot desc <owner/repo> <text>` — Set or update the description.
- `/tzeepot add-project <path>` — Register a project directory for `/tzeepot scan --all`.
- `/tzeepot help` — Show this help.

## Key Principles

- **Automatic Russian descriptions**: When a repo is added or scanned, I MUST ensure it has a concise, professional description in Russian. If GitHub description is in English — translate. If absent — generate from repo name/contents. Always save with `--set-desc`.
- **Hints**: End every response with `💡 Hint: Try /tzeepot list or /tzeepot help`.
- **Graceful MCP degradation**: If any `mcp__mempalace__*` call fails or times out, skip it silently — never block the check output.

## Main Workflow (/tzeepot)

1. Run `python3 ~/.claude/skills/tzeepot/depot.py --check --project-dir "$(pwd)"`.
   - With `/tzeepot --no-summary`: skip changelog summarization.
2. Parse JSON output.
   - If `no_repos_configured` → suggest `/tzeepot scan`.
   - If `baseline_established` → inform user baseline is set; run again later.
3. **Before displaying each release with `new_releases`**:
   - Call `mcp__mempalace__mempalace_kg_query` with `object="depot-{owner}-{name}-upgrade-note"` (replace `/` with `-`).
   - If a fact is found: show `⚠️ Прошлый апгрейд {date}: {note}` before the changelog.
   - If MCP call fails: skip silently.
4. Display releases in two sections:
   - **🚀 ТРЕБУЕТСЯ ОБНОВЛЕНИЕ** — repos with `is_dependency: true`.
   - **📰 ЧТО НОВОГО** — all other repos.
   - For each entry show: `{label} {installed_version} → {latest_version}` (omit installed if null).
   - Per-release badge: 🟢 SAFE / 🟡 REVIEW / 🔴 BREAKING (from `upgrade_risk`).
   - **⚠️ SECURITY**: highlight any `is_security: true` prominently.
   - If `truncated: true`: show `⚠️ {repo}: 30+ releases since last check. Full history: https://github.com/{repo}/releases`.
   - If multiple releases in `new_releases` list: show each with version + risk badge.
5. Summarize changelogs for top 5 releases across all repos (unless `no_summary: true`):
   - Prompt: "Summarize in 3 bullets: breaking changes, new features, notable fixes. Flag breaking changes with ⚠️."
   - For security releases add: "Highlight the security vulnerabilities fixed."
   - For each summary provide a **Risk Score** (1–10) and **Estimated Time** (e.g., 5m, 1h).
6. Ask for decision on each release:
   - **Upgrade now** → record with `--acknowledge` (see below); ask for optional note.
   - **Snooze N days** → record with `--snooze`.
   - **Dismiss** → record with `--dismiss`.

### After Acknowledge

After recording an acknowledge, ask:
> "Any issues with this upgrade? (optional note for future reference)"

If user provides a note:
- Call `mcp__mempalace__mempalace_kg_add` with:
  - `object`: `"depot-{owner}-{name}-upgrade-note"` (slashes → dashes)
  - `predicate`: `"last_upgrade_result"`
  - `value`: `"{YYYY-MM-DD}: {user_note}"`

### Recording Decisions

```bash
echo '{"repo":"owner/repo","version":"1.2.3"}' | python3 ~/.claude/skills/tzeepot/depot.py --acknowledge
echo '{"repo":"owner/repo","version":"1.2.3"}' | python3 ~/.claude/skills/tzeepot/depot.py --dismiss
echo '{"repo":"owner/repo","version":"1.2.3","days":30}' | python3 ~/.claude/skills/tzeepot/depot.py --snooze
```

## Digest Workflow (/tzeepot digest)

Run: `python3 ~/.claude/skills/tzeepot/depot.py --digest --days 7 --project-dir "$(pwd)"`

- Shows only unacknowledged, non-snoozed releases published in the last N days.
- Sorted: security first → BREAKING → REVIEW → SAFE → newest date.
- Display top 5 by convention.
- Includes `installed_version` when available (same cross-reference as `--check`).

## Stats Workflow (/tzeepot stats)

Run: `python3 ~/.claude/skills/tzeepot/depot.py --stats`

Display the output as-is (plain text). No further processing needed.

## Report Workflow (/tzeepot report)

Run: `python3 ~/.claude/skills/tzeepot/depot.py --report`

Confirm to the user which file was written (path printed by the command).

## Category Workflow (/tzeepot category)

Run: `python3 ~/.claude/skills/tzeepot/depot.py --set-category owner/repo <deps|tools|monitoring>`

Valid categories:
- `deps` — project dependency (tracked in requirements.txt / package.json)
- `tools` — installed tool (gstack, codex, etc.)
- `monitoring` — informational watch (news, ecosystem)

## Maintenance

### Renaming and Descriptions
- Rename: `python3 ~/.claude/skills/tzeepot/depot.py --label-override owner/repo "New Label"`
- Description: `python3 ~/.claude/skills/tzeepot/depot.py --set-desc owner/repo "Text in Russian"`
- Compact list: `python3 ~/.claude/skills/tzeepot/depot.py --list --compact`

### Broken repos
If a repository returns a warning (404 / unreachable): list it in a footer. User can remove with `/tzeepot remove owner/repo`.

### Manual Test Checklist (SKILL.md layer — no automated coverage)
- Run `/tzeepot` → verify SAFE/REVIEW/BREAKING badges appear correctly.
- Run `/tzeepot` on a repo with a MemPalace note → verify ⚠️ warning appears.
- Acknowledge an upgrade with a note → verify `mcp__mempalace__mempalace_kg_add` is called.
- Run `/tzeepot` on a repo with `truncated: true` → verify GitHub releases link appears.

💡 **Hint**: Try `/tzeepot list` or `/tzeepot help` for more options.
