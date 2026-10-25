# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright==1.63.0"]
# ///
"""Checks the /lab pages of a running lab server. For each project of the
picker, every page reachable from /lab/ must return 200, and only the pages of
picks() may show the picker; the pages link only files that /lab/file serves.
Every page with a ```mermaid block, an audio player or an image shown in place,
and the first page of each kind for each project, is then opened in Chrome in
the light and in the dark scheme: each block must be drawn or show Mermaid's
error (the errors are printed), each player must load its audio and each image
must show, its background must follow the scheme, and it must make no request to
another host. Pages with diagrams are saved as screenshots.

usage: uv run --script checks/check_pages.py [--shots FOLDER] [URL]

URL is http://127.0.0.1:$EXPERIMENT_LAB_PORT (default 5000) unless given. It
uses the installed Google Chrome; Playwright downloads no browser. Links on
folder listings and files are not followed, and from a run page only its files,
so the crawl stays within the lab's own pages.
"""
import argparse
import collections
import concurrent.futures
import html
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping
from html.parser import HTMLParser
from pathlib import Path
from typing import NamedTuple

from playwright.sync_api import Page, sync_playwright

BACKGROUND = {"light": "rgb(255, 255, 255)", "dark": "rgb(13, 17, 23)"}  # --bg in base.html
SHOWN = ('class="language-mermaid"', "<audio ", '<img class="media"')  # what Chrome must draw, play or show
PICKER = '<select aria-label="Project"'
# The players' and images' sources that do not load, each after at most 30 s.
NOT_LOADED = """() => {
  const loads = (element, start, done) => Promise.race([
    new Promise(ok => {
      element.addEventListener(done, () => ok(null));
      element.addEventListener("error", () => ok(element.src));
      start();
    }),
    new Promise(ok => setTimeout(() => ok(element.src + " (no answer in 30 s)"), 30000))]);
  const audio = Array.from(document.querySelectorAll("audio"),
                           a => loads(a, () => { a.preload = "metadata"; a.load(); }, "loadedmetadata"));
  const images = Array.from(document.querySelectorAll("img.media"), img => img.complete
    ? (img.naturalWidth ? null : img.src) : loads(img, () => { img.loading = "eager"; }, "load"));
  return Promise.all([...audio, ...images]).then(r => r.filter(Boolean));
}"""

type Attribute = tuple[str, str | None]
type Kind = tuple[str | None, str]  # a project and a kind of page


class Answer(NamedTuple):
    status: int
    content_type: str
    body: str


class Fetched(NamedTuple):
    status: int
    shows: bool  # has something of SHOWN
    picker: bool


class Visit(NamedTuple):
    project: str | None
    path: str


class Seen(NamedTuple):
    """What Chrome shows of a page."""
    blocks: int  # ```mermaid blocks
    drawn: int
    errors: list[str]  # Mermaid's
    media: int  # audio players and images
    not_loaded: list[str]
    background: str
    outside: list[str]  # requests to other hosts


class Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[Attribute]) -> None:
        href = dict(attrs).get("href")
        if tag == "a" and href:
            self.links.append(href)


def kind(path: str) -> str:
    """The kind of page: the part of the path after /lab/, "run file" for a run's file."""
    parts = urllib.parse.urlsplit(path).path.removeprefix("/lab/").split("/")
    return "run file" if parts[0] == "run" and "file" in parts[2:3] else parts[0] or "index"


def follow(source: str, target: str) -> bool:
    t = urllib.parse.urlsplit(target)
    if t.netloc or not t.path.startswith("/lab/") or t.path.startswith("/lab/static/"):
        return False
    return {"file": False, "run file": False, "run": kind(target) == "run file"}.get(kind(source), True)


def picks(path: str) -> bool:
    """Whether the page shows the picker: the records, experiments and query
    pages, and the files page of the picked project (no path)."""
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
    return kind(path) in ("index", "experiments", "query") or kind(path) == "file" and "path" not in query


def cookie(project: str) -> str:
    """The cookie that picks the project, as the picker sets it."""
    return f"project={urllib.parse.quote(project, safe='')}"


def fetch(base: str, path: str, project: str | None) -> Answer:
    request = urllib.request.Request(base + path, headers={"Cookie": cookie(project)} if project else {})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return Answer(response.status, response.headers.get_content_type(),
                          response.read().decode(errors="replace"))
    except urllib.error.HTTPError as e:
        return Answer(e.code, e.headers.get_content_type(), "")


def projects(base: str) -> list[str | None]:
    """The projects the picker offers; [None], no project, if it offers none."""
    found: list[str | None] = [html.unescape(p) for p in re.findall(r"<option[^>]*>([^<]*)</option>",
                                                                    fetch(base, "/lab/", None).body)]
    return found or [None]


def crawl(base: str, project: str | None) -> dict[str, Fetched]:
    """Each page reachable from /lab/ with the project picked."""
    found, frontier = {}, ["/lab/"]
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        while frontier:
            fetched = list(pool.map(lambda p: (p, fetch(base, p, project)), frontier))
            frontier = []
            for path, (status, content_type, body) in fetched:
                found[path] = Fetched(status, any(s in body for s in SHOWN), PICKER in body)
                if status != 200 or content_type != "text/html":
                    continue
                links = Links()
                links.feed(body)
                for href in links.links:
                    target = urllib.parse.urldefrag(urllib.parse.urljoin(path, href))[0]
                    if follow(path, target) and target not in found and target not in frontier:
                        frontier.append(target)
    return found


def crawl_all(base: str) -> dict[Visit, Fetched]:
    """The pages of every project the picker offers; prints how many of each
    kind answered with each status."""
    pages = {Visit(project, path): found for project in projects(base)
             for path, found in crawl(base, project).items()}
    counts = collections.Counter((v.project, kind(v.path), f.status) for v, f in pages.items())
    for (project, k, status), n in sorted(counts.items(), key=str):
        print(f"{n:6}  {status}  {project}  {k}")
    return pages


def to_draw(pages: Mapping[Visit, Fetched]) -> list[Visit]:
    """The first page of each kind for each project, and each page with
    something of SHOWN once, with the first project it was found with."""
    first: dict[Kind, Visit] = {}
    drawn: dict[str, Visit] = {}
    for v, f in pages.items():
        if f.status == 200 and kind(v.path) != "run file":
            first.setdefault((v.project, kind(v.path)), v)
        if f.shows:
            drawn.setdefault(v.path, v)
    return sorted({*first.values(), *drawn.values()}, key=str)


def wrong(pages: Mapping[Visit, Fetched]) -> list[str]:
    """The pages that do not return 200, and those that show the picker but
    should not, or the other way round."""
    return [f"{f.status} {v.project} {v.path}" if f.status != 200 else
            f"{v.project} {v.path}: the picker is {'shown' if f.picker else 'missing'}"
            for v, f in pages.items() if f.status != 200 or f.picker != picks(v.path)]


def see(page: Page, url: str, host: str) -> Seen:
    requested = []
    page.on("request", lambda r: requested.append(r.url))
    page.goto(url, wait_until="networkidle")
    blocks = page.locator("pre > code.language-mermaid").count()
    if blocks:
        page.wait_for_function("n => document.querySelectorAll('.diagram svg, .diagram .error').length >= n",
                               arg=blocks, timeout=30000)
    return Seen(blocks, page.locator(".diagram svg").count(), page.locator(".diagram .error").all_inner_texts(),
                page.locator("audio, img.media").count(), page.evaluate(NOT_LOADED),
                page.evaluate("getComputedStyle(document.body).backgroundColor"),
                [url for url in requested if urllib.parse.urlsplit(url).netloc not in (host, "")])


def problems(seen: Seen, scheme: str) -> list[str]:
    return [problem for problem, wrong in (
        (f"{seen.drawn} of {seen.blocks} diagrams drawn, {len(seen.errors)} errors",
         seen.drawn + len(seen.errors) != seen.blocks),
        (f"background {seen.background}", seen.background != BACKGROUND[scheme]),
        (f"these do not load: {seen.not_loaded}", bool(seen.not_loaded)),
        (f"requests to other hosts: {seen.outside}", bool(seen.outside))) if wrong]


def draw(base: str, visits: Iterable[Visit], shots: Path | None) -> list[str]:
    """Problems found opening the pages in Chrome, light and dark."""
    host = urllib.parse.urlsplit(base).netloc
    found = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        for scheme in ("light", "dark"):
            context = browser.new_context(color_scheme=scheme)
            for project, path in visits:
                if project:
                    context.add_cookies([{"name": "project", "value": cookie(project).partition("=")[2], "url": base}])
                page = context.new_page()
                seen = see(page, base + path, host)
                print(f"{scheme:5}  {project}  {path}  diagrams {seen.drawn}/{seen.blocks}  audio and images "
                      f"{seen.media - len(seen.not_loaded)}/{seen.media}  background {seen.background}")
                for error in seen.errors:
                    print("       " + error.replace("\n", "\n       "))
                found += [f"{scheme} {project} {path}: {problem}" for problem in problems(seen, scheme)]
                if seen.blocks and shots:
                    page.screenshot(path=shots / f"{kind(path)}-{abs(hash(path))}-{scheme}.png", full_page=True)
                page.close()
            context.close()
        browser.close()
    return found


def main() -> None:
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    p.add_argument("url", nargs="?", default=f"http://127.0.0.1:{os.environ.get('EXPERIMENT_LAB_PORT', '5000')}")
    p.add_argument("--shots", type=Path)
    a = p.parse_args()
    if a.shots:
        a.shots.mkdir(parents=True, exist_ok=True)
    base = a.url.rstrip("/")
    pages = crawl_all(base)
    shown = to_draw(pages)
    found = wrong(pages) + draw(base, shown, a.shots)
    for problem in found:
        print("PROBLEM", problem)
    if found:
        sys.exit(f"{len(found)} problems")
    print(f"all {len(pages)} pages return 200; {len(shown)} drawn in light and dark")


if __name__ == "__main__":
    main()
