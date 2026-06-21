#!/usr/bin/env python3
"""Generate docs/assets/metrics.svg — a "provable figures" panel for the Norse
Stack repo and its sibling service repos.

Stdlib only; hand-written SVG, no chart libraries.

The point of this panel is the opposite of a marketing banner: every figure is
COMPUTED at run time from the working tree (a cloc-like LOC count, a grep-based
test count, the docker-compose service count, the signal-layer count parsed from
the README), and each is labelled "generated" with the method used. If a figure
cannot be computed (e.g. a sibling repo is not checked out) it is OMITTED rather
than guessed — there are no hard-coded counts in this file.

Usage:
    python3 scripts/gen-metrics-panel.py            # writes docs/assets/metrics.svg
    python3 scripts/gen-metrics-panel.py --out X.svg
"""
from __future__ import annotations

import argparse
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
STACK = os.path.normpath(os.path.join(HERE, ".."))
PARENT = os.path.normpath(os.path.join(STACK, ".."))

# (label, extensions) for the cloc-like pass. Comment/blank stripping is
# language-aware enough to be honest: we count non-blank, non-pure-comment lines.
CODE_EXT = {
    ".go": "Go",
    ".py": "Python",
    ".java": "Java",
    ".ts": "TS/JS",
    ".tsx": "TS/JS",
    ".js": "TS/JS",
    ".sh": "Shell",
}

# Directories never counted (vendored / generated / VCS).
SKIP_DIRS = {
    ".git", "node_modules", "vendor", "dist", "build", "target",
    "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    "site", ".venv", "venv", ".idea", ".gradle", "testdata",
}

# Repos to scan (this repo + siblings). Missing ones are skipped silently.
REPOS = ["norse-stack", "muninn", "huginn", "sleipnir", "muninn-py"]


def _is_comment(line: str, ext: str) -> bool:
    s = line.strip()
    if not s:
        return True
    if ext in (".py", ".sh"):
        return s.startswith("#")
    if ext in (".go", ".java", ".ts", ".tsx", ".js"):
        return s.startswith("//") or s.startswith("*") or s.startswith("/*")
    return False


def count_loc(root: str) -> tuple[int, dict[str, int]]:
    """Return (total_code_lines, per-language breakdown) over root, cloc-like:
    skips blanks and pure-comment lines and the SKIP_DIRS."""
    total = 0
    by_lang: dict[str, int] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            ext = os.path.splitext(fn)[1]
            if ext not in CODE_EXT:
                continue
            path = os.path.join(dirpath, fn)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    n = sum(0 if _is_comment(ln, ext) else 1 for ln in f)
            except OSError:
                continue
            total += n
            by_lang[CODE_EXT[ext]] = by_lang.get(CODE_EXT[ext], 0) + n
    return total, by_lang


# Test detectors: Go/Java `func TestXxx`, Python `def test_xxx`, JS `it(`/`test(`.
TEST_PATTERNS = [
    (re.compile(r"\bfunc\s+Test[A-Z_]\w*\s*\("), (".go",)),
    (re.compile(r"^\s*def\s+test_\w+\s*\("), (".py",)),
    (re.compile(r"@Test\b"), (".java",)),
    (re.compile(r"\b(it|test)\s*\(\s*['\"]"), (".ts", ".tsx", ".js")),
]


def count_tests(root: str) -> int:
    n = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            ext = os.path.splitext(fn)[1]
            path = os.path.join(dirpath, fn)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            except OSError:
                continue
            for pat, exts in TEST_PATTERNS:
                if ext in exts:
                    n += len(pat.findall(text))
    return n


def count_compose_services(compose_path: str) -> int | None:
    if not os.path.exists(compose_path):
        return None
    in_services = False
    count = 0
    for line in open(compose_path, encoding="utf-8"):
        if re.match(r"^services:\s*$", line):
            in_services = True
            continue
        if in_services and re.match(r"^[A-Za-z]", line):
            break  # left the services: block
        if in_services and re.match(r"^  [A-Za-z0-9_-]+:\s*(#.*)?$", line):
            count += 1
    return count or None


def count_signal_layers(readme_path: str) -> int | None:
    """Count rows of the Signal Layers table ONLY. Scoped to the section whose
    heading matches /Signal Layers/, stopping at the next markdown heading, so
    other numbered tables in the README (e.g. the port map) are not miscounted.
    Each row must lead with a small contiguous index (1,2,3,...)."""
    if not os.path.exists(readme_path):
        return None
    in_section = False
    n = 0
    for line in open(readme_path, encoding="utf-8"):
        if line.startswith("#") or re.match(r"^#{1,6}\s", line):
            # a markdown heading line
            if re.search(r"signal\s+layers", line, re.IGNORECASE):
                in_section = True
                n = 0
                continue
            if in_section:
                break  # next heading ends the section
        if in_section:
            m = re.match(r"^\|\s*(\d+)\s*\|", line)
            if m and int(m.group(1)) == n + 1:
                n += 1
    return n or None


def count_adrs() -> int | None:
    """Count ADR markdown files across repos (docs/adr/*.md)."""
    total = 0
    found = False
    for repo in REPOS:
        adr = os.path.join(PARENT, repo, "docs", "adr")
        if os.path.isdir(adr):
            found = True
            total += sum(1 for f in os.listdir(adr)
                         if f.endswith(".md") and re.match(r"^\d", f))
    return total if found else None


# ---------------------------------------------------------------------------
# Collect figures
# ---------------------------------------------------------------------------


def collect() -> dict:
    repos_present = [r for r in REPOS if os.path.isdir(os.path.join(PARENT, r))]
    total_loc = 0
    total_tests = 0
    lang_totals: dict[str, int] = {}
    per_repo = []
    for r in repos_present:
        root = os.path.join(PARENT, r)
        loc, by_lang = count_loc(root)
        tests = count_tests(root)
        total_loc += loc
        total_tests += tests
        for k, v in by_lang.items():
            lang_totals[k] = lang_totals.get(k, 0) + v
        per_repo.append((r, loc, tests))

    services = count_compose_services(os.path.join(STACK, "docker-compose.yml"))
    layers = count_signal_layers(os.path.join(STACK, "README.md"))
    adrs = count_adrs()

    return dict(
        repos_present=repos_present,
        total_loc=total_loc,
        total_tests=total_tests,
        lang_totals=lang_totals,
        per_repo=sorted(per_repo, key=lambda t: -t[1]),
        services=services,
        layers=layers,
        adrs=adrs,
    )


# ---------------------------------------------------------------------------
# SVG
# ---------------------------------------------------------------------------

W = 1100
PAD = 28

C = dict(
    bg="#0d1117", panel="#161b22", grid="#21262d", ink="#e6edf3",
    sub="#8b949e", accent="#58a6ff", pos="#3fb950", warn="#d29922",
    purple="#bc8cff", cyan="#39c5cf",
)

LANG_COLOR = {
    "Go": "#39c5cf", "Python": "#58a6ff", "Java": "#d29922",
    "TS/JS": "#bc8cff", "Shell": "#3fb950",
}


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def kfmt(n: int) -> str:
    if n >= 1000:
        return f"{n/1000:.1f}k"
    return str(n)


def build_svg(d: dict) -> str:
    # Build only the stat cards we could actually compute.
    cards = []
    cards.append(("Lines of code", kfmt(d["total_loc"]),
                  "non-blank/non-comment, cloc-like", "accent"))
    cards.append(("Automated tests", str(d["total_tests"]),
                  "grep func Test / def test_ / @Test", "pos"))
    if d["services"]:
        cards.append(("Compose services", str(d["services"]),
                      "parsed from docker-compose.yml", "cyan"))
    if d["layers"]:
        cards.append(("Signal layers", str(d["layers"]),
                      "parsed from README table", "purple"))
    cards.append(("Service repos", str(len(d["repos_present"])),
                  "polyglot, present sibling checkouts", "warn"))
    if d["adrs"]:
        cards.append(("ADRs", str(d["adrs"]),
                      "docs/adr/*.md across repos", "ink"))

    # layout
    card_h = 96
    cols = 3
    rows = (len(cards) + cols - 1) // cols
    gap = 16
    grid_top = 78
    avail = W - PAD * 2
    cw = (avail - gap * (cols - 1)) / cols

    # right column: language bar + per-repo. Make total height fit content.
    lang_h = 150
    repo_h = 24 + 22 * max(1, len(d["per_repo"]))
    H = grid_top + rows * (card_h + gap) + max(lang_h, repo_h) + 64

    out: list[str] = []
    out.append(
        f'<svg viewBox="0 0 {W} {int(H)}" xmlns="http://www.w3.org/2000/svg" '
        f'font-family="ui-sans-serif,system-ui,sans-serif" role="img" '
        f'aria-label="Norse Stack provable metrics panel (computed at build time)">'
    )
    # var(--name) used for readability below; resolve_vars() inlines literals
    # before return. GitHub strips <style>/CSS custom properties from SVGs
    # embedded as images, so no var() may survive into the committed file.
    out.append(f'<rect width="{W}" height="{int(H)}" fill="var(--bg)"/>')

    # header
    out.append(f'<text x="{PAD}" y="34" fill="var(--ink)" font-size="22" '
               f'font-weight="700">Norse Stack — Provable Metrics</text>')
    out.append(f'<text x="{PAD}" y="55" fill="var(--accent)" font-size="12" '
               f'font-family="ui-monospace,monospace">'
               f'every figure computed at build time from the working tree by '
               f'scripts/gen-metrics-panel.py — not claimed, generated</text>')

    # stat cards
    for i, (label, value, method, color) in enumerate(cards):
        r, cidx = divmod(i, cols)
        x = PAD + cidx * (cw + gap)
        y = grid_top + r * (card_h + gap)
        out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{cw:.1f}" '
                   f'height="{card_h}" rx="8" fill="var(--panel)" '
                   f'stroke="var(--grid)"/>')
        out.append(f'<text x="{x+16:.1f}" y="{y+26:.1f}" fill="var(--sub)" '
                   f'font-size="11" font-weight="600" letter-spacing="0.05em" '
                   f'font-family="ui-monospace,monospace">{esc(label.upper())}</text>')
        out.append(f'<text x="{x+16:.1f}" y="{y+62:.1f}" fill="var(--{color})" '
                   f'font-size="34" font-weight="800" '
                   f'font-family="ui-monospace,monospace">{esc(value)}</text>')
        out.append(f'<text x="{x+16:.1f}" y="{y+84:.1f}" fill="#6e7681" '
                   f'font-size="9.5" font-family="ui-monospace,monospace">'
                   f'{esc(method[:54])}</text>')

    panel_y = grid_top + rows * (card_h + gap)

    # language composition bar (left half)
    lw = (avail - gap) / 2
    lx = PAD
    out.append(f'<rect x="{lx:.1f}" y="{panel_y:.1f}" width="{lw:.1f}" '
               f'height="{lang_h}" rx="8" fill="var(--panel)" '
               f'stroke="var(--grid)"/>')
    out.append(f'<text x="{lx+16:.1f}" y="{panel_y+24:.1f}" fill="var(--sub)" '
               f'font-size="11" font-weight="600" letter-spacing="0.05em" '
               f'font-family="ui-monospace,monospace">LANGUAGE COMPOSITION (LOC)</text>')
    langs = sorted(d["lang_totals"].items(), key=lambda t: -t[1])
    tot = sum(v for _, v in langs) or 1
    bar_x, bar_y, bar_w, bar_h = lx + 16, panel_y + 40, lw - 32, 22
    cx = bar_x
    for name, v in langs:
        seg = bar_w * (v / tot)
        out.append(f'<rect x="{cx:.1f}" y="{bar_y:.1f}" width="{seg:.1f}" '
                   f'height="{bar_h}" fill="{LANG_COLOR.get(name, "#888")}"/>')
        cx += seg
    out.append(f'<rect x="{bar_x:.1f}" y="{bar_y:.1f}" width="{bar_w:.1f}" '
               f'height="{bar_h}" rx="3" fill="none" stroke="var(--grid)"/>')
    # legend
    ly = bar_y + bar_h + 24
    lcx = bar_x
    for name, v in langs:
        pct = 100 * v / tot
        out.append(f'<rect x="{lcx:.1f}" y="{ly-9:.1f}" width="10" height="10" '
                   f'rx="2" fill="{LANG_COLOR.get(name, "#888")}"/>')
        txt = f"{name} {pct:.0f}%"
        out.append(f'<text x="{lcx+15:.1f}" y="{ly:.1f}" fill="var(--ink)" '
                   f'font-size="11" font-family="ui-monospace,monospace">'
                   f'{esc(txt)}</text>')
        lcx += 24 + len(txt) * 7.2

    # per-repo LOC/tests (right half)
    rx = PAD + lw + gap
    out.append(f'<rect x="{rx:.1f}" y="{panel_y:.1f}" width="{lw:.1f}" '
               f'height="{repo_h}" rx="8" fill="var(--panel)" '
               f'stroke="var(--grid)"/>')
    out.append(f'<text x="{rx+16:.1f}" y="{panel_y+24:.1f}" fill="var(--sub)" '
               f'font-size="11" font-weight="600" letter-spacing="0.05em" '
               f'font-family="ui-monospace,monospace">PER-REPO (LOC · TESTS)</text>')
    ry = panel_y + 46
    maxloc = max((loc for _, loc, _ in d["per_repo"]), default=1)
    for name, loc, tests in d["per_repo"]:
        out.append(f'<text x="{rx+16:.1f}" y="{ry:.1f}" fill="var(--ink)" '
                   f'font-size="11.5" font-family="ui-monospace,monospace">'
                   f'{esc(name)}</text>')
        # mini bar
        mb_x = rx + 150
        mb_w = (lw - 150 - 120) * (loc / maxloc)
        out.append(f'<rect x="{mb_x:.1f}" y="{ry-10:.1f}" width="{max(mb_w,1):.1f}" '
                   f'height="11" rx="2" fill="var(--accent)" opacity="0.7"/>')
        out.append(f'<text x="{rx+lw-16:.1f}" y="{ry:.1f}" fill="var(--sub)" '
                   f'font-size="11" text-anchor="end" '
                   f'font-family="ui-monospace,monospace">'
                   f'{kfmt(loc)} · {tests}t</text>')
        ry += 22

    out.append(f'<text x="{PAD}" y="{int(H)-14}" fill="var(--sub)" '
               f'font-size="10" font-family="ui-monospace,monospace">'
               f'Reproduce: python3 scripts/gen-metrics-panel.py · '
               f'omits any figure it cannot compute (no hard-coded counts).</text>')
    out.append('</svg>')
    return resolve_vars("\n".join(out))


def resolve_vars(svg: str) -> str:
    """Inline every var(--name) to its literal from C so the SVG survives
    GitHub's image-embed sanitizer (which drops <style> and CSS custom
    properties). Raises if any var() is left unresolved."""
    for name, value in C.items():
        svg = svg.replace(f"var(--{name})", value)
    if "var(--" in svg:
        leftover = re.findall(r"var\(--[^)]*\)", svg)
        raise ValueError(f"unresolved CSS vars in SVG: {sorted(set(leftover))}")
    return svg


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    default_out = os.path.join(STACK, "docs", "assets", "metrics.svg")
    ap.add_argument("--out", default=os.path.normpath(default_out))
    args = ap.parse_args()

    d = collect()
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    svg = build_svg(d)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(svg + "\n")
    print(f"wrote {args.out} ({len(svg)} bytes)")
    print(f"  LOC={d['total_loc']} tests={d['total_tests']} "
          f"services={d['services']} layers={d['layers']} "
          f"repos={d['repos_present']} adrs={d['adrs']}")


if __name__ == "__main__":
    main()
