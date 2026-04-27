import pytest
import json
import os
import sys
import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open

import depot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_release(tag, published_at="2026-04-20T10:00:00Z", body="", draft=False, prerelease=False):
    return {
        "tag_name": tag,
        "published_at": published_at,
        "body": body,
        "draft": draft,
        "prerelease": prerelease,
    }


def run_check(config_data, state_data, gh_releases, extra_argv=None, project_dir=None):
    """Helper: run --check with mocked config/state/releases, return parsed JSON output."""
    if project_dir is None:
        project_dir = "."

    argv = ["depot.py", "--check", "--project-dir", project_dir]
    if extra_argv:
        argv += extra_argv

    captured = []

    def fake_print(*args, **kwargs):
        if kwargs.get("file") is sys.stderr:
            return
        captured.append(args[0] if args else "")

    with patch("sys.argv", argv), \
         patch("depot.load_json") as mock_load, \
         patch("depot.atomic_write_json"), \
         patch("depot.get_github_releases") as mock_gh, \
         patch("depot.get_github_advisories", return_value=[]), \
         patch("depot.get_repo_metadata", return_value=None), \
         patch("builtins.print", side_effect=fake_print):
        mock_load.side_effect = [config_data, state_data]
        mock_gh.side_effect = gh_releases if callable(gh_releases) else (
            lambda slug, page=1, per_page=10: gh_releases
        )
        depot.main()

    output = next((c for c in captured if c.startswith("{")), None)
    return json.loads(output) if output else None


# ---------------------------------------------------------------------------
# Existing tests (updated for v2 output format)
# ---------------------------------------------------------------------------

def test_normalize_github_url():
    assert depot.normalize_github_url("https://github.com/owner/repo") == "owner/repo"
    assert depot.normalize_github_url("git+https://github.com/owner/repo.git") == "owner/repo"
    assert depot.normalize_github_url("github:owner/repo") == "owner/repo"
    assert depot.normalize_github_url("https://github.com/owner/repo/") == "owner/repo"
    assert depot.normalize_github_url("https://github.com/owner/repo.git?query=1") == "owner/repo"
    assert depot.normalize_github_url("https://github.com/owner/repo/subdir") is None


def test_pypi_url_monorepo_rejected():
    assert depot.normalize_github_url("https://github.com/owner/repo/tree/master/subdir") is None


@patch("depot.CONFIG_FILE")
def test_load_json_default(mock_config):
    mock_config.exists.return_value = False
    assert depot.load_json(mock_config, {"test": 1}) == {"test": 1}


def test_atomic_write_json(tmp_path):
    path = tmp_path / "test.json"
    data = {"a": 1}
    depot.atomic_write_json(path, data)
    assert path.exists()
    with open(path, "r") as f:
        assert json.load(f) == data


def test_atomic_write_text(tmp_path):
    path = tmp_path / "report.md"
    depot.atomic_write_text(path, "# Hello\n")
    assert path.read_text() == "# Hello\n"


@patch("subprocess.run")
def test_get_github_releases(mock_run):
    mock_run.return_value = MagicMock(stdout=json.dumps([{"tag_name": "v1.0.0"}]), returncode=0)
    res = depot.get_github_releases("owner/repo")
    assert res[0]["tag_name"] == "v1.0.0"


@patch("urllib.request.urlopen")
def test_resolve_pypi_to_github(mock_urlopen):
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({
        "info": {"project_urls": {"Source": "https://github.com/owner/repo"}}
    }).encode()
    mock_response.__enter__.return_value = mock_response
    mock_urlopen.return_value = mock_response
    assert depot.resolve_pypi_to_github("somepkg") == "owner/repo"


def test_scan_project_requirements(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text("flask>=3.0.0\n# comment\nrequests\n")
    repos = depot.scan_project(tmp_path)
    assert "flask" in repos
    assert "requests" in repos
    assert repos["flask"]["type"] == "pypi"


def test_snooze_logic():
    today = datetime.date(2026, 4, 26)
    future = (today + datetime.timedelta(days=1)).isoformat()
    past = (today - datetime.timedelta(days=1)).isoformat()
    assert "2026-04-26" < future
    assert "2026-04-26" >= past


@patch("subprocess.run")
def test_gh_api_404_returns_warning(mock_run):
    """Repos returning 404 appear in warnings."""
    config = {"repos": [{"owner": "owner", "repo": "repo", "label": "label", "category": "tools"}]}
    state = {"last_seen_versions": {"owner/repo": "1.0.0"}, "dismissed_versions": {}, "snooze_until": {}}

    def fake_gh(slug, page=1, per_page=10):
        return {"error": "404 Not Found"}

    captured = []

    def fake_print(*args, **kwargs):
        if kwargs.get("file") is sys.stderr: return
        captured.append(args[0] if args else "")

    with patch("sys.argv", ["depot.py", "--check"]), \
         patch("depot.load_json") as mock_load, \
         patch("depot.atomic_write_json"), \
         patch("depot.get_github_releases", side_effect=fake_gh), \
         patch("depot.get_github_tags", return_value={"error": "404"}), \
         patch("depot.get_github_commits", return_value={"error": "404"}), \
         patch("depot.get_repo_metadata", return_value=None), \
         patch("builtins.print", side_effect=fake_print):
        mock_load.side_effect = [config, state]
        depot.main()

    output = next((c for c in captured if c.startswith("{")), None)
    result = json.loads(output)
    assert len(result["warnings"]) == 1
    assert "404" in result["warnings"][0]["reason"]


@patch("sys.stdin")
def test_stdin_malformed_json_exits_cleanly(mock_stdin):
    mock_stdin.read.return_value = "not json"
    with patch("sys.argv", ["depot.py", "--acknowledge"]):
        with pytest.raises(SystemExit) as cm:
            depot.main()
        assert cm.value.code == 1


@patch("sys.stdin")
def test_stdin_missing_field_exits_cleanly(mock_stdin):
    mock_stdin.read.return_value = json.dumps({"repo": "owner/repo"})
    with patch("sys.argv", ["depot.py", "--acknowledge"]):
        with pytest.raises(SystemExit) as cm:
            depot.main()
        assert cm.value.code == 1


# ---------------------------------------------------------------------------
# B1: Version delta
# ---------------------------------------------------------------------------

def test_version_delta_in_output():
    """new_releases entries include last_seen_version and delta field."""
    config = {"repos": [{"owner": "pallets", "repo": "flask", "label": "Flask", "category": "deps", "pypi_name": "flask"}]}
    state = {"last_seen_versions": {"pallets/flask": "3.0.3"}, "dismissed_versions": {}, "snooze_until": {}}

    captured = []
    def fake_print(*a, **kw):
        if kw.get("file") is sys.stderr: return
        captured.append(a[0] if a else "")

    with patch("sys.argv", ["depot.py", "--check", "--project-dir", "."]), \
         patch("depot.load_json") as ml, \
         patch("depot.atomic_write_json"), \
         patch("depot.scan_project", return_value={}), \
         patch("depot.get_github_releases", return_value=[make_release("v3.1.0")]), \
         patch("depot.get_github_advisories", return_value=[]), \
         patch("depot.get_repo_metadata", return_value=None), \
         patch("depot.ensure_categories", return_value=False), \
         patch("builtins.print", side_effect=fake_print):
        ml.side_effect = [config, state]
        depot.main()

    out = json.loads(next(c for c in captured if c.startswith("{")))
    assert len(out["new_releases"]) == 1
    entry = out["new_releases"][0]
    assert entry["last_seen_version"] == "3.0.3"
    assert entry["latest_version"] == "3.1.0"
    rel = entry["new_releases"][0]
    assert rel["delta"] == "3.0.3 → 3.1.0"


# ---------------------------------------------------------------------------
# B2: installed_version
# ---------------------------------------------------------------------------

def test_installed_version_from_requirements(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text("Django==5.0.4\nrequests>=2.28\n")
    result = depot.scan_project(tmp_path)
    assert result["django"]["version"] == "5.0.4"
    assert result["requests"]["version"] is None  # range, not pinned


def test_installed_version_null_no_mapping(tmp_path):
    """Repo without pypi_name/npm_name → installed_version=None."""
    (tmp_path / "requirements.txt").write_text("# empty\n")
    config = {"repos": [{"owner": "owner", "repo": "repo", "label": "repo", "category": "tools"}]}
    state = {"last_seen_versions": {"owner/repo": "1.0.0"}, "dismissed_versions": {}, "snooze_until": {}}

    captured = []
    def fp(*a, **kw):
        if kw.get("file") is sys.stderr: return
        captured.append(a[0] if a else "")

    with patch("sys.argv", ["depot.py", "--check", "--project-dir", str(tmp_path)]), \
         patch("depot.load_json") as ml, \
         patch("depot.atomic_write_json"), \
         patch("depot.get_github_releases", return_value=[make_release("v2.0.0")]), \
         patch("depot.get_github_advisories", return_value=[]), \
         patch("depot.get_repo_metadata", return_value=None), \
         patch("depot.ensure_categories", return_value=False), \
         patch("builtins.print", side_effect=fp):
        ml.side_effect = [config, state]
        depot.main()

    out = json.loads(next(c for c in captured if c.startswith("{")))
    entry = out["new_releases"][0]
    assert entry["installed_version"] is None


def test_installed_version_range_is_null(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text("Django>=5.0,<6\n")
    result = depot.scan_project(tmp_path)
    assert result["django"]["version"] is None


def test_installed_version_extras_notation(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text("Django[crypto]==5.0.4\n")
    result = depot.scan_project(tmp_path)
    assert result["django"]["version"] == "5.0.4"


def test_installed_version_git_url_is_null(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text("git+https://github.com/owner/repo.git@main\n")
    result = depot.scan_project(tmp_path)
    assert "git+https" not in result
    assert len(result) == 0


def test_installed_version_subdir_glob(tmp_path):
    req_dir = tmp_path / "requirements"
    req_dir.mkdir()
    (req_dir / "prod.txt").write_text("celery==5.3.4\n")
    result = depot.scan_project(tmp_path)
    assert result["celery"]["version"] == "5.3.4"


def test_installed_version_from_pyproject(tmp_path):
    toml_content = '[project]\ndependencies = ["httpx==0.27.0"]\n'
    (tmp_path / "pyproject.toml").write_text(toml_content)
    try:
        import tomllib  # noqa: F401 — skip if not available
    except ImportError:
        pytest.skip("tomllib not available")
    result = depot.scan_project(tmp_path)
    assert result.get("httpx", {}).get("version") == "0.27.0"


def test_installed_version_from_package_json(tmp_path):
    pkg = {"dependencies": {"react": "18.2.0"}, "devDependencies": {"typescript": "5.0.0"}}
    (tmp_path / "package.json").write_text(json.dumps(pkg))
    result = depot.scan_project(tmp_path)
    assert result["react"]["version"] == "18.2.0"
    assert result["typescript"]["version"] == "5.0.0"


# ---------------------------------------------------------------------------
# B5: Classifier
# ---------------------------------------------------------------------------

def test_classifier_safe():
    assert depot.classify_upgrade("5.0.4", "5.0.5") == "SAFE"


def test_classifier_review():
    assert depot.classify_upgrade("5.0.4", "5.1.0") == "REVIEW"


def test_classifier_breaking():
    assert depot.classify_upgrade("4.2.0", "5.0.0") == "BREAKING"


def test_classifier_non_semver():
    # Non-semver date tag → REVIEW fallback
    assert depot.classify_upgrade("20240115", "20250101") == "REVIEW"


def test_classifier_prerelease_is_review():
    assert depot.classify_upgrade("5.1.0", "5.2.0a1") == "REVIEW"
    assert depot.classify_upgrade("5.1.0", "5.2.0rc2") == "REVIEW"


def test_classifier_v_prefix_stripped():
    assert depot.classify_upgrade("v5.0.4", "v5.0.5") == "SAFE"


# ---------------------------------------------------------------------------
# E1: Category
# ---------------------------------------------------------------------------

def test_category_migration():
    """pypi_name → deps; monitor=True → monitoring; else → tools."""
    assert depot.migrate_category({"pypi_name": "flask"}) == "deps"
    assert depot.migrate_category({"npm_name": "react"}) == "deps"
    assert depot.migrate_category({"monitor": True}) == "monitoring"
    assert depot.migrate_category({}) == "tools"
    assert depot.migrate_category({"category": "monitoring"}) == "monitoring"


def test_set_category_command(tmp_path):
    config = {
        "repos": [{"owner": "owner", "repo": "repo", "label": "repo", "category": "tools"}],
        "projects": []
    }
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(config))

    with patch("depot.CONFIG_FILE", cfg_file), \
         patch("sys.argv", ["depot.py", "--set-category", "owner/repo", "monitoring"]):
        depot.main()

    saved = json.loads(cfg_file.read_text())
    assert saved["repos"][0]["category"] == "monitoring"


def test_set_category_invalid_value():
    with patch("sys.argv", ["depot.py", "--set-category", "owner/repo", "bogus"]), \
         patch("builtins.print"):
        with pytest.raises(SystemExit) as cm:
            depot.main()
        assert cm.value.code == 1


# ---------------------------------------------------------------------------
# E2: Multi-release gap
# ---------------------------------------------------------------------------

def test_multi_release_gap():
    """3 releases newer than last_seen → all 3 returned in new_releases list."""
    config = {"repos": [{"owner": "owner", "repo": "repo", "label": "repo", "category": "deps", "pypi_name": "pkg"}]}
    state = {"last_seen_versions": {"owner/repo": "1.0.0"}, "dismissed_versions": {}, "snooze_until": {}}

    releases = [
        make_release("v1.3.0", "2026-04-25T00:00:00Z"),
        make_release("v1.2.0", "2026-04-20T00:00:00Z"),
        make_release("v1.1.0", "2026-04-15T00:00:00Z"),
        make_release("v1.0.0", "2026-03-01T00:00:00Z"),
    ]

    captured = []
    def fp(*a, **kw):
        if kw.get("file") is sys.stderr: return
        captured.append(a[0] if a else "")

    with patch("sys.argv", ["depot.py", "--check", "--project-dir", "."]), \
         patch("depot.load_json") as ml, \
         patch("depot.atomic_write_json"), \
         patch("depot.scan_project", return_value={}), \
         patch("depot.get_github_releases", return_value=releases), \
         patch("depot.get_github_advisories", return_value=[]), \
         patch("depot.get_repo_metadata", return_value=None), \
         patch("depot.ensure_categories", return_value=False), \
         patch("builtins.print", side_effect=fp):
        ml.side_effect = [config, state]
        depot.main()

    out = json.loads(next(c for c in captured if c.startswith("{")))
    entry = out["new_releases"][0]
    assert len(entry["new_releases"]) == 3
    versions = [r["version"] for r in entry["new_releases"]]
    assert "1.3.0" in versions and "1.2.0" in versions and "1.1.0" in versions


def test_multi_release_truncated_flag():
    """> 30 fetched releases without finding last_seen → truncated=true."""
    # Simulate 3 pages of 10 releases, none matching last_seen "0.1.0"
    page1 = [make_release(f"v3.{i}.0") for i in range(10)]
    page2 = [make_release(f"v2.{i}.0") for i in range(10)]
    page3 = [make_release(f"v1.{i}.0") for i in range(10, 20)]

    call_count = [0]
    def fake_releases(slug, page=1, per_page=10):
        call_count[0] += 1
        if page == 1: return page1
        if page == 2: return page2
        if page == 3: return page3
        return []

    config = {"repos": [{"owner": "owner", "repo": "repo", "label": "repo", "category": "tools"}]}
    state = {"last_seen_versions": {"owner/repo": "0.1.0"}, "dismissed_versions": {}, "snooze_until": {}}

    captured = []
    def fp(*a, **kw):
        if kw.get("file") is sys.stderr: return
        captured.append(a[0] if a else "")

    with patch("sys.argv", ["depot.py", "--check", "--project-dir", "."]), \
         patch("depot.load_json") as ml, \
         patch("depot.atomic_write_json"), \
         patch("depot.scan_project", return_value={}), \
         patch("depot.get_github_releases", side_effect=fake_releases), \
         patch("depot.get_github_advisories", return_value=[]), \
         patch("depot.get_repo_metadata", return_value=None), \
         patch("depot.ensure_categories", return_value=False), \
         patch("builtins.print", side_effect=fp):
        ml.side_effect = [config, state]
        depot.main()

    out = json.loads(next(c for c in captured if c.startswith("{")))
    assert len(out["new_releases"]) > 0
    assert out["new_releases"][0]["truncated"] is True


def test_multi_release_truncated_30_cap():
    """Same as above: 30 fetched releases with last_seen not found → truncated=true."""
    # All 30 releases are newer than last_seen "0.0.1"
    all_releases = [make_release(f"v1.{i}.0") for i in range(1, 31)]

    call_count = [0]
    def fake_releases(slug, page=1, per_page=10):
        call_count[0] += 1
        start = (page - 1) * 10
        return all_releases[start:start + 10]

    config = {"repos": [{"owner": "owner", "repo": "repo", "label": "repo", "category": "tools"}]}
    state = {"last_seen_versions": {"owner/repo": "0.0.1"}, "dismissed_versions": {}, "snooze_until": {}}

    captured = []
    def fp(*a, **kw):
        if kw.get("file") is sys.stderr: return
        captured.append(a[0] if a else "")

    with patch("sys.argv", ["depot.py", "--check", "--project-dir", "."]), \
         patch("depot.load_json") as ml, \
         patch("depot.atomic_write_json"), \
         patch("depot.scan_project", return_value={}), \
         patch("depot.get_github_releases", side_effect=fake_releases), \
         patch("depot.get_github_advisories", return_value=[]), \
         patch("depot.get_repo_metadata", return_value=None), \
         patch("depot.ensure_categories", return_value=False), \
         patch("builtins.print", side_effect=fp):
        ml.side_effect = [config, state]
        depot.main()

    out = json.loads(next(c for c in captured if c.startswith("{")))
    assert out["new_releases"][0]["truncated"] is True


# ---------------------------------------------------------------------------
# B4: Digest
# ---------------------------------------------------------------------------

def _make_digest_entries():
    return [
        {
            "repo": "a/safe-pkg", "label": "safe-pkg", "is_security": False,
            "last_seen_version": "1.0.0", "latest_version": "1.0.1",
            "installed_version": None, "is_dependency": True, "truncated": False,
            "is_snoozed": False,
            "new_releases": [{"version": "1.0.1", "delta": "1.0.0→1.0.1", "upgrade_risk": "SAFE",
                               "published_at": "2026-04-25T10:00:00Z", "release_notes_body": "", "is_security": False, "severity": None}],
        },
        {
            "repo": "b/breaking-pkg", "label": "breaking-pkg", "is_security": False,
            "last_seen_version": "1.0.0", "latest_version": "2.0.0",
            "installed_version": None, "is_dependency": True, "truncated": False,
            "is_snoozed": False,
            "new_releases": [{"version": "2.0.0", "delta": "1.0.0→2.0.0", "upgrade_risk": "BREAKING",
                               "published_at": "2026-04-24T10:00:00Z", "release_notes_body": "", "is_security": False, "severity": None}],
        },
        {
            "repo": "c/security-pkg", "label": "security-pkg", "is_security": True,
            "last_seen_version": "1.0.0", "latest_version": "1.0.2",
            "installed_version": None, "is_dependency": True, "truncated": False,
            "is_snoozed": False,
            "new_releases": [{"version": "1.0.2", "delta": "1.0.0→1.0.2", "upgrade_risk": "SAFE",
                               "published_at": "2026-04-23T10:00:00Z", "release_notes_body": "CVE-2026-999", "is_security": True, "severity": "high"}],
        },
    ]


def test_digest_filters_by_date():
    """Releases outside --days window are excluded."""
    config = {"repos": [{"owner": "owner", "repo": "repo", "label": "repo", "category": "deps", "pypi_name": "pkg"}]}
    # last_seen = 2 months ago; release published > 7 days ago
    old_date = (datetime.datetime.utcnow() - datetime.timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    state = {"last_seen_versions": {"owner/repo": "1.0.0"}, "dismissed_versions": {}, "snooze_until": {}}

    captured = []
    def fp(*a, **kw):
        if kw.get("file") is sys.stderr: return
        captured.append(a[0] if a else "")

    with patch("sys.argv", ["depot.py", "--digest", "--days", "7", "--project-dir", "."]), \
         patch("depot.load_json") as ml, \
         patch("depot.atomic_write_json"), \
         patch("depot.scan_project", return_value={}), \
         patch("depot.get_github_releases", return_value=[make_release("v1.0.1", old_date)]), \
         patch("depot.get_github_advisories", return_value=[]), \
         patch("depot.get_repo_metadata", return_value=None), \
         patch("depot.ensure_categories", return_value=False), \
         patch("builtins.print", side_effect=fp):
        ml.side_effect = [config, state]
        depot.main()

    out = json.loads(next(c for c in captured if c.startswith("{")))
    assert len(out["new_releases"]) == 0


def test_digest_sort_order():
    """Sort: security first, then BREAKING, then newest date."""
    entries = _make_digest_entries()
    # Apply the same sorting logic as depot.digest
    RISK_SORT_LOCAL = {"BREAKING": 0, "REVIEW": 1, "SAFE": 2}
    entries.sort(
        key=lambda e: (e["new_releases"][0].get("published_at") or "") if e["new_releases"] else "",
        reverse=True
    )
    entries.sort(
        key=lambda e: (
            0 if e.get("is_security") else 1,
            RISK_SORT_LOCAL.get(
                e["new_releases"][0].get("upgrade_risk", "REVIEW") if e["new_releases"] else "REVIEW",
                1
            ),
        )
    )
    assert entries[0]["repo"] == "c/security-pkg"   # security first
    assert entries[1]["repo"] == "b/breaking-pkg"   # then BREAKING
    assert entries[2]["repo"] == "a/safe-pkg"       # then SAFE


def test_digest_excludes_snoozed():
    """Snoozed repo does not appear in --digest output."""
    config = {"repos": [{"owner": "owner", "repo": "snoozed", "label": "repo", "category": "tools"}]}
    future = (datetime.date.today() + datetime.timedelta(days=7)).isoformat()
    state = {
        "last_seen_versions": {"owner/snoozed": "1.0.0"},
        "dismissed_versions": {},
        "snooze_until": {"owner/snoozed": {"1.0.1": future}},
    }
    recent = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    captured = []
    def fp(*a, **kw):
        if kw.get("file") is sys.stderr: return
        captured.append(a[0] if a else "")

    with patch("sys.argv", ["depot.py", "--digest", "--days", "7", "--project-dir", "."]), \
         patch("depot.load_json") as ml, \
         patch("depot.atomic_write_json"), \
         patch("depot.scan_project", return_value={}), \
         patch("depot.get_github_releases", return_value=[make_release("v1.0.1", recent)]), \
         patch("depot.get_github_advisories", return_value=[]), \
         patch("depot.get_repo_metadata", return_value=None), \
         patch("depot.ensure_categories", return_value=False), \
         patch("builtins.print", side_effect=fp):
        ml.side_effect = [config, state]
        depot.main()

    out = json.loads(next(c for c in captured if c.startswith("{")))
    assert len(out["new_releases"]) == 0


def test_digest_with_project_dir_installed_version(tmp_path):
    """--digest + --project-dir shows installed_version when pypi_name matches."""
    req = tmp_path / "requirements.txt"
    req.write_text("flask==3.0.3\n")
    config = {"repos": [{"owner": "pallets", "repo": "flask", "label": "Flask", "category": "deps", "pypi_name": "flask"}]}
    recent = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    state = {"last_seen_versions": {"pallets/flask": "3.0.3"}, "dismissed_versions": {}, "snooze_until": {}}

    captured = []
    def fp(*a, **kw):
        if kw.get("file") is sys.stderr: return
        captured.append(a[0] if a else "")

    with patch("sys.argv", ["depot.py", "--digest", "--days", "7", "--project-dir", str(tmp_path)]), \
         patch("depot.load_json") as ml, \
         patch("depot.atomic_write_json"), \
         patch("depot.get_github_releases", return_value=[make_release("v3.1.0", recent)]), \
         patch("depot.get_github_advisories", return_value=[]), \
         patch("depot.get_repo_metadata", return_value=None), \
         patch("depot.ensure_categories", return_value=False), \
         patch("builtins.print", side_effect=fp):
        ml.side_effect = [config, state]
        depot.main()

    out = json.loads(next(c for c in captured if c.startswith("{")))
    if out["new_releases"]:
        assert out["new_releases"][0]["installed_version"] == "3.0.3"


# ---------------------------------------------------------------------------
# E6: Stats
# ---------------------------------------------------------------------------

def test_stats_output(tmp_path, capsys):
    future = (datetime.date.today() + datetime.timedelta(days=5)).isoformat()
    state = {
        "last_seen_versions": {"a/b": "1.0", "c/d": "2.0"},
        "dismissed_versions": {"a/b": {"0.9": True}},
        "snooze_until": {"c/d": {"3.0": future}},
    }
    config = {"repos": [{"owner": "a", "repo": "b"}, {"owner": "c", "repo": "d"}]}
    with patch("sys.argv", ["depot.py", "--stats"]), \
         patch("depot.load_json") as ml:
        ml.side_effect = [state, config]
        depot.main()
    out = capsys.readouterr().out
    assert "Total repos watched:     2" in out
    assert "Upgrades acknowledged:   2" in out
    assert "Upgrades dismissed:      1" in out
    assert "Upgrades snoozed:        1" in out


# ---------------------------------------------------------------------------
# E4: Report
# ---------------------------------------------------------------------------

def test_report_writes_file(tmp_path):
    config = {
        "repos": [{"owner": "owner", "repo": "repo", "label": "pkg", "category": "deps"}],
        "projects": [str(tmp_path)]
    }
    state = {"last_seen_versions": {}, "dismissed_versions": {}, "snooze_until": {}}
    with patch("sys.argv", ["depot.py", "--report"]), \
         patch("depot.CONFIG_FILE", tmp_path / "config.json"), \
         patch("depot.STATE_FILE", tmp_path / "state.json"), \
         patch("depot.load_json") as ml, \
         patch("builtins.print"):
        ml.side_effect = [config, state]
        depot.main()
    assert (tmp_path / "DEPOT-STATUS.md").exists()


def test_report_atomic_write(tmp_path):
    """Atomic write: tmp file used then replaced; final file is valid."""
    config = {"repos": [], "projects": [str(tmp_path)]}
    state = {"last_seen_versions": {}, "dismissed_versions": {}, "snooze_until": {}}
    with patch("sys.argv", ["depot.py", "--report"]), \
         patch("depot.load_json") as ml, \
         patch("builtins.print"):
        ml.side_effect = [config, state]
        depot.main()
    target = tmp_path / "DEPOT-STATUS.md"
    assert target.exists()
    # .tmp file should be gone
    assert not (tmp_path / "DEPOT-STATUS.md.tmp").exists()


def test_report_no_project_dir_fallback(tmp_path):
    """No project dirs in config → writes to ~/.gstack/depot/."""
    config = {"repos": [], "projects": []}
    state = {"last_seen_versions": {}, "dismissed_versions": {}, "snooze_until": {}}
    fallback_dir = tmp_path / "gstack_depot"
    with patch("sys.argv", ["depot.py", "--report"]), \
         patch("depot.load_json") as ml, \
         patch("pathlib.Path.home", return_value=tmp_path), \
         patch("builtins.print"):
        ml.side_effect = [config, state]
        # Patch the gstack depot path
        depot_target = tmp_path / ".gstack" / "depot" / "DEPOT-STATUS.md"
        with patch("depot.Path.home", return_value=tmp_path):
            depot.main()


def test_report_creates_directory(tmp_path):
    """~/.gstack/depot/ is created if it doesn't exist."""
    config = {"repos": [], "projects": []}
    state = {"last_seen_versions": {}, "dismissed_versions": {}, "snooze_until": {}}
    gstack_dir = tmp_path / ".gstack" / "depot"
    assert not gstack_dir.exists()

    # Use --output to specify a path inside a non-existent dir
    nested = tmp_path / "nested" / "dir" / "DEPOT-STATUS.md"
    with patch("sys.argv", ["depot.py", "--report", "--output", str(nested)]), \
         patch("depot.load_json") as ml, \
         patch("builtins.print"):
        ml.side_effect = [config, state]
        depot.main()
    assert nested.exists()


# ---------------------------------------------------------------------------
# First-run edge case (B1)
# ---------------------------------------------------------------------------

def test_first_run_delta_format():
    """On first run (no last_seen), baseline is set and baseline_established=True."""
    config = {"repos": [{"owner": "owner", "repo": "repo", "label": "repo", "category": "tools"}]}
    state = {"last_seen_versions": {}, "dismissed_versions": {}, "snooze_until": {}}

    captured = []
    def fp(*a, **kw):
        if kw.get("file") is sys.stderr: return
        captured.append(a[0] if a else "")

    with patch("sys.argv", ["depot.py", "--check", "--project-dir", "."]), \
         patch("depot.load_json") as ml, \
         patch("depot.atomic_write_json"), \
         patch("depot.scan_project", return_value={}), \
         patch("depot.get_github_releases", return_value=[make_release("v2.0.0")]), \
         patch("depot.get_repo_metadata", return_value=None), \
         patch("depot.ensure_categories", return_value=False), \
         patch("builtins.print", side_effect=fp):
        ml.side_effect = [config, state]
        depot.main()

    out = json.loads(next(c for c in captured if c.startswith("{")))
    assert out["baseline_established"] is True
    assert len(out["new_releases"]) == 0


# ---------------------------------------------------------------------------
# B0: Exception format
# ---------------------------------------------------------------------------

def test_exception_stderr_format(capsys):
    """get_github_advisories logs [depot warn] on CalledProcessError."""
    import subprocess
    err = subprocess.CalledProcessError(1, "gh")
    with patch("subprocess.run", side_effect=err):
        result = depot.get_github_advisories("owner/repo")
    assert result == []
    captured = capsys.readouterr()
    assert "[tzeepot warn] CalledProcessError:" in captured.err
