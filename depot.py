import os
import sys
import json
import argparse
import subprocess
import datetime
import re
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    from packaging import version as pkg_version
except ImportError:
    print("Error: 'packaging' required. Install: pip install packaging", file=sys.stderr)
    sys.exit(1)

SKILL_DIR = Path.home() / ".claude" / "skills" / "tzeepot"
CONFIG_FILE = SKILL_DIR / "config.json"
STATE_FILE = SKILL_DIR / "state.json"
CACHE_FILE = SKILL_DIR / "cache.json"

VALID_CATEGORIES = {"tools", "deps", "monitoring"}
RISK_SORT = {"BREAKING": 0, "REVIEW": 1, "SAFE": 2}


def load_json(path, default=None):
    if not path.exists():
        return default if default is not None else {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"[tzeepot warn] JSONDecodeError: {e}", file=sys.stderr)
        return default if default is not None else {}


def atomic_write_json(path, data):
    tmp_path = path.with_suffix(".json.tmp")
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, path)


def atomic_write_text(path, content):
    tmp_path = path.with_name(path.name + ".tmp")
    with open(tmp_path, "w") as f:
        f.write(content)
    os.replace(tmp_path, path)


def normalize_github_url(url):
    if not url:
        return None
    url = re.sub(r"^git\+", "", url)
    url = re.sub(r"^github:", "https://github.com/", url)
    if "github.com" not in url:
        return None
    url = url.split("?")[0].split("#")[0].rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    match = re.search(r"github\.com/([^/]+)/([^/]+)$", url)
    return f"{match.group(1)}/{match.group(2)}" if match else None


def get_github_releases(repo_slug, page=1, per_page=10):
    cmd = ["gh", "api", f"/repos/{repo_slug}/releases?per_page={per_page}&page={page}"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        return {"error": str(e)}
    except json.JSONDecodeError as e:
        print(f"[tzeepot warn] JSONDecodeError: {e}", file=sys.stderr)
        return {"error": str(e)}


def get_github_tags(repo_slug):
    cmd = ["gh", "api", f"/repos/{repo_slug}/tags?per_page=10"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        tags = json.loads(result.stdout)
        return [
            {"tag_name": t["name"], "published_at": None, "body": "", "draft": False, "prerelease": False}
            for t in tags
        ]
    except subprocess.CalledProcessError as e:
        return {"error": str(e)}
    except json.JSONDecodeError as e:
        print(f"[tzeepot warn] JSONDecodeError: {e}", file=sys.stderr)
        return {"error": str(e)}


def get_github_advisories(repo_slug):
    cmd = ["gh", "api", f"/repos/{repo_slug}/security-advisories"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        print(f"[tzeepot warn] CalledProcessError: {e}", file=sys.stderr)
        return []
    except json.JSONDecodeError as e:
        print(f"[tzeepot warn] JSONDecodeError: {e}", file=sys.stderr)
        return []


def get_github_commits(repo_slug):
    cmd = ["gh", "api", f"/repos/{repo_slug}/commits?per_page=5"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        commits = json.loads(result.stdout)
        return [
            {
                "tag_name": c["sha"][:7],
                "published_at": c["commit"]["author"]["date"],
                "body": c["commit"]["message"],
                "draft": False,
                "prerelease": False,
            }
            for c in commits
        ]
    except subprocess.CalledProcessError as e:
        return {"error": str(e)}
    except json.JSONDecodeError as e:
        print(f"[tzeepot warn] JSONDecodeError: {e}", file=sys.stderr)
        return {"error": str(e)}


def get_repo_metadata(repo_slug):
    cmd = ["gh", "api", f"/repos/{repo_slug}"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        return {"description": data.get("description"), "pushed_at": data.get("pushed_at")}
    except subprocess.CalledProcessError as e:
        print(f"[tzeepot warn] CalledProcessError: {e}", file=sys.stderr)
        return None
    except json.JSONDecodeError as e:
        print(f"[tzeepot warn] JSONDecodeError: {e}", file=sys.stderr)
        return None


def resolve_pypi_to_github(pkg):
    try:
        import urllib.request
        with urllib.request.urlopen(f"https://pypi.org/pypi/{pkg}/json", timeout=10) as response:
            data = json.loads(response.read().decode())
        info = data.get("info", {})
        urls = info.get("project_urls") or {}
        for key in ("Source Code", "Source", "Repository", "Code", "Homepage"):
            url = urls.get(key) or info.get("home_page")
            if not url:
                continue
            if "github.com" not in url:
                continue
            slug = normalize_github_url(url)
            if slug:
                return slug
        return None
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        print(f"[tzeepot warn] URLError: {e}", file=sys.stderr)
        return None
    except json.JSONDecodeError as e:
        print(f"[tzeepot warn] JSONDecodeError: {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[tzeepot warn] Exception: {e}", file=sys.stderr)
        return None


def resolve_npm_to_github(pkg):
    cmd = ["npm", "view", pkg, "repository.url", "--json"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            url = result.stdout.strip().strip('"')
            return normalize_github_url(url)
        return None
    except subprocess.CalledProcessError as e:
        print(f"[tzeepot warn] CalledProcessError: {e}", file=sys.stderr)
        return None
    except subprocess.TimeoutExpired as e:
        print(f"[tzeepot warn] TimeoutExpired: {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[tzeepot warn] Exception: {e}", file=sys.stderr)
        return None


def classify_upgrade(last_version, new_version):
    """Return SAFE/REVIEW/BREAKING based on semver diff."""
    last_clean = (last_version or "").lstrip("v")
    new_clean = (new_version or "").lstrip("v")
    if re.search(r"(a|b|rc|alpha|beta|dev)\d*", new_clean, re.IGNORECASE):
        return "REVIEW"
    if not (last_clean and new_clean):
        return "REVIEW"
    if not (re.match(r"^\d+\.\d+", last_clean) and re.match(r"^\d+\.\d+", new_clean)):
        return "REVIEW"
    try:
        last_v = pkg_version.parse(last_clean)
        new_v = pkg_version.parse(new_clean)
        if new_v.major > last_v.major:
            return "BREAKING"
        if new_v.minor > last_v.minor:
            return "REVIEW"
        return "SAFE"
    except Exception:
        return "REVIEW"


def scan_project(project_dir):
    """Scan project deps. Returns {pkg_lower: {"type": "pypi"|"npm", "version": str|None}}."""
    repos = {}
    p_dir = Path(project_dir)

    req_files = []
    root_req = p_dir / "requirements.txt"
    if root_req.exists():
        req_files.append(root_req)

    req_dir = p_dir / "requirements"
    if req_dir.is_dir():
        req_files.extend(sorted(req_dir.glob("*.txt")))

    seen_in_file = {}

    for req_file in req_files:
        try:
            with open(req_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or line.startswith("-"):
                        continue
                    if line.startswith("git+") or line.startswith("http"):
                        continue
                    line_clean = re.sub(r"\[.*?\]", "", line)
                    parts = re.split(r"[<>=!~]", line_clean)
                    pkg = parts[0].strip().lower()
                    if not pkg:
                        continue
                    pinned = re.search(r"==([^\s,;]+)", line_clean)
                    version_val = pinned.group(1) if pinned else None
                    if pkg not in repos:
                        repos[pkg] = {"type": "pypi", "version": version_val}
                        seen_in_file[pkg] = req_file.name
                    else:
                        if seen_in_file.get(pkg) != req_file.name:
                            print(
                                f"[tzeepot warn] {pkg} found in multiple requirements files,"
                                f" using {seen_in_file[pkg]}",
                                file=sys.stderr,
                            )
        except OSError as e:
            print(f"[tzeepot warn] OSError: {e}", file=sys.stderr)
            continue

    pyproject = p_dir / "pyproject.toml"
    if pyproject.exists():
        try:
            import tomllib
        except ImportError:
            tomllib = None

        if tomllib:
            try:
                with open(pyproject, "rb") as f:
                    data = tomllib.load(f)
                deps = data.get("project", {}).get("dependencies", [])
                for d in deps:
                    d_clean = re.sub(r"\[.*?\]", "", d)
                    parts = re.split(r"[<>=!~ ]", d_clean)
                    pkg = parts[0].strip().lower()
                    if not pkg:
                        continue
                    if pkg not in repos:
                        pinned = re.search(r"==([^\s,;]+)", d_clean)
                        repos[pkg] = {"type": "pypi", "version": pinned.group(1) if pinned else None}
            except Exception as e:
                print(f"[tzeepot warn] Exception: {e}", file=sys.stderr)

    pkg_json = p_dir / "package.json"
    if pkg_json.exists():
        try:
            with open(pkg_json, "r") as f:
                data = json.load(f)
            for section in ("dependencies", "devDependencies"):
                for pkg, ver in data.get(section, {}).items():
                    pkg_lower = pkg.lower()
                    if pkg_lower not in repos:
                        pinned_ver = ver if (ver and re.match(r"^\d+\.\d+", ver)) else None
                        repos[pkg_lower] = {"type": "npm", "version": pinned_ver}
        except (json.JSONDecodeError, OSError) as e:
            print(f"[tzeepot warn] JSONDecodeError/OSError: {e}", file=sys.stderr)
            return repos

    return repos


def get_relative_time(iso_str):
    if not iso_str:
        return "Unknown"
    try:
        dt = datetime.datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        now = datetime.datetime.now(datetime.timezone.utc)
        diff = now - dt
        if diff.days < 0:
            return "Just now"
        if diff.days == 0:
            return "Today"
        if diff.days == 1:
            return "Yesterday"
        if diff.days < 30:
            return f"{diff.days} days ago"
        if diff.days < 365:
            return f"{diff.days // 30} months ago"
        return f"{diff.days // 365} years ago"
    except Exception:
        return iso_str


def get_health_indicator(iso_str):
    if not iso_str:
        return "⚪"
    try:
        dt = datetime.datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        now = datetime.datetime.now(datetime.timezone.utc)
        diff = now - dt
        if diff.days < 90:
            return "🟢"
        if diff.days < 365:
            return "🟡"
        return "🔴"
    except Exception:
        return "⚪"


def migrate_category(repo_conf):
    """Derive category from legacy fields."""
    if "category" in repo_conf:
        return repo_conf["category"]
    if repo_conf.get("pypi_name") or repo_conf.get("npm_name"):
        return "deps"
    if repo_conf.get("monitor"):
        return "monitoring"
    return "tools"


def ensure_categories(config):
    """Migration: add category to any repo missing it. Returns True if changed."""
    changed = False
    for r in config.get("repos", []):
        if "category" not in r:
            r["category"] = migrate_category(r)
            changed = True
    return changed


def main():
    parser = argparse.ArgumentParser(description="Depot: Track GitHub Releases")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--digest", action="store_true")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--output", type=str)
    parser.add_argument("--set-category", nargs=2, metavar=("REPO", "CATEGORY"))
    parser.add_argument("--scan", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--no-summary", action="store_true")
    parser.add_argument("--project-dir", type=str, default=".")
    parser.add_argument("--add-project", type=str)
    parser.add_argument("--add", type=str)
    parser.add_argument("--description", type=str)
    parser.add_argument("--set-desc", nargs=2, metavar=("REPO", "TEXT"))
    parser.add_argument("--label", type=str)
    parser.add_argument("--remove", type=str)
    parser.add_argument("--label-override", nargs=2, metavar=("REPO", "LABEL"))
    parser.add_argument("--monitor", type=str, help="[deprecated] Use --set-category instead")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--info", action="store_true")
    parser.add_argument("--dismiss", action="store_true")
    parser.add_argument("--snooze", action="store_true")
    parser.add_argument("--acknowledge", action="store_true")

    args = parser.parse_args()

    if args.add_project:
        config = load_json(CONFIG_FILE, {"repos": [], "projects": []})
        path = str(Path(args.add_project).resolve())
        if path not in config.get("projects", []):
            config.setdefault("projects", []).append(path)
            atomic_write_json(CONFIG_FILE, config)
            print(f"Added project path: {path}")
        else:
            print(f"Path already in projects list: {path}")
        return

    if args.add:
        slug = args.add
        if "/" not in slug:
            print(f"Error: Invalid repo format '{slug}'. Use owner/repo.", file=sys.stderr)
            sys.exit(1)
        owner, repo = slug.split("/", 1)
        config = load_json(CONFIG_FILE, {"repos": [], "projects": []})
        for r in config.get("repos", []):
            if r.get("owner") == owner and r.get("repo") == repo:
                print(f"Already watching {slug}")
                return
        desc = args.description or ""
        meta = get_repo_metadata(slug)
        if meta and meta.get("description") and not desc:
            desc = meta.get("description", "")
        new_repo = {
            "owner": owner,
            "repo": repo,
            "label": args.label or repo,
            "description": desc,
            "category": "tools",
        }
        config.setdefault("repos", []).append(new_repo)
        atomic_write_json(CONFIG_FILE, config)
        print(f"Added {slug}")
        return

    if args.set_desc:
        slug, desc = args.set_desc
        config = load_json(CONFIG_FILE, {"repos": [], "projects": []})
        for r in config.get("repos", []):
            if f"{r['owner']}/{r['repo']}" == slug:
                r["description"] = desc
                atomic_write_json(CONFIG_FILE, config)
                print(f"Updated description for {slug}")
                return
        print(f"Repo {slug} not found.", file=sys.stderr)
        sys.exit(1)

    if args.remove:
        slug = args.remove
        config = load_json(CONFIG_FILE, {"repos": [], "projects": []})
        new_repos = [r for r in config.get("repos", []) if f"{r['owner']}/{r['repo']}" != slug]
        if len(new_repos) == len(config.get("repos", [])):
            print(f"Repo {slug} not found in watch list.", file=sys.stderr)
            sys.exit(1)
        config["repos"] = new_repos
        atomic_write_json(CONFIG_FILE, config)
        print(f"Removed {slug}")
        return

    if args.label_override:
        slug, new_label = args.label_override
        config = load_json(CONFIG_FILE, {"repos": [], "projects": []})
        for r in config.get("repos", []):
            if f"{r['owner']}/{r['repo']}" == slug:
                r["label"] = new_label
                atomic_write_json(CONFIG_FILE, config)
                print(f"Updated label for {slug} to '{new_label}'")
                return
        print(f"Repo {slug} not found.", file=sys.stderr)
        sys.exit(1)

    if args.monitor:
        slug = args.monitor
        print(f"[tzeepot warn] --monitor is deprecated, use --set-category {slug} monitoring", file=sys.stderr)
        config = load_json(CONFIG_FILE, {"repos": [], "projects": []})
        for r in config.get("repos", []):
            if f"{r['owner']}/{r['repo']}" == slug:
                r["category"] = "monitoring"
                r["monitor"] = True
                atomic_write_json(CONFIG_FILE, config)
                print(f"Set {slug} category to: monitoring")
                return
        print(f"Repo {slug} not found.", file=sys.stderr)
        sys.exit(1)

    if args.set_category:
        slug, new_cat = args.set_category
        if new_cat not in VALID_CATEGORIES:
            print(
                f"Error: invalid category '{new_cat}'. Valid: {', '.join(sorted(VALID_CATEGORIES))}",
                file=sys.stderr,
            )
            sys.exit(1)
        config = load_json(CONFIG_FILE, {"repos": [], "projects": []})
        for r in config.get("repos", []):
            if f"{r['owner']}/{r['repo']}" == slug:
                r["category"] = new_cat
                atomic_write_json(CONFIG_FILE, config)
                print(f"Set category for {slug} to: {new_cat}")
                return
        print(f"Repo {slug} not found.", file=sys.stderr)
        sys.exit(1)

    if args.list or args.compact or args.info:
        config = load_json(CONFIG_FILE, {"repos": [], "projects": []})
        ensure_categories(config)
        state = load_json(STATE_FILE, {"last_seen_versions": {}, "metadata": {}})

        if not config["repos"]:
            print("No repos configured.")
            return

        cats = {
            "deps": ("📦 ЗАВИСИМОСТИ ПРОЕКТА", []),
            "tools": ("🛠  УСТАНОВЛЕННЫЕ ИНСТРУМЕНТЫ", []),
            "monitoring": ("🌐 МОНИТОРИНГ (НОВОСТИ И РАЗВИТИЕ)", []),
        }
        for r in config["repos"]:
            cat = r.get("category", "tools")
            if cat in cats:
                cats[cat][1].append(r)

        def print_repo_item(r):
            slug = f"{r['owner']}/{r['repo']}"
            label = r.get("label", slug)
            ver = state.get("last_seen_versions", {}).get(slug, "Unknown")
            meta = state.get("metadata", {}).get(slug, {})
            desc = r.get("description") or meta.get("description") or "No description"
            pushed_at = meta.get("pushed_at")
            health = get_health_indicator(pushed_at)

            if args.info:
                print(f"🔹 {label} — {desc}")
                return

            if args.compact:
                print(f"{health} {label}: v{ver} - https://github.com/{slug}")
                return

            relative_time = get_relative_time(pushed_at)
            pkg_info = ""
            if r.get("pypi_name"):
                pkg_info = f" [PyPI: {r['pypi_name']}]"
            if r.get("npm_name"):
                pkg_info = f" [npm: {r['npm_name']}]"
            print(f"{health} {label} ({slug}) - v{ver}{pkg_info}")
            print(f"   Description: {desc}")
            print(f"   Last Activity: {relative_time} ({pushed_at[:10] if pushed_at else 'N/A'})")
            print("------------------------------------------------------------")

        for cat_key in ("deps", "tools", "monitoring"):
            header, repos = cats[cat_key]
            if not repos:
                continue
            if not args.compact:
                print(f"\n{header}")
                print("============================================================")
            for r in repos:
                print_repo_item(r)

        if not args.compact and config.get("projects"):
            print("\n📂 Отслеживаемые папки проектов:")
            for p in config["projects"]:
                print(f"- {p}")
        return

    if args.scan:
        config = load_json(CONFIG_FILE, {"repos": [], "projects": []})

        project_dirs = [args.project_dir]
        if args.all and "projects" in config:
            project_dirs.extend(config["projects"])

        project_dirs = list(set(str(Path(p).resolve()) for p in project_dirs))

        all_found_pkgs = {}
        for p_dir in project_dirs:
            if not Path(p_dir).exists():
                continue
            print(f"Scanning {p_dir}...", file=sys.stderr)
            all_found_pkgs.update(scan_project(p_dir))

        if not all_found_pkgs:
            print("No dependencies found.", file=sys.stderr)
            return

        cache = load_json(CACHE_FILE, {})
        new_pkgs = {p: d for p, d in all_found_pkgs.items() if p not in cache}

        if new_pkgs:
            cached_count = len(all_found_pkgs) - len(new_pkgs)
            print(f"Scanning {len(new_pkgs)} new packages (cache: {cached_count})...", file=sys.stderr)

            def resolve_task(pkg_data):
                pkg, data = pkg_data
                if data["type"] == "pypi":
                    return pkg, resolve_pypi_to_github(pkg), "pypi"
                return pkg, resolve_npm_to_github(pkg), "npm"

            with ThreadPoolExecutor(max_workers=10) as executor:
                i = 0
                resolved_results = []
                for pkg, slug, ptype in executor.map(resolve_task, new_pkgs.items()):
                    i += 1
                    sys.stderr.write(f"\rScanning {i}/{len(new_pkgs)}...")
                    sys.stderr.flush()
                    resolved_results.append((pkg, slug, ptype))
                    cache[pkg] = slug
        else:
            resolved_results = []

        atomic_write_json(CACHE_FILE, cache)

        existing_slugs = {f"{r['owner']}/{r['repo']}" for r in config.get("repos", [])}
        added_count = 0
        for pkg, slug, ptype in resolved_results:
            if not slug or slug in existing_slugs:
                continue
            owner, repo_name = slug.split("/", 1)
            new_repo = {
                "owner": owner,
                "repo": repo_name,
                "label": repo_name,
                "category": "deps",
            }
            if ptype == "pypi":
                new_repo["pypi_name"] = pkg
            else:
                new_repo["npm_name"] = pkg
            config.setdefault("repos", []).append(new_repo)
            existing_slugs.add(slug)
            added_count += 1

        atomic_write_json(CONFIG_FILE, config)
        unresolved = len(all_found_pkgs) - len(resolved_results)
        print(f"Added {added_count} new repos. {unresolved} could not be auto-resolved.")
        return

    if args.stats:
        state = load_json(
            STATE_FILE,
            {"last_seen_versions": {}, "dismissed_versions": {}, "snooze_until": {}},
        )
        config = load_json(CONFIG_FILE, {"repos": [], "projects": []})

        total = len(config.get("repos", []))
        acknowledged = len(state.get("last_seen_versions", {}))
        dismissed_count = sum(len(v) for v in state.get("dismissed_versions", {}).values())

        today = datetime.date.today().isoformat()
        snoozed_count = sum(
            1
            for versions in state.get("snooze_until", {}).values()
            for until in versions.values()
            if until > today
        )

        oldest_until = None
        oldest_repo = None
        for repo, versions in state.get("snooze_until", {}).items():
            for ver, until in versions.items():
                if until > today:
                    if oldest_until is None or until < oldest_until:
                        oldest_until = until
                        oldest_repo = repo

        print("Depot Stats:")
        print(f"Total repos watched:     {total}")
        print(f"Upgrades acknowledged:   {acknowledged}  (repos with last_seen_versions set)")
        print(f"Upgrades dismissed:      {dismissed_count}  (dismissed_versions entries)")
        print(f"Upgrades snoozed:        {snoozed_count}  (active snooze_until entries)")
        if oldest_repo:
            print(f"Longest pending:  {oldest_repo} (snoozed until {oldest_until})")
        else:
            print("Longest pending:  none")
        return

    if args.report:
        config = load_json(CONFIG_FILE, {"repos": [], "projects": []})
        state = load_json(
            STATE_FILE,
            {"last_seen_versions": {}, "dismissed_versions": {}, "snooze_until": {}},
        )

        if args.output:
            target = Path(args.output)
        elif config.get("projects"):
            target = Path(config["projects"][0]) / "DEPOT-STATUS.md"
        else:
            print("[tzeepot warn] No project dir configured, writing to ~/.gstack/depot/", file=sys.stderr)
            target = Path.home() / ".gstack" / "depot" / "DEPOT-STATUS.md"

        os.makedirs(target.parent, exist_ok=True)
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lsv = state.get("last_seen_versions", {})

        up_to_date_count = 0
        not_checked = []
        for r in config.get("repos", []):
            slug = f"{r['owner']}/{r['repo']}"
            if lsv.get(slug):
                up_to_date_count += 1
            else:
                not_checked.append(r)

        lines = [
            "# Dependency Status",
            f"Last checked: {now_str}",
            "",
            "| Status | Count |",
            "|--------|-------|",
            f"| 🔴 Never checked | {len(not_checked)} |",
            f"| 🟢 Baseline set | {up_to_date_count} |",
        ]

        if not_checked:
            lines += ["", "## Never Checked", "| Package | Repo |", "|---------|------|"]
            for r in not_checked:
                slug = f"{r['owner']}/{r['repo']}"
                label = r.get("label", slug)
                lines.append(f"| {label} | {slug} |")

        content = "\n".join(lines) + "\n"
        atomic_write_text(target, content)
        print(f"Report written to: {target}")
        return

    if args.check or args.digest:
        config = load_json(CONFIG_FILE, {"repos": [], "projects": []})

        if not config["repos"]:
            print(json.dumps({"no_repos_configured": True}))
            return

        if ensure_categories(config):
            atomic_write_json(CONFIG_FILE, config)

        state = load_json(
            STATE_FILE,
            {"last_seen_versions": {}, "dismissed_versions": {}, "snooze_until": {}},
        )
        is_first_run = not state["last_seen_versions"]
        installed_map = scan_project(args.project_dir) if args.project_dir else {}

        results = {
            "baseline_established": is_first_run,
            "new_releases": [],
            "up_to_date": [],
            "warnings": [],
            "no_summary": args.no_summary,
        }

        def check_repo(repo_conf):
            slug = f"{repo_conf['owner']}/{repo_conf['repo']}"
            last_v = state["last_seen_versions"].get(slug)
            is_dependency = bool(
                repo_conf.get("pypi_name")
                or repo_conf.get("npm_name")
                or repo_conf.get("category") == "deps"
            )

            all_releases = []
            truncated = False
            found_last = False
            used_fallback = False

            for page in range(1, 4):
                resp = get_github_releases(slug, page=page, per_page=10)
                if isinstance(resp, dict) and "error" in resp:
                    if page == 1:
                        resp = get_github_tags(slug)
                        if isinstance(resp, dict) and "error" in resp:
                            resp = get_github_commits(slug)
                        if isinstance(resp, dict) and "error" in resp:
                            return {"slug": slug, "warning": resp["error"]}
                        used_fallback = True
                        all_releases.extend(resp if isinstance(resp, list) else [])
                    break
                else:
                    page_releases = resp if isinstance(resp, list) else []
                    if not page_releases:
                        break
                    all_releases.extend(page_releases)
                    if used_fallback:
                        break
                    if last_v:
                        for rel in page_releases:
                            v_str = rel.get("tag_name", "").lstrip("v")
                            if v_str == last_v.lstrip("v"):
                                found_last = True
                                break
                        if found_last:
                            break

            if last_v and not found_last and not truncated:
                if len(all_releases) >= 30:
                    truncated = True

            valid_releases = [r for r in all_releases if not r.get("draft") and not r.get("prerelease")]
            if not valid_releases:
                valid_releases = [r for r in all_releases if not r.get("draft")]

            if not valid_releases:
                meta = get_repo_metadata(slug)
                return {"slug": slug, "up_to_date": True, "meta": meta}

            meta = get_repo_metadata(slug)

            if is_first_run or not last_v:
                latest = valid_releases[0]
                v_str = latest.get("tag_name", "").lstrip("v")
                return {"slug": slug, "baseline": v_str, "meta": meta}

            last_v_clean = last_v.lstrip("v")
            new_rels = []
            for rel in valid_releases:
                v_str = rel.get("tag_name", "").lstrip("v")
                try:
                    if pkg_version.parse(v_str) > pkg_version.parse(last_v_clean):
                        new_rels.append(rel)
                    else:
                        break
                except Exception:
                    if v_str != last_v_clean:
                        new_rels.append(rel)

            if not new_rels:
                return {"slug": slug, "up_to_date": True, "meta": meta}

            latest_v = new_rels[0].get("tag_name", "").lstrip("v")

            snoozed_until = state.get("snooze_until", {}).get(slug, {}).get(latest_v)
            if snoozed_until:
                today_iso = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
                if today_iso < snoozed_until:
                    return {"slug": slug, "is_snoozed": True, "version": latest_v, "meta": meta}

            if state.get("dismissed_versions", {}).get(slug, {}).get(latest_v):
                return {"slug": slug, "up_to_date": True, "meta": meta}

            advisories = get_github_advisories(slug)
            is_sec_global = False
            severity_global = None
            for adv in advisories:
                is_sec_global = True
                severity_global = adv.get("severity")

            release_list = []
            for rel in new_rels:
                v_str = rel.get("tag_name", "").lstrip("v")
                body = rel.get("body", "") or ""
                is_sec = is_sec_global or "CVE-" in body or "GHSA-" in body
                release_list.append({
                    "version": v_str,
                    "delta": f"{last_v_clean} → {v_str}",
                    "upgrade_risk": classify_upgrade(last_v_clean, v_str),
                    "published_at": rel.get("published_at"),
                    "release_notes_body": body,
                    "is_security": is_sec,
                    "severity": severity_global,
                })

            installed_version = None
            pkg_name = repo_conf.get("pypi_name") or repo_conf.get("npm_name")
            if pkg_name:
                pkg_data = installed_map.get(pkg_name.lower())
                if pkg_data:
                    installed_version = pkg_data.get("version")

            return {
                "slug": slug,
                "new": True,
                "last_seen_version": last_v_clean,
                "latest_version": latest_v,
                "installed_version": installed_version,
                "is_dependency": is_dependency,
                "truncated": truncated,
                "new_releases": release_list,
                "label": repo_conf.get("label", slug),
                "is_security": any(r["is_security"] for r in release_list),
                "meta": meta,
            }

        state_changed = False
        print(f"Checking {len(config['repos'])} repos...", file=sys.stderr)
        with ThreadPoolExecutor(max_workers=10) as executor:
            i = 0
            for res in executor.map(check_repo, config["repos"]):
                i += 1
                sys.stderr.write(f"\rChecking {i}/{len(config['repos'])}...")
                sys.stderr.flush()

                slug = res["slug"]
                if res.get("meta"):
                    state.setdefault("metadata", {})[slug] = res["meta"]
                    state_changed = True

                if "warning" in res:
                    results["warnings"].append({"repo": slug, "reason": res["warning"]})
                    continue

                if res.get("baseline"):
                    state["last_seen_versions"][slug] = res["baseline"]
                    state_changed = True
                    results["up_to_date"].append(slug)
                    continue

                if res.get("new"):
                    results["new_releases"].append({
                        "repo": slug,
                        "label": res["label"],
                        "last_seen_version": res["last_seen_version"],
                        "latest_version": res["latest_version"],
                        "installed_version": res.get("installed_version"),
                        "is_dependency": res["is_dependency"],
                        "truncated": res.get("truncated", False),
                        "new_releases": res["new_releases"],
                        "is_security": res.get("is_security", False),
                        "is_snoozed": False,
                    })
                    continue

                results["up_to_date"].append(slug)

        sys.stderr.write("\n")

        if args.digest:
            cutoff = (
                datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=args.days)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            filtered = []
            for entry in results["new_releases"]:
                recent = [
                    v for v in entry["new_releases"]
                    if (v.get("published_at") or "") >= cutoff
                ]
                if not recent:
                    continue
                entry_copy = dict(entry)
                entry_copy["new_releases"] = recent
                filtered.append(entry_copy)

            filtered.sort(
                key=lambda e: (e["new_releases"][0].get("published_at") or "") if e["new_releases"] else "",
                reverse=True,
            )
            filtered.sort(
                key=lambda e: (
                    0 if e.get("is_security") else 1,
                    RISK_SORT.get(
                        e["new_releases"][0].get("upgrade_risk", "REVIEW") if e["new_releases"] else "REVIEW",
                        1,
                    ),
                )
            )
            results["new_releases"] = filtered

        if state_changed:
            atomic_write_json(STATE_FILE, state)

        print(json.dumps(results))
        return

    if args.dismiss or args.snooze or args.acknowledge:
        try:
            data = json.loads(sys.stdin.read())
            repo = data.get("repo")
            v = data.get("version")
            if not repo or not v:
                print("Error: Missing 'repo' or 'version' in stdin JSON", file=sys.stderr)
                sys.exit(1)
            state = load_json(
                STATE_FILE,
                {"last_seen_versions": {}, "dismissed_versions": {}, "snooze_until": {}},
            )
            state["last_seen_versions"][repo] = v
            if args.dismiss:
                state.setdefault("dismissed_versions", {}).setdefault(repo, {})[v] = True
            elif args.snooze:
                days = data.get("days", 30)
                until = (
                    datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days)
                ).date().isoformat()
                state.setdefault("snooze_until", {}).setdefault(repo, {})[v] = until
            atomic_write_json(STATE_FILE, state)
            print(f"Action recorded for {repo}@{v}")
            return
        except json.JSONDecodeError as e:
            print(f"Error processing action: {e}", file=sys.stderr)
            sys.exit(1)
            return

    parser.print_help()


if __name__ == "__main__":
    main()
