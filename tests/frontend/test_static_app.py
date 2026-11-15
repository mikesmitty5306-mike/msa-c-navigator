"""Static PWA (web/) checks: unit tests, iPhone/PWA setup, CSP, service worker, data contract, size budget, smoke test.

Run with the rest of the suite:  ..\\.venv\\Scripts\\python.exe -m pytest -q tests
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
APP = HERE.parents[1]
WEB = APP / "web"
FIXTURES = HERE / "fixtures" / "data"
PUBLIC_DATA = APP / "public-data"
# Optional: another contract-format export to check (e.g. a full export made with make_fixtures.py --full)
EXTRA_DATA = Path(os.environ["MSAC_TEST_EXPORT"]) if os.environ.get("MSAC_TEST_EXPORT") else None
sys.path.insert(0, str(HERE))
BUDGET_BYTES = 2 * 1024 * 1024
INITIAL_DATA = ["meta.json", "grades.json", "trials.json"]


def find_node() -> str | None:
    found = shutil.which("node")
    if found:
        return found
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "nodejs-lts" / "node.exe"
    return str(local) if local.exists() else None


def find_edge() -> str | None:
    for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
        if Path(p).exists():
            return p
    return shutil.which("chromium") or shutil.which("google-chrome") or shutil.which("chromium-browser")


def shell_files() -> list[str]:
    sw = (WEB / "sw.js").read_text(encoding="utf-8")
    block = re.search(r"const SHELL = \[(.*?)\];", sw, re.S).group(1)
    return re.findall(r'"([^"]+)"', block)


# ---------------------------------------------------------------- JavaScript unit tests (node:test)
def test_javascript_unit_tests_pass():
    node = find_node()
    if not node:
        pytest.skip("Node.js not installed")
    r = subprocess.run([node, "--test", str(HERE / "unit")], capture_output=True, text=True, timeout=120, cwd=APP)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert re.search(r"fail 0", r.stdout)


# ---------------------------------------------------------------- Home Screen app setup
def png_size(path: Path) -> tuple[int, int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    w, h, _depth, color = struct.unpack(">IIBB", data[16:26])
    return w, h, color


def test_manifest():
    m = json.loads((WEB / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert m["name"] == m["short_name"] == "MSA-C Navigator"
    assert m["display"] == "standalone" and m["start_url"] == "./" and m["scope"] == "./" and m["id"] == "./"
    for icon in m["icons"]:
        w, h, color = png_size(WEB / icon["src"])
        assert f"{w}x{h}" == icon["sizes"] and color == 2          # opaque RGB: no black corners on iOS
    assert any(i.get("purpose") == "maskable" for i in m["icons"])


def test_index_html_for_iphone_and_csp():
    page = (WEB / "index.html").read_text(encoding="utf-8")
    csp = re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', page).group(1)
    directives = dict(d.strip().split(" ", 1) for d in csp.split(";") if d.strip())
    assert directives["script-src"] == "'self'" and directives["style-src"] == "'self'"
    assert "unsafe" not in csp and directives["object-src"] == "'none'" and directives["connect-src"] == "'self'"
    for tag in ('name="robots" content="noindex, nofollow"', 'name="apple-mobile-web-app-capable" content="yes"',
                'name="apple-mobile-web-app-title" content="MSA-C Navigator"', 'rel="apple-touch-icon" sizes="180x180"',
                "viewport-fit=cover", 'name="color-scheme" content="light dark"', 'rel="manifest" href="manifest.webmanifest"',
                'name="apple-mobile-web-app-status-bar-style" content="black-translucent"', "Not medical advice."):
        assert tag in page, tag
    assert "Disallow: /" in (WEB / "robots.txt").read_text(encoding="utf-8")


def test_nothing_the_csp_would_block():
    page = (WEB / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)", page), "inline script in index.html"
    assert not re.search(r"\sstyle=", page), "inline style attribute in index.html"
    for js in [*(WEB / "js").glob("*.js"), WEB / "sw.js"]:
        text = js.read_text(encoding="utf-8")
        assert not re.search(r'\sstyle="', text), f"inline style attribute in {js.name} (blocked by style-src 'self')"
        assert not re.search(r'\son[a-z]+="', text), f"inline event handler in {js.name}"
        assert "eval(" not in text and "new Function" not in text, js.name


def test_external_links_open_outside_the_app():
    for js in (WEB / "js").glob("*.js"):
        text = js.read_text(encoding="utf-8")
        for m in re.finditer(r"<a [^>]*href=\"\$\{(?:p\.url|s\.url|d\.registry_url)[^>]*>", text):
            assert 'target="_blank"' in m.group(0) and 'rel="noopener"' in m.group(0), m.group(0)
        for m in re.finditer(r"<a [^>]*href=\"(?:mailto|tel):[^>]*>|<a [^>]*href=\"\$\{(?:mailto|tel\()[^>]*>", text):
            assert "target=" not in m.group(0), m.group(0)


def test_service_worker_caches_the_whole_shell():
    listed = set(shell_files())
    actual = {p.relative_to(WEB).as_posix() for p in WEB.rglob("*") if p.is_file()} - {"sw.js", "robots.txt"}
    assert "./" in listed
    assert listed - {"./"} == actual, f"missing from SHELL: {actual - listed}; listed but absent: {listed - {'./'} - actual}"
    sw = (WEB / "sw.js").read_text(encoding="utf-8")
    for needle in ('addEventListener("push"', "showNotification", 'addEventListener("notificationclick"', "openWindow",
                   '"skip-waiting"', "x-msac-offline", "data/meta.json"):
        assert needle in sw, needle


# ---------------------------------------------------------------- data contract (fixtures now; public-data when Codex's build exists)
def data_dirs():
    dirs = [pytest.param(FIXTURES, id="fixtures")]
    if (PUBLIC_DATA / "meta.json").exists():
        dirs.append(pytest.param(PUBLIC_DATA, id="public-data"))
    if EXTRA_DATA and (EXTRA_DATA / "meta.json").exists():
        dirs.append(pytest.param(EXTRA_DATA, id="export"))
    return dirs


def load(d: Path, name: str):
    return json.loads((d / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("d", data_dirs())
def test_data_follows_the_contract(d):
    meta = load(d, "meta.json")
    assert meta["schema_version"] == 1
    assert {"build_id", "generated_at", "sources", "counts", "files", "disclaimer"} <= set(meta)
    assert "push_public_key" in meta
    for name, info in meta["files"].items():
        data = (d / name).read_bytes()
        assert len(data) == info["bytes"] and hashlib.sha256(data).hexdigest() == info["sha256"], name
        assert json.loads(data)["schema_version"] == 1, name
    grades = load(d, "grades.json")
    assert set(grades["meanings"]) >= {"A", "B", "C", "D", "n/a"}
    for m in grades["mechanisms"]:
        assert {"id", "name", "kind", "description", "agents", "grade", "reason", "results", "trials", "activity"} <= set(m)
        assert set(m["results"]) == {"human", "animal", "in_vitro"}
        assert all(set(v) == {"positive", "negative", "mixed", "unclear"} for v in m["results"].values())
        assert set(m["trials"]) == {"recruiting", "completed", "stopped", "ongoing"}
        if f"mechanisms/{m['id']}.json" in meta["files"]:
            mf = load(d, f"mechanisms/{m['id']}.json")
            assert set(mf["studies"]) == {"human", "animal", "in_vitro"} and "msa_c_recent" in mf
            assert all("counts_as_failure" in s for s in mf["trials"]["stopped"])
    trials = load(d, "trials.json")["trials"]
    for t in trials:
        assert {"id", "title", "status", "status_label", "open", "active", "phase", "max_phase", "type", "sponsor", "fit",
                "mechanisms", "min_age", "max_age", "sex", "sites", "last_update", "status_changed", "rank"} <= set(t)
        assert all(len(s) == 5 for s in t["sites"])
        if f"trials/{t['id']}.json" in meta["files"]:
            detail = load(d, f"trials/{t['id']}.json")
            assert {"registry_url", "eligibility", "contacts", "sites", "inquiry"} <= set(detail)
    index = load(d, "library/index.json")
    assert index["columns"][0] == "uid" and len(index["columns"]) == 13
    for s in index["shards"]:
        rows = load(d, f"library/{s['name']}.json")["rows"]
        assert len(rows) == s["rows"] and all(len(r) == len(index["columns"]) for r in rows[:50])


# ---------------------------------------------------------------- size budget
def initial_download(data_dir: Path) -> dict:
    shell = [WEB / f for f in shell_files() if f != "./"]
    data = [data_dir / n for n in INITIAL_DATA]
    raw = sum(p.stat().st_size for p in shell + data)
    gz = sum(len(gzip.compress(p.read_bytes(), 6)) for p in shell + data)
    return {"files": len(shell) + len(data), "raw": raw, "gzip": gz,
            "shell_raw": sum(p.stat().st_size for p in shell), "data_raw": sum(p.stat().st_size for p in data)}


@pytest.mark.parametrize("d", data_dirs())
def test_initial_download_under_budget(d):
    size = initial_download(d)
    assert size["raw"] < BUDGET_BYTES, size
    assert size["gzip"] < BUDGET_BYTES / 2, size


# ---------------------------------------------------------------- smoke test: every screen renders in a real browser
SCREENS = [
    ("#/", "Best-supported mechanisms"), ("#/", "Welcome to MSA-C Navigator"), ("#/", "Data as of"),
    ("#/grades", "Evidence grades"), ("#/grades/help", "Solid human trial"), ("#/grades/asyn_aggregation", "Why:"),
    ("#/grades/asyn_aggregation/human", "Negative: reported no benefit"),
    ("#/grades/proteostasis/trials", "Counts as a failure in the grade"),
    ("#/trials?sort=promising", "Most promising first"), ("#/trial/NCT04165486", "Draft an inquiry email"), ("#/trials/NCT04165486", "Draft an inquiry email"),
    ("#/library?q=sirolimus", "Search looks in titles"), ("#/saved", "No saved trials yet"),
    ("#/settings", "Notifications work only in the Home Screen app"), ("#/settings", "Export backup"),
    ("#/settings/install", "new trial that people with MSA-C can join opens"), ("#/nope", "Page not found"),
]


@pytest.fixture(scope="module")
def preview():
    from serve_preview import serve_in_thread
    server, url = serve_in_thread(EXTRA_DATA if EXTRA_DATA else FIXTURES)
    yield url
    server.shutdown()


@pytest.mark.parametrize("route, expected", SCREENS)
def test_screen_renders(preview, route, expected):
    edge = find_edge()
    if not edge:
        pytest.skip("no Chromium-based browser found")
    node = find_node()
    if not node:
        pytest.skip("Node.js not installed")
    # Waits for the app's own "screen drawn" mark (<html data-ready="1">) instead of a fixed time budget.
    r = subprocess.run([node, str(HERE / "dump_ready.mjs"), edge, preview + route, "60000"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    if r.returncode == 3:
        pytest.skip(r.stderr.strip())
    dom = r.stdout
    assert r.returncode == 0, f"{route}: {r.stderr.strip()[:300]}"
    assert expected in dom, f"{route}: '{expected}' not found. Page says: {re.sub(r'<[^>]+>', ' ', dom)[:600]}"
    assert "Couldn't load this" not in dom
