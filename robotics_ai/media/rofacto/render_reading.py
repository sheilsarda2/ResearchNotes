#!/usr/bin/env python3
"""Build the two RoFacto browser reading editions without changing Markdown.

From the repository root, after the Markdown and media are final:

    python3 robotics_ai/media/rofacto/render_reading.py

Or provide one or more Markdown paths explicitly:

    python3 robotics_ai/media/rofacto/render_reading.py "robotics_ai/RoFacto - Questions and Research.md"

Each output uses the source basename with an .html suffix, beside its .md file.
The shared stylesheet stays at media/rofacto/reading.css. Dependencies are the
installed markdown_it, matplotlib, and Pillow packages; all rendered math and the simple
Mermaid flowchart work offline. Unsupported formula/diagram syntax remains
readable as its original source. External video sources still require a network.
No Markdown, posters, GIFs, or other source media are modified.
"""

from __future__ import annotations

import argparse
import html
import io
import os
from pathlib import Path
import re
import textwrap
import unicodedata
from urllib.parse import quote, unquote, urlsplit, urlunsplit
import xml.etree.ElementTree as ET

from markdown_it import MarkdownIt
from markdown_it.token import Token
from PIL import Image
import matplotlib

matplotlib.use("Agg")
from matplotlib import rc_context
from matplotlib.font_manager import FontProperties
from matplotlib.mathtext import math_to_image


ASSET_DIR = Path(__file__).resolve().parent
DOCUMENT_DIR = ASSET_DIR.parents[1]
DOCUMENT_NAMES = (
    "RoFacto - Questions and Research.md",
    "Weekly Paper Review - 2026-09-10.md",
)
XML_SVG = "http://www.w3.org/2000/svg"
XML_XLINK = "http://www.w3.org/1999/xlink"
ET.register_namespace("", XML_SVG)
ET.register_namespace("xlink", XML_XLINK)


def escape(value: str) -> str:
    return html.escape(value, quote=True)


def relative_url(target: Path, directory: Path) -> str:
    return quote(Path(os.path.relpath(target, directory)).as_posix(), safe="/")


def media_dimensions(source: str, directory: Path):
    """Reserve the local asset's aspect ratio before its pixels are loaded."""
    parsed = urlsplit(source)
    if parsed.scheme or parsed.netloc:
        return None
    asset = directory / unquote(parsed.path)
    if not asset.is_file():
        return None
    try:
        if asset.suffix.lower() == ".svg":
            svg = ET.parse(asset).getroot()
            view_box = svg.attrib.get("viewBox", "").replace(",", " ").split()
            if len(view_box) == 4:
                width, height = float(view_box[2]), float(view_box[3])
            else:
                size = [re.fullmatch(r"([0-9.]+)(?:px)?", svg.attrib.get(axis, ""))
                        for axis in ("width", "height")]
                if not all(size):
                    return None
                width, height = (float(value.group(1)) for value in size)
        else:
            with Image.open(asset) as image:
                width, height = image.size
        return (round(width), round(height)) if width > 0 and height > 0 else None
    except (OSError, ValueError, ET.ParseError):
        return None


def remove_reading_prompt(source: str, source_path: Path) -> str:
    """The HTML edition need not invite the reader to open itself."""
    pattern = re.compile(
        r"(?m)^\[(?:Read with video controls|Open the reading edition[^\]\n]*)\]"
        r"\(([^)\n]+)\)\.[ \t]*"
    )

    def replace(match):
        destination = urlsplit(match.group(1))
        if not destination.scheme and Path(unquote(destination.path)).name == source_path.with_suffix(".html").name:
            return ""
        return match.group(0)

    return pattern.sub(replace, source, count=1)


def math_inline(state, silent: bool) -> bool:
    start = state.pos
    if state.src[start] != "$" or state.src.startswith("$$", start):
        return False
    if start + 1 >= state.posMax or state.src[start + 1].isspace():
        return False
    end = start + 1
    while end < state.posMax:
        character = state.src[end]
        if character == "\n":
            return False
        if character == "\\":
            end += 2
            continue
        if character == "$":
            if end == start + 1 or state.src[end - 1].isspace():
                return False
            if not silent:
                token = state.push("math_inline", "math", 0)
                token.content = state.src[start + 1:end]
                token.markup = "$"
            state.pos = end + 1
            return True
        end += 1
    return False


def math_block(state, start_line: int, end_line: int, silent: bool) -> bool:
    start = state.bMarks[start_line] + state.tShift[start_line]
    first_line = state.src[start:state.eMarks[start_line]].strip()
    if not first_line.startswith("$$"):
        return False
    tail = first_line[2:]
    content = []
    if tail.endswith("$$"):
        content.append(tail[:-2])
        closing_line = start_line
    else:
        if tail:
            content.append(tail)
        closing_line = start_line + 1
        while closing_line < end_line:
            pos = state.bMarks[closing_line] + state.tShift[closing_line]
            line = state.src[pos:state.eMarks[closing_line]]
            if line.strip().endswith("$$"):
                content.append(line.rstrip()[:-2])
                break
            content.append(line)
            closing_line += 1
        else:
            return False
    if silent:
        return True
    token = state.push("math_block", "math", 0)
    token.content = "\n".join(content).strip()
    token.markup = "$$"
    token.map = [start_line, closing_line + 1]
    token.block = True
    state.line = closing_line + 1
    return True


class MathRenderer:
    """Embed vector math; retain an accessible original-TeX representation."""

    def __init__(self):
        self.counter = 0
        self.cache = {}
        self.fallbacks = []

    def render(self, source: str, display: bool = False) -> str:
        self.counter += 1
        source = source.strip()
        wrapper = "div" if display else "span"
        class_name = "math-display" if display else "math-inline"
        try:
            if source not in self.cache:
                buffer = io.BytesIO()
                with rc_context({"mathtext.fontset": "stix", "svg.fonttype": "path", "savefig.transparent": True}):
                    depth = math_to_image(
                        "$" + source.replace("\n", " ") + "$",
                        buffer,
                        prop=FontProperties(size=16),
                        dpi=144,
                        format="svg",
                        color="#202b2e",
                    )
                self.cache[source] = (buffer.getvalue(), depth)
            svg_bytes, depth = self.cache[source]
            svg = ET.fromstring(svg_bytes)
            width = float(svg.attrib["width"].removesuffix("pt")) / 16
            height = float(svg.attrib["height"].removesuffix("pt")) / 16
            svg.set("width", "%.4fem" % width)
            svg.set("height", "%.4fem" % height)
            svg.set("aria-hidden", "true")
            svg.set("focusable", "false")
            svg.set("style", "vertical-align: -%.4fem" % (depth / 16))
            prefix = "formula-%d-" % self.counter
            identifiers = {element.attrib["id"]: prefix + element.attrib["id"]
                           for element in svg.iter() if "id" in element.attrib}
            for element in svg.iter():
                for name, value in list(element.attrib.items()):
                    if name == "id":
                        element.set(name, identifiers[value])
                    elif value.startswith("#") and value[1:] in identifiers:
                        element.set(name, "#" + identifiers[value[1:]])
                    else:
                        for old, new in identifiers.items():
                            value = value.replace("url(#%s)" % old, "url(#%s)" % new)
                        element.set(name, value)
            # Matplotlib's default global SVG style need not affect the article.
            for parent in svg.iter():
                for element in list(parent):
                    if element.tag in {"{%s}metadata" % XML_SVG, "{%s}style" % XML_SVG}:
                        parent.remove(element)
            picture = ET.tostring(svg, encoding="unicode")
            return ('<%s class="%s" role="math" aria-label="%s" title="%s">'
                    '%s<span class="sr-only">%s</span></%s>' %
                    (wrapper, class_name, escape(source), escape(source), picture,
                     escape(source), wrapper))
        except (ValueError, RuntimeError, TypeError) as exc:
            self.fallbacks.append((source, str(exc)))
            return '<%s class="%s math-source"><code>%s</code></%s>' % (
                wrapper, class_name, escape(source), wrapper)


def render_flowchart(source: str) -> str:
    """Draw the document's simple labeled DAG without a JavaScript dependency."""
    nodes = {}
    edges = []
    pattern = re.compile(
        r"^\s*([A-Za-z][\w]*)(?:\[([^\]]+)\])?\s*-->\s*"
        r"([A-Za-z][\w]*)(?:\[([^\]]+)\])?\s*;?\s*$"
    )
    for line in source.splitlines():
        if not line.strip() or re.match(r"\s*(?:flowchart|graph)\s+(?:LR|TD|TB)\s*$", line):
            continue
        match = pattern.match(line)
        if not match:
            raise ValueError("Unsupported Mermaid syntax")
        left, left_label, right, right_label = match.groups()
        nodes[left] = left_label or nodes.get(left, left)
        nodes[right] = right_label or nodes.get(right, right)
        edges.append((left, right))
    if not nodes:
        raise ValueError("Empty flowchart")
    outgoing = {node: [] for node in nodes}
    for left, right in edges:
        outgoing[left].append(right)
    distances = {}

    def distance(node, visiting):
        if node in visiting:
            raise ValueError("Cyclic Mermaid chart")
        if node not in distances:
            distances[node] = max((distance(next_node, visiting | {node}) + 1
                                   for next_node in outgoing[node]), default=0)
        return distances[node]

    for node in nodes:
        distance(node, set())
    maximum = max(distances.values())
    rows = {}
    for node in nodes:
        rows.setdefault(maximum - distances[node], []).append(node)
    box_width, box_height, gap_x, gap_y, margin = 280, 72, 36, 38, 20
    width = max(len(row) for row in rows.values()) * (box_width + gap_x) - gap_x + 2 * margin
    height = (maximum + 1) * (box_height + gap_y) - gap_y + 2 * margin
    positions = {}
    for rank, row in rows.items():
        row_width = len(row) * (box_width + gap_x) - gap_x
        offset = (width - row_width) / 2
        for index, node in enumerate(row):
            positions[node] = (offset + index * (box_width + gap_x), margin + rank * (box_height + gap_y))
    parts = [
        '<div class="flow-diagram"><svg xmlns="http://www.w3.org/2000/svg" '
        'viewBox="0 0 %s %s" role="img" aria-labelledby="action-flow-title">' % (width, height),
        '<title id="action-flow-title">Robot commands and initial scene become visual conditions '
        'for the predicted interaction video</title>',
        '<defs><marker id="action-flow-arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="#50736f"/></marker></defs>',
    ]
    for left, right in edges:
        x1, y1 = positions[left]
        x2, y2 = positions[right]
        x1 += box_width / 2
        x2 += box_width / 2
        y1 += box_height
        midpoint = (y1 + y2) / 2
        parts.append('<path d="M %g %g C %g %g, %g %g, %g %g" fill="none" '
                     'stroke="#50736f" stroke-width="2" marker-end="url(#action-flow-arrow)"/>' %
                     (x1, y1, x1, midpoint, x2, midpoint, x2, y2 - 3))
    for node, label in nodes.items():
        x, y = positions[node]
        lines = textwrap.wrap(label, width=30, break_long_words=False)
        parts.append('<g><rect x="%g" y="%g" width="%g" height="%g" rx="11" '
                     'fill="#eef3ef" stroke="#aac0b7"/>' % (x, y, box_width, box_height))
        line_start = y + box_height / 2 - (len(lines) - 1) * 10 + 5
        for index, line in enumerate(lines):
            parts.append('<text x="%g" y="%g" text-anchor="middle" '
                         'font-family="system-ui, sans-serif" font-size="16" fill="#202b2e">%s</text>' %
                         (x + box_width / 2, line_start + index * 20, escape(line)))
        parts.append("</g>")
    parts.append('</svg><details class="diagram-source"><summary>Diagram text</summary>'
                 '<pre><code>%s</code></pre></details></div>' % escape(source))
    return "".join(parts)


def video_markup(image_token: Token, link: str, source_directory: Path) -> str:
    preview = image_token.attrGet("src") or ""
    parsed = urlsplit(preview)
    poster = urlunsplit(parsed._replace(path=re.sub(r"\.gif$", ".jpg", parsed.path, flags=re.I)))
    video_source = link
    if not parsed.scheme and not parsed.netloc:
        local_video = source_directory / Path(unquote(parsed.path)).with_suffix(".mp4")
        if local_video.is_file():
            video_source = urlunsplit(parsed._replace(path=re.sub(r"\.gif$", ".mp4", parsed.path, flags=re.I)))
    label = image_token.content or "Research demonstration video"
    dimensions = media_dimensions(poster, source_directory)
    size = ' width="%d" height="%d"' % dimensions if dimensions else ""
    return ('<span class="video-embed"><video controls playsinline preload="none" '
            'poster="%s" aria-label="%s"%s><source src="%s" type="video/mp4">'
            'Your browser does not support this video. <a href="%s">Open the video</a>.'
            '</video><a class="video-source" href="%s">Open video separately</a></span>' %
            (escape(poster), escape(label), size, escape(video_source), escape(link), escape(link)))


def enhance_tokens(tokens, source_directory: Path):
    headings = []
    identifiers = set()
    for index, token in enumerate(tokens):
        if token.type == "heading_open":
            text = tokens[index + 1].content
            base = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
            base = re.sub(r"[^a-z0-9]+", "-", base).strip("-") or "section"
            identifier = base
            number = 2
            while identifier in identifiers:
                identifier = "%s-%d" % (base, number)
                number += 1
            identifiers.add(identifier)
            token.attrSet("id", identifier)
            headings.append((int(token.tag[1:]), text, identifier))
        if token.type != "inline" or not token.children:
            continue
        children = token.children
        enhanced = []
        offset = 0
        has_media = False
        while offset < len(children):
            if (offset + 2 < len(children) and children[offset].type == "link_open"
                    and children[offset + 1].type == "image"
                    and children[offset + 2].type == "link_close"):
                link = children[offset].attrGet("href") or ""
                preview = children[offset + 1].attrGet("src") or ""
                if urlsplit(link).path.lower().endswith(".mp4") and urlsplit(preview).path.lower().endswith(".gif"):
                    replacement = Token("html_inline", "", 0)
                    replacement.content = video_markup(children[offset + 1], link, source_directory)
                    enhanced.append(replacement)
                    has_media = True
                    offset += 3
                    continue
            child = children[offset]
            if child.type == "image":
                child.attrSet("loading", "lazy")
                child.attrSet("decoding", "async")
                dimensions = media_dimensions(child.attrGet("src") or "", source_directory)
                if dimensions:
                    child.attrSet("width", str(dimensions[0]))
                    child.attrSet("height", str(dimensions[1]))
                has_media = True
            if child.type == "link_open":
                link = child.attrGet("href") or ""
                parsed = urlsplit(link)
                # Link the two editions to one another while retaining all other citations.
                if not parsed.scheme and Path(unquote(parsed.path)).name in DOCUMENT_NAMES:
                    child.attrSet("href", urlunsplit(parsed._replace(path=parsed.path[:-3] + ".html")))
            enhanced.append(child)
            offset += 1
        token.children = enhanced
        if has_media and index > 0 and tokens[index - 1].type == "paragraph_open":
            tokens[index - 1].attrJoin("class", "media-paragraph")
    return headings


def render_markdown(source: str, source_directory: Path = DOCUMENT_DIR):
    renderer = MathRenderer()
    md = MarkdownIt("commonmark", {"html": True, "typographer": False}).enable("table")
    md.inline.ruler.before("escape", "math_inline", math_inline)
    md.block.ruler.before("fence", "math_block", math_block, {"alt": ["paragraph", "reference", "blockquote", "list"]})
    md.add_render_rule("math_inline", lambda self, tokens, index, options, env:
                       renderer.render(tokens[index].content))
    md.add_render_rule("math_block", lambda self, tokens, index, options, env:
                       renderer.render(tokens[index].content, display=True) + "\n")
    standard_fence = md.renderer.rules["fence"]

    def fence(self, tokens, index, options, env):
        token = tokens[index]
        if token.info.strip() == "mermaid":
            try:
                return render_flowchart(token.content) + "\n"
            except ValueError:
                pass
        return standard_fence(tokens, index, options, env)

    md.add_render_rule("fence", fence)
    md.add_render_rule("table_open", lambda self, tokens, index, options, env:
                       '<div class="table-scroll" role="region" aria-label="Scrollable table" tabindex="0"><table>\n')
    md.add_render_rule("table_close", lambda self, tokens, index, options, env: "</table></div>\n")
    tokens = md.parse(source)
    headings = enhance_tokens(tokens, source_directory)
    body = md.renderer.render(tokens, md.options, {})
    return body, headings, renderer


def render_document(source_path: Path) -> tuple[str, MathRenderer]:
    source_path = source_path.resolve()
    source = remove_reading_prompt(source_path.read_text(encoding="utf-8"), source_path)
    body, headings, renderer = render_markdown(source, source_path.parent)
    title = next((text for level, text, identifier in headings if level == 1), source_path.stem)
    is_questions = source_path.name == DOCUMENT_NAMES[0]
    navigation = []
    for name, label in zip(DOCUMENT_NAMES, ("Questions & research", "Weekly review")):
        destination = source_path.parent / Path(name).with_suffix(".html")
        current = ' aria-current="page"' if source_path.name == name else ""
        navigation.append('<a href="%s"%s>%s</a>' %
                          (relative_url(destination, source_path.parent), current, label))
    contents = ""
    if is_questions:
        links = []
        for level, text, identifier in headings:
            if level in (2, 3):
                links.append('<li class="toc-level-%d"><a href="#%s">%s</a></li>' %
                             (level, identifier, escape(text)))
        contents = ('<aside class="contents"><details class="toc-shell" open>'
                    '<summary>On this page</summary><nav aria-label="Table of contents"><ol>%s</ol></nav>'
                    '</details></aside>' % "".join(links))
    css_url = relative_url(ASSET_DIR / "reading.css", source_path.parent)
    markdown_url = relative_url(source_path, source_path.parent)
    page = '''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<meta name="generator" content="RoFacto reading edition">
<title>{title}</title>
<link rel="stylesheet" href="{css}">
</head>
<body class="{page_class}">
<a class="skip-link" href="#article">Skip to article</a>
<header class="site-header"><a class="site-label" href="#article">RoFacto / reading notes</a>
<nav aria-label="Reading editions">{navigation}</nav></header>
<div class="reading-layout">{contents}<main id="article" tabindex="-1"><article>
{body}
</article><footer class="article-footer"><a href="{markdown}">Markdown source</a><a href="#article">Back to top ↑</a></footer></main></div>
<script>
(() => {{
  const toc = document.querySelector('.toc-shell');
  const mobile = window.matchMedia('(max-width: 1050px)');
  if (toc) {{
    const adapt = () => {{ toc.open = !mobile.matches; }};
    adapt();
    mobile.addEventListener('change', adapt);
    toc.addEventListener('click', event => {{
      if (mobile.matches && event.target.closest('a')) toc.open = false;
    }});
    const links = [...toc.querySelectorAll('a[href^="#"]')];
    const observed = links.map(link => document.getElementById(link.hash.slice(1))).filter(Boolean);
    if ('IntersectionObserver' in window) {{
      const observer = new IntersectionObserver(entries => {{
        const visible = entries.filter(entry => entry.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (!visible.length) return;
        for (const link of links) {{
          if (link.hash === '#' + visible[0].target.id) link.setAttribute('aria-current', 'location');
          else link.removeAttribute('aria-current');
        }}
      }}, {{rootMargin: '-10% 0px -70% 0px'}});
      observed.forEach(heading => observer.observe(heading));
    }}
  }}
}})();
</script>
</body>
</html>
'''.format(title=escape(title), css=css_url,
           page_class="questions-edition" if is_questions else "review-edition",
           navigation="".join(navigation), contents=contents, body=body, markdown=markdown_url)
    page = "\n".join(line.rstrip() for line in page.splitlines()) + "\n"
    return page, renderer


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("documents", nargs="*", type=Path, help="Markdown files; default is both RoFacto documents")
    args = parser.parse_args()
    for source in args.documents or [DOCUMENT_DIR / name for name in DOCUMENT_NAMES]:
        if source.suffix.lower() != ".md":
            parser.error("Expected a Markdown file: %s" % source)
        page, renderer = render_document(source)
        destination = source.with_suffix(".html")
        destination.write_text(page, encoding="utf-8")
        print("Built %s (%d math expressions; %d source fallbacks)" %
              (destination, renderer.counter, len(renderer.fallbacks)))
        for formula, reason in renderer.fallbacks:
            print("  Formula kept as source: %s" % formula)


if __name__ == "__main__":
    main()
