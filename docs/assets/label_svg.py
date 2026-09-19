#!/usr/bin/env python3
"""Add role, title and desc to a generated SVG so it is an accessible figure.

svg-term-cli writes a bare <svg> element. GitHub renders it through an <img> tag, where
the alt text does the work, but the file is also opened on its own, so it carries its own
name and description. Usage: label_svg.py FILE "title" "description".

It also opens the window corner out to the 12 pixel radius the other README pictures use,
which svg-term-cli hard codes at 5.
"""
import html
import re
import sys

path, title, desc = sys.argv[1], sys.argv[2], sys.argv[3]
slug = re.sub(r"[^a-z0-9]+", "-", path.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower())
source = open(path, encoding="utf-8").read()

source = re.sub(r"\s+role=\"img\"|\s+aria-labelledby=\"[^\"]*\"", "", source, count=2)
source = re.sub(r"<title\b.*?</title>|<desc\b.*?</desc>", "", source, flags=re.S)
source = re.sub(r'(<rect\b[^>]*?)rx="5" ry="5"', r'\g<1>rx="12" ry="12"', source, count=1)
# Quiet the three window dots down to the one hairline grey the other pictures use.
for loud in ("#ff5f58", "#ffbd2e", "#18c132"):
    source = source.replace(f'fill="{loud}"', 'fill="#30363d"')

open_tag = re.match(r"<svg\b[^>]*>", source)
if not open_tag:
    sys.exit(f"{path}: no <svg> element")
head = open_tag.group(0)[:-1] + f' role="img" aria-labelledby="{slug}-title {slug}-desc">'
labels = (
    f'<title id="{slug}-title">{html.escape(title)}</title>'
    f'<desc id="{slug}-desc">{html.escape(desc)}</desc>'
)
open(path, "w", encoding="utf-8").write(head + labels + source[open_tag.end():])
print(f"labelled {path}")
