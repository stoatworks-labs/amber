#!/usr/bin/env python3
"""Write THIRD-PARTY-NOTICES.txt: the licence texts the plugin binary owes.

    scripts/third-party-notices.py            # (re)write THIRD-PARTY-NOTICES.txt
    scripts/third-party-notices.py --check    # exit 1 if it is out of date

The converter ships no third-party code, but the plugin does: Ruffle and every
crate it pulls in are compiled into Amber.bundle and Amber.dll, along with the
FFGL SDK and, on Windows, GLEW. MIT, BSD and Apache-2.0 all ask for their notices
to travel with the binary, and a hand-kept list goes stale the first time the
Ruffle pin moves. So this reads the real build:

  - The crate set is `cargo tree` over normal, non-proc-macro edges for each
    release target, which is what reaches the binary. Build scripts and proc
    macros run at compile time and ship nothing.
  - Each crate's licence files come from its own package. A crate inside a git
    workspace (all of Ruffle's) keeps them at the workspace root, so that is
    searched next. The few crates.io packages that carry no file at all get the
    standard text for the licence their Cargo.toml declares.
  - The FFGL SDK and GLEW texts come from the external/ffgl submodule, which the
    build needs checked out anyway.
  - Ruffle's fallback font (Noto Sans, merged with part of Noto Sans Math) is
    compiled in through `include_bytes!` and carries no licence file in Ruffle's
    tree, so its OFL notice is written here from the font's own name table and
    notofonts' OFL.txt.

Needs cargo and the network the first time (cargo fetches the Windows-only crates
to read their manifests). Run it after any change to source/amber_core/Cargo.lock.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "source" / "amber_core"
FFGL = ROOT / "external" / "ffgl"
OUT = ROOT / "THIRD-PARTY-NOTICES.txt"

# The release targets: the two slices of the universal macOS bundle, and the
# Windows DLL.
MAC = ("aarch64-apple-darwin", "x86_64-apple-darwin")
WIN = ("x86_64-pc-windows-msvc",)

RUFFLE_REPO = "github.com/ruffle-rs/ruffle?"

LICENCE_FILE = re.compile(
    r"^(LICEN[CS]E|COPYING|NOTICE|UNLICENSE|COPYRIGHT)([-_.].*)?$", re.I)

RULE = "=" * 78
THIN = "-" * 78

MIT_TEXT = """\
Copyright (c) {holder}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE."""

CC0_TEXT = """\
CC0-1.0: dedicated to the public domain under the Creative Commons CC0 1.0
Universal dedication (https://creativecommons.org/publicdomain/zero/1.0/).
No notice is required; it is listed here for completeness."""

# The copyright lines are notofonts' own, from the OFL.txt in each source repo;
# the first also appears in the subset font's name table. The body is the
# unmodified SIL Open Font License 1.1.
NOTO_OFL = """\
Copyright 2022 The Noto Project Authors (https://github.com/notofonts/latin-greek-cyrillic)
Copyright 2022 The Noto Project Authors (https://github.com/notofonts/math)

This Font Software is licensed under the SIL Open Font License, Version 1.1.
This license is copied below, and is also available with a FAQ at:
https://openfontlicense.org

-----------------------------------------------------------
SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007
-----------------------------------------------------------

PREAMBLE
The goals of the Open Font License (OFL) are to stimulate worldwide
development of collaborative font projects, to support the font
creation efforts of academic and linguistic communities, and to
provide a free and open framework in which fonts may be shared and
improved in partnership with others.

The OFL allows the licensed fonts to be used, studied, modified and
redistributed freely as long as they are not sold by themselves. The
fonts, including any derivative works, can be bundled, embedded,
redistributed and/or sold with any software provided that any reserved
names are not used by derivative works. The fonts and derivatives,
however, cannot be released under any other type of license. The
requirement for fonts to remain under this license does not apply to
any document created using the fonts or their derivatives.

DEFINITIONS
"Font Software" refers to the set of files released by the Copyright
Holder(s) under this license and clearly marked as such. This may
include source files, build scripts and documentation.

"Reserved Font Name" refers to any names specified as such after the
copyright statement(s).

"Original Version" refers to the collection of Font Software
components as distributed by the Copyright Holder(s).

"Modified Version" refers to any derivative made by adding to,
deleting, or substituting -- in part or in whole -- any of the
components of the Original Version, by changing formats or by porting
the Font Software to a new environment.

"Author" refers to any designer, engineer, programmer, technical
writer or other person who contributed to the Font Software.

PERMISSION & CONDITIONS
Permission is hereby granted, free of charge, to any person obtaining
a copy of the Font Software, to use, study, copy, merge, embed,
modify, redistribute, and sell modified and unmodified copies of the
Font Software, subject to the following conditions:

1) Neither the Font Software nor any of its individual components, in
Original or Modified Versions, may be sold by itself.

2) Original or Modified Versions of the Font Software may be bundled,
redistributed and/or sold with any software, provided that each copy
contains the above copyright notice and this license. These can be
included either as stand-alone text files, human-readable headers or
in the appropriate machine-readable metadata fields within text or
binary files as long as those fields can be easily viewed by the user.

3) No Modified Version of the Font Software may use the Reserved Font
Name(s) unless explicit written permission is granted by the
corresponding Copyright Holder. This restriction only applies to the
primary font name as presented to the users.

4) The name(s) of the Copyright Holder(s) or the Author(s) of the Font
Software shall not be used to promote, endorse or advertise any
Modified Version, except to acknowledge the contribution(s) of the
Copyright Holder(s) and the Author(s) or with their explicit written
permission.

5) The Font Software, modified or unmodified, in part or in whole,
must be distributed entirely under this license, and must not be
distributed under any other license. The requirement for fonts to
remain under this license does not apply to any document created using
the Font Software.

TERMINATION
This license becomes null and void if any of the above conditions are
not met.

DISCLAIMER
THE FONT SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO ANY WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT
OF COPYRIGHT, PATENT, TRADEMARK, OR OTHER RIGHT. IN NO EVENT SHALL THE
COPYRIGHT HOLDER BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
INCLUDING ANY GENERAL, SPECIAL, INDIRECT, INCIDENTAL, OR CONSEQUENTIAL
DAMAGES, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF THE USE OR INABILITY TO USE THE FONT SOFTWARE OR FROM
OTHER DEALINGS IN THE FONT SOFTWARE."""


def run(*cmd):
    r = subprocess.run(cmd, cwd=CORE, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"{' '.join(cmd[:3])} failed:\n{r.stderr.strip()}")
    return r.stdout


def shipped(target):
    """(name, version) of every crate linked into the binary for one target."""
    out = run("cargo", "tree", "--locked", "--target", target,
              "-e", "normal,no-proc-macro", "--prefix", "none", "--format", "{p}")
    crates = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].startswith("v"):
            crates.add((parts[0], parts[1][1:]))
    return crates


def tidy(text):
    lines = [l.rstrip() for l in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(lines).strip("\n")


def licence_files(directory):
    found = sorted(p for p in directory.iterdir()
                   if p.is_file() and LICENCE_FILE.match(p.name))
    # A REUSE-style crate keeps one file per licence in a LICENSES/ directory.
    reuse = directory / "LICENSES"
    if reuse.is_dir():
        found += sorted(p for p in reuse.iterdir() if p.is_file())
    return found


def holder(authors):
    names = [re.sub(r"\s*<[^>]*>", "", a).strip() for a in authors]
    return ", ".join(n for n in names if n) or "the authors"


# Ruffle's LICENSE.md, and h263-rs's in the same style, go on after their own
# licence to list every library in their whole dependency tree with its licence:
# a hundred kilobytes, most of it crates amber never builds. The ones it does
# build are in section 5 with their own texts, so the list is cut here.
THIRD_PARTY_LIST = re.compile(r"^#+\s*Third-Party Libraries\s*$", re.M)

APACHE_START = re.compile(r"Apache License\s+Version 2\.0, January 2004")
APACHE_END = "END OF TERMS AND CONDITIONS"


def own_licence(name, text):
    m = THIRD_PARTY_LIST.search(text)
    if not m:
        return text
    return (text[:m.start()].rstrip() + f"\n\n[The rest of this {name} lists every library in"
            " that project's own\ndependency tree, with its licence. It's left out here: the"
            " ones linked into\namber are in section 5.]")


def split_apache(text):
    """(terms, notices) for a plain copy of the Apache-2.0 licence, else None.

    Crates ship the same licence with different wrapping, with or without the
    appendix, and now and then with the appendix's copyright line filled in. One
    copy of the terms covers them all; a filled-in copyright line is a notice in
    its own right and is kept with the crate. Anything else around the terms
    means the file is not a plain copy, and it is printed whole.
    """
    m = APACHE_START.search(text)
    end = text.find(APACHE_END)
    if not m or end < m.start() or text[:m.start()].strip():
        return None
    terms = text[m.start():end + len(APACHE_END)]
    tail = text[end + len(APACHE_END):]
    notices = [l.strip() for l in tail.splitlines()
               if re.search(r"\bCopyright\b", l) and not re.search(r"[\[{]yyyy[\]}]", l)]
    rest = " ".join(l for l in tail.splitlines() if l.strip() not in notices).split()
    rest = " ".join(rest)
    if rest and not (rest.startswith("APPENDIX: How to apply the Apache License")
                     and rest.endswith("limitations under the License.") and len(rest) < 2000):
        return None
    return terms, notices


def apache_key(terms):
    return " ".join(terms.replace("https://", "http://").split())


def texts_for(pkg):
    """[(label, text)] for one package, and whether it fell back to a standard text."""
    texts, fell_back = _texts_for(pkg)
    if " OR " in (pkg.get("license") or ""):
        # self_cell is Apache-2.0 OR GPL-2.0-only and ships both texts. amber
        # takes it under Apache-2.0, and printing the GPL beside it would only
        # suggest otherwise.
        texts = [(n, t) for n, t in texts
                 if t is None or "GNU GENERAL PUBLIC LICENSE" not in t[:400]]
    return texts, fell_back


def _texts_for(pkg):
    manifest_dir = Path(pkg["manifest_path"]).parent
    found = licence_files(manifest_dir)
    if pkg.get("license_file"):
        extra = (manifest_dir / pkg["license_file"]).resolve()
        if extra.is_file() and extra not in found:
            found.append(extra)
    # A git dependency's crate usually sits in a workspace whose licence lives at
    # the root of the checkout, a few directories up.
    if not found and (pkg.get("source") or "").startswith("git+"):
        d = manifest_dir
        while not found and d.parent.parent.name != "checkouts" and d != d.parent:
            d = d.parent
            found = licence_files(d)
    if found:
        return [(p.name, own_licence(p.name, tidy(p.read_text(encoding="utf-8", errors="replace"))))
                for p in found], False

    expr = pkg.get("license") or ""
    ids = set(re.findall(r"[A-Za-z0-9.+-]+", expr))
    if "MIT" in ids:
        return [("MIT (standard text)", MIT_TEXT.format(holder=holder(pkg["authors"])))], True
    if "Apache-2.0" in ids:
        return [("Apache-2.0 (standard text)", None)], True
    if "CC0-1.0" in ids:
        return [("CC0-1.0", CC0_TEXT)], True
    sys.exit(f"{pkg['name']} {pkg['version']}: no licence file and no fallback for {expr!r}")


def source_of(pkg):
    src = pkg.get("source") or ""
    if src.startswith("registry+"):
        return "crates.io"
    if src.startswith("git+"):
        url, _, rev = src[4:].partition("#")
        return f"{url.split('?')[0]} @ {rev[:7]}"
    return "local"


def section(n, title):
    return f"\n{RULE}\n{n}. {title}\n{RULE}\n"


def block(text):
    return f"{THIN}\n{text}\n{THIN}\n"


def render():
    mac = set().union(*(shipped(t) for t in MAC))
    win = set().union(*(shipped(t) for t in WIN))
    meta = json.loads(run("cargo", "metadata", "--locked", "--format-version", "1"))
    packages = {(p["name"], p["version"]): p for p in meta["packages"]}
    ours = {(p["name"], p["version"]) for p in meta["packages"]
            if p["id"] in meta["workspace_members"]}

    ruffle, others = [], []
    for key in sorted((mac | win) - ours):
        pkg = packages.get(key)
        if pkg is None:
            sys.exit(f"{key[0]} {key[1]} is in the tree but not in cargo metadata")
        (ruffle if RUFFLE_REPO in (pkg.get("source") or "") else others).append(pkg)

    rev = {source_of(p) for p in ruffle}
    if len(rev) != 1:
        sys.exit(f"expected one Ruffle revision, found {sorted(rev)}")
    ruffle_texts, _ = texts_for(ruffle[0])

    def where(pkg):
        key = (pkg["name"], pkg["version"])
        if key in mac and key in win:
            return ""
        return " (macOS only)" if key in mac else " (Windows only)"

    # Group the texts, so the MIT text a hundred crates share in different
    # wrappings, and the Apache-2.0 terms, each appear once rather than once per
    # crate. Whitespace never decides a group; a word or a copyright line does.
    groups, fallback = {}, set()
    pending_apache = []

    def add(key, text, label):
        group = groups.setdefault(key, {"text": text, "users": []})
        if label not in group["users"]:
            group["users"].append(label)

    for pkg in others:
        label = f"{pkg['name']} {pkg['version']}"
        texts, fell_back = texts_for(pkg)
        if fell_back:
            fallback.add(label)
        for name, text in texts:
            if text is None:
                pending_apache.append(label)
                continue
            apache = split_apache(text)
            if apache:
                terms, notices = apache
                add(("apache", apache_key(terms)), terms,
                    label + "".join(f" ({n})" for n in notices))
            else:
                add(("text", " ".join(text.split())), text, label)
    if pending_apache:
        apaches = [k for k in groups if k[0] == "apache"]
        if not apaches:
            sys.exit("no crate ships the Apache-2.0 terms to stand in for a missing file")
        commonest = max(apaches, key=lambda k: len(groups[k]["users"]))
        for label in pending_apache:
            add(commonest, None, label)

    ffgl = tidy((FFGL / "LICENSE.md").read_text(encoding="utf-8"))
    glew_dir = sorted(FFGL.glob("deps/glew-*"))
    if not glew_dir:
        sys.exit("external/ffgl is not checked out: git submodule update --init")
    glew = tidy((glew_dir[-1] / "LICENSE.txt").read_text(encoding="utf-8"))

    out = []
    out.append(f"""\
THIRD-PARTY NOTICES: amber

amber's own code is MIT licensed; see LICENSE. The FFGL plugin (Amber.bundle on
macOS, Amber.dll on Windows) also contains other people's work, compiled in, and
this file carries their copyright notices and licence texts. The converter
(tools/amber) contains none of it: it runs your own ffmpeg and your own build of
Ruffle's exporter as separate programs, and ships no part of either.

Generated by scripts/third-party-notices.py from source/amber_core/Cargo.lock
and the external/ffgl submodule. Edit those and re-run it, not this file.

  1. Ruffle
  2. Noto Sans and Noto Sans Math, Ruffle's fallback device font
  3. Resolume FFGL SDK
  4. GLEW (Windows build only)
  5. Other Rust crates linked into the plugin
""")

    out.append(section(1, "Ruffle"))
    out.append(f"""\
https://ruffle.rs  ({next(iter(rev))})
Licence: MIT OR Apache-2.0, at your option. Both texts follow, as Ruffle ships them.

The plugin embeds Ruffle to play .swf files live. These crates from the Ruffle
repository are linked in:

""")
    for pkg in ruffle:
        out.append(f"  {pkg['name']} {pkg['version']}{where(pkg)}\n")
    out.append("\n")
    for name, text in ruffle_texts:
        out.append(f"{name}:\n{block(text)}")

    out.append(section(2, "Noto Sans and Noto Sans Math, Ruffle's fallback device font"))
    out.append("""\
https://github.com/notofonts/latin-greek-cyrillic
https://github.com/notofonts/math
Licence: SIL Open Font License 1.1

Ruffle's default_font feature, which amber enables, compiles a subset of Noto
Sans, merged with the Mathematical Operators block of Noto Sans Math, into the
plugin as the font Flash content gets when it asks for a device font.

""")
    out.append(block(NOTO_OFL))

    out.append(section(3, "Resolume FFGL SDK"))
    out.append("""\
https://github.com/resolume/ffgl  (submodule external/ffgl)
Licence: BSD-3-Clause

The FreeFrame GL plugin interface, compiled into both the macOS and the Windows
plugin.

""")
    out.append(block(ffgl))

    out.append(section(4, "GLEW (Windows build only)"))
    out.append("""\
https://github.com/nigels-com/glew
Licence: BSD-3-Clause, with the Mesa 3-D (MIT) and Khronos (MIT) components it
includes.

The OpenGL extension loader, statically linked into Amber.dll from vcpkg. The
macOS bundle uses the system OpenGL framework and contains no GLEW.

""")
    out.append(block(glew))

    out.append(section(5, "Other Rust crates linked into the plugin"))
    out.append("""\
Ruffle's own dependencies, wgpu (the graphics layer it renders through) and
everything below them. A crate marked * ships no licence file in its package,
so the standard text of the licence its Cargo.toml declares is given instead,
with its authors as the copyright holder.

""")
    width = max(len(f"{p['name']} {p['version']}") for p in others) + 2
    for pkg in others:
        label = f"{pkg['name']} {pkg['version']}"
        mark = "*" if label in fallback else " "
        out.append(f" {mark}{label:<{width}}{pkg.get('license') or '?'}"
                   f"  [{source_of(pkg)}]{where(pkg)}\n")
    out.append("""
The licence texts, each after the crates it covers. The Apache-2.0 terms are
given once for every crate that ships a plain copy of them, with any copyright
line a crate filled into the licence's appendix kept beside its name.

""")
    for group in sorted(groups.values(), key=lambda g: g["users"][0]):
        out.append(f"Used by: {', '.join(group['users'])}\n{block(group['text'])}\n")

    return "".join(out).rstrip("\n") + "\n"


def main():
    text = render()
    if "--check" in sys.argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            print("THIRD-PARTY-NOTICES.txt is out of date: run scripts/third-party-notices.py")
            return 1
        print("THIRD-PARTY-NOTICES.txt is current")
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(text) // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
