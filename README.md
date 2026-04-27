# Tzeepot

GitHub release tracker skill for [Claude Code](https://claude.ai/code). Watches repositories you care about and surfaces new releases — with risk badges, security alerts, changelog summaries, and upgrade history.

## What it does

- Checks GitHub for new releases of repos you track
- Classifies each release as 🟢 SAFE / 🟡 REVIEW / 🔴 BREAKING based on semver
- Highlights ⚠️ security advisories
- Scans your `requirements.txt` / `pyproject.toml` / `package.json` to auto-detect dependencies
- Summarizes changelogs via Claude (risk score + estimated upgrade time)
- Tracks your upgrade decisions: acknowledge, snooze, or dismiss

## Requirements

- [Claude Code](https://claude.ai/code) CLI
- Python 3.12+
- `packaging` library: `pip install packaging`
- `gh` CLI (for security advisories): [cli.github.com](https://cli.github.com)

## Installation

```bash
git clone https://github.com/Tzeenchist/tzeepot ~/.claude/skills/tzeepot
pip install packaging
```

Then register the skill in your Claude Code settings (`~/.claude/settings.json`):

```json
{
  "skills": [
    { "name": "tzeepot", "path": "~/.claude/skills/tzeepot" }
  ]
}
```

## Usage

```
/tzeepot                          — Check for new releases
/tzeepot digest [--days N]        — Weekly digest, top 5 prioritized
/tzeepot scan                     — Auto-detect repos from project files
/tzeepot add owner/repo           — Add a repo manually
/tzeepot remove owner/repo        — Remove a repo
/tzeepot list                     — List watched repos with status
/tzeepot versions                 — Compact list with versions and links
/tzeepot stats                    — Upgrade statistics
/tzeepot report                   — Write DEPOT-STATUS.md to project folder
/tzeepot label owner/repo "Name"  — Rename a watched repo
/tzeepot category owner/repo deps|tools|monitoring
/tzeepot help                     — Full command reference
```

## How releases are classified

| Badge | Meaning | When |
|-------|---------|------|
| 🟢 SAFE | Patch release | 3rd digit bumped (1.2.3 → 1.2.4) |
| 🟡 REVIEW | Minor release | 2nd digit bumped (1.2.x → 1.3.0) |
| 🔴 BREAKING | Major release | 1st digit bumped (1.x.x → 2.0.0) |

## State files

Tzeepot stores its data in `~/.claude/skills/tzeepot/`:

| File | Purpose |
|------|---------|
| `config.json` | Watched repos and project paths |
| `state.json` | Last-seen versions and snooze records |
| `cache.json` | Cached PyPI/npm resolution results |

These are excluded from git via `.gitignore`.

## License

MIT
