# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright==1.63.0"]
# ///
"""Checks the /lab pages of a running lab server. Every page reachable from
/lab/ must return 200, except a file outside the folder lab.toml names, which
must return 403. Every page with a ```mermaid block, and the first page of each
kind, is then opened in Chrome in the light and in the dark scheme: each block
must be drawn or show Mermaid's error (the errors are printed), its background
must follow the scheme, and it must make no request to another host. Pages with
diagrams are saved as screenshots.

usage: uv run --script bin/check_pages.py [--shots FOLDER] [URL]

URL is http://127.0.0.1:$EXPERIMENT_LAB_PORT (default 5000) unless given. It
uses the installed Google Chrome; Playwright downloads no browser. Links on
folder listings and files are not followed, and from a run page only its files,
so the crawl stays within the lab's own pages.
"""
import argparse
import collections
import concurrent.futures
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from playwright.sync_api import sync_playwright

BACKGROUND = {"light": "rgb(255, 255, 255)", "dark": "rgb(13, 17, 23)"}  # --bg in base.html


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        href = dict(attrs).get("href")
        if tag == "a" and href:
            self.links.append(href)


def kind(path):
    """The kind of page: the part of the path after /lab/, "run file" for a run's file."""
    parts = urllib.parse.urlsplit(path).path.removeprefix("/lab/").split("/")
    return "run file" if parts[0] == "run" and "file" in parts[2:3] else parts[0] or "index"


def follow(source, target):
    t = urllib.parse.urlsplit(target)
    if t.netloc or not t.path.startswith("/lab/") or t.path.startswith("/lab/static/"):
        return False
    return {"file": False, "run file": False, "run": kind(target) == "run file"}.get(kind(source), True)


def fetch(base, path):
    try:
        with urllib.request.urlopen(base + path, timeout=120) as response:
            return response.status, response.headers.get_content_type(), response.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get_content_type(), ""


def crawl(base):
    """Each page reachable from /lab/: its status and whether it has a ```mermaid block."""
    found, frontier = {}, ["/lab/"]
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        while frontier:
            fetched = list(pool.map(lambda p: (p, fetch(base, p)), frontier))
            frontier = []
            for path, (status, content_type, body) in fetched:
                found[path] = (status, 'class="language-mermaid"' in body)
                if status != 200 or content_type != "text/html":
                    continue
                links = Links()
                links.feed(body)
                for href in links.links:
                    target = urllib.parse.urldefrag(urllib.parse.urljoin(path, href))[0]
                    if follow(path, target) and target not in found and target not in frontier:
                        frontier.append(target)
    return found


def draw(base, paths, shots):
    """Problems found opening the pages in Chrome, light and dark."""
    host = urllib.parse.urlsplit(base).netloc
    problems = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        for scheme in ("light", "dark"):
            context = browser.new_context(color_scheme=scheme)
            for path in paths:
                page = context.new_page()
                requested = []
                page.on("request", lambda r: requested.append(r.url))
                page.goto(base + path, wait_until="networkidle")
                blocks = page.locator("pre > code.language-mermaid").count()
                if blocks:
                    page.wait_for_function("n => document.querySelectorAll('.diagram svg, .diagram .error').length >= n",
                                           arg=blocks, timeout=30000)
                drawn = page.locator(".diagram svg").count()
                errors = page.locator(".diagram .error").all_inner_texts()
                background = page.evaluate("getComputedStyle(document.body).backgroundColor")
                outside = [url for url in requested if urllib.parse.urlsplit(url).netloc not in (host, "")]
                print(f"{scheme:5}  {path}  diagrams {drawn}/{blocks}  background {background}")
                for error in errors:
                    print("       " + error.replace("\n", "\n       "))
                if drawn + len(errors) != blocks:
                    problems.append(f"{scheme} {path}: {drawn} of {blocks} diagrams drawn, {len(errors)} errors")
                if background != BACKGROUND[scheme]:
                    problems.append(f"{scheme} {path}: background {background}")
                if outside:
                    problems.append(f"{scheme} {path}: requests to other hosts: {outside}")
                if blocks and shots:
                    page.screenshot(path=shots / f"{kind(path)}-{abs(hash(path))}-{scheme}.png", full_page=True)
                page.close()
            context.close()
        browser.close()
    return problems


def main():
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    p.add_argument("url", nargs="?", default=f"http://127.0.0.1:{os.environ.get('EXPERIMENT_LAB_PORT', '5000')}")
    p.add_argument("--shots", type=Path)
    a = p.parse_args()
    if a.shots:
        a.shots.mkdir(parents=True, exist_ok=True)
    pages = crawl(a.url.rstrip("/"))
    counts = collections.Counter((kind(path), status) for path, (status, _) in pages.items())
    for (k, status), n in sorted(counts.items()):
        print(f"{n:6}  {status}  {k}")
    problems = [f"{status} {path}" for path, (status, _) in pages.items()
                if status != 200 and not (status == 403 and kind(path) == "file")]
    first = {}
    for path, (status, _) in pages.items():
        if status == 200 and kind(path) != "run file":
            first.setdefault(kind(path), path)
    shown = sorted({*first.values(), *(path for path, (_, mermaid) in pages.items() if mermaid)})
    problems += draw(a.url.rstrip("/"), shown, a.shots)
    for problem in problems:
        print("PROBLEM", problem)
    if problems:
        sys.exit(f"{len(problems)} problems")
    print(f"all {len(pages)} pages return 200 or, for files outside the files folder, 403; "
          f"{len(shown)} drawn in light and dark")


if __name__ == "__main__":
    main()
