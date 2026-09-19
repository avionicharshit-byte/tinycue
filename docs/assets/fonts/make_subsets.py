#!/usr/bin/env python3
"""Rebuild the two woff2 subsets that docs/assets/*.svg embed.

The SVGs are shown through an <img> tag, where a webfont cannot be fetched, so the
glyphs they need travel inside the file as base64 woff2. This script fetches the two
upstream faces, keeps only the characters the pictures use, and writes the subsets
beside itself. Both faces are SIL Open Font License 1.1; see ../FONT-LICENSE.txt.

    pip install fonttools brotli
    python3 docs/assets/fonts/make_subsets.py

Only needed when a picture gains a character the current subsets do not carry.
"""
import pathlib
import subprocess
import sys
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
RAW = "https://raw.githubusercontent.com/google/fonts/main/ofl"
SOURCES = {
    "sg-subset.woff2": f"{RAW}/schibstedgrotesk/SchibstedGrotesk%5Bwght%5D.ttf",
    "pm-subset.woff2": f"{RAW}/ibmplexmono/IBMPlexMono-Regular.ttf",
}

CHARS = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    " .,:;!?'\"()[]{}<>/\\|-_=+*&%#@$^~`"
)
UNICODES = sorted({ord(c) for c in CHARS} | {0x00A0, 0x00B7, 0x2019, 0x201C, 0x201D, 0x2192})


def main() -> None:
    unis = ",".join(f"U+{u:04X}" for u in UNICODES)
    for out, url in SOURCES.items():
        src = HERE / (out.replace("-subset.woff2", "-source.ttf"))
        print(f"fetching {url}")
        urllib.request.urlretrieve(url, src)
        subprocess.run(
            [sys.executable, "-m", "fontTools.subset", str(src),
             f"--unicodes={unis}", "--layout-features=kern,liga,calt,tnum",
             "--flavor=woff2", "--no-hinting", "--desubroutinize",
             f"--output-file={HERE / out}"],
            check=True,
        )
        src.unlink()
        print(f"{out}: {(HERE / out).stat().st_size} bytes")


if __name__ == "__main__":
    main()
