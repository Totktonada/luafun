#!/usr/bin/env python3
"""Split the flat generated `fun` module page into per-category pages.

`emmylua_doc_cli` documents all exported functions on a single
`modules/fun.md` page.  The desired documentation groups the functions
the same way the library does (Basic Functions, Generators, Slicing, ...),
which is expressed by `---@category` tags in `fun.lua`.

Usage: split_api.py <sphinx_src> <fun.lua>
"""

import os
import re
import sys

# slug -> page title, in the order in which the pages should appear
CATEGORIES = [
    ("basic", "Basic Functions"),
    ("generators", "Generators"),
    ("slicing", "Slicing"),
    ("indexing", "Indexing"),
    ("filtering", "Filtering"),
    ("reducing", "Reducing"),
    ("transformations", "Transformations"),
    ("compositions", "Compositions"),
    ("operators", "Operators"),
    ("internal", "Internal"),
]
TITLES = {title for _, title in CATEGORIES}

# slug -> introductory text inserted right after the page title
INTROS = {
    "basic": (
        "The section contains functions to create iterators from Lua objects."
    ),
    "generators": (
        "This section contains a number of useful generators modeled after\n"
        "Standard ML, Haskell, Python, Ruby, JavaScript and other languages."
    ),
    "slicing": (
        "This section contains functions to make subsequences from iterators."
    ),
    "indexing": (
        "This section contains functions to find elements by its values."
    ),
    "filtering": (
        "This section contains functions to filter values during iteration."
    ),
    "reducing": (
        "The section contains functions to analyze iteration values and\n"
        "recombine through use of a given combining operation the results of\n"
        "recursively processing its constituent parts, building up a return\n"
        "value\n"
        "\n"
        "```{note}\n"
        "An attempt to use infinity iterators with the most function from the\n"
        "module causes an infinite loop.\n"
        "```"
    ),
    "operators": (
        "This auxiliary module exports a set of Lua operators as intrinsic\n"
        "functions to use with the library high-order primitives."
    ),
}

# slug -> ordered (sub-heading, [function names]) groups.  The page lists the
# functions in this order, a sub-heading is written before the first function
# of its group and the function headings are demoted to level 3 so that they
# nest under it.
SECTIONS = {
    "generators": [
        ("Finite Generators", ["range"]),
        ("Infinity Generators",
         ["duplicate", "replicate", "xrepeat", "tabulate", "zeros", "ones"]),
        ("Random sampling", ["rands"]),
    ],
    "slicing": [
        ("Basic", ["nth", "head", "car", "tail", "cdr"]),
        ("Subsequences",
         ["take_n", "take_while", "take", "drop_n", "drop_while", "drop",
          "span", "split", "split_at"]),
    ],
    "reducing": [
        ("Folds", ["foldl", "reduce", "length", "totable", "tomap"]),
        ("Predicates",
         ["is_prefix_of", "is_null", "all", "every", "any", "some"]),
        ("Special folds",
         ["sum", "product", "min", "minimum", "min_by", "minimum_by",
          "max", "maximum", "max_by", "maximum_by"]),
    ],
}


def sort_functions(slug, sections, exported):
    """Put the functions of a page in the order of the old documentation.

    emmylua_doc_cli lists them alphabetically. A page with sub-headings takes
    the order of SECTIONS, any other page the order in which fun.lua exports
    the functions (`exported`: name -> position).
    """
    listed = [name for _, names in SECTIONS.get(slug, []) for name in names]

    def position(section):
        name = re.match(r"## fun\.([A-Za-z_]\w*)", section).group(1)
        if name in listed:
            return (0, listed.index(name))
        return (1, exported.get(name, len(exported)))

    return sorted(sections, key=position)


def add_subheadings(slug, sections):
    """Insert the sub-headings of a page and demote its function headings."""
    groups = SECTIONS.get(slug)
    if not groups:
        return sections
    heading_of = {}
    for heading, names in groups:
        for name in names:
            heading_of[name] = heading
    out = []
    seen = set()
    for section in sections:
        m = re.match(r"## fun\.([A-Za-z_]\w*)", section)
        name = m.group(1) if m else None
        heading = heading_of.get(name)
        if heading and heading not in seen:
            seen.add(heading)
            out.append("## %s" % heading)
        # demote "## fun.x" to "### fun.x" so that it nests under the heading
        out.append("#" + section)
    return out


# The labels emmylua_doc_cli writes above the generated parts of an entry.
GENERATED_LABELS = ("**Overloads**", "**Parameters**", "**Returns**")
# The whole own text of an alias's entry (`exports.xrepeat = duplicate`).
ALIAS_TEXT = re.compile(r"An alias for \[[^\]]+\]\([^)]*\)\.")


def compact_alias(section):
    """Shorten the entry of an alias to the form of the old documentation.

    For `exports.xrepeat = duplicate` emmylua_doc_cli repeats the entry of
    duplicate -- its typed signature, overloads, parameters and returns --
    above the alias's own text. The old documentation gave the parameter
    names and that text only, and so does the result:

        ## fun.xrepeat
        ---
        ```lua
        function fun.xrepeat(...)
        ```

        An alias for [duplicate](generators.md#funduplicate).

    An entry with any other text is returned as it is.
    """
    lines = section.split("\n")
    signature = None  # the first code block: the entry's own signature
    block = None  # the lines of the code block being read
    text = []  # the lines outside the code blocks, the lists and the labels
    for line in lines[1:]:
        if line.startswith("```"):
            if block is None:  # an opening fence
                block = []
            else:  # a closing fence
                if signature is None:
                    signature = "\n".join(block)
                block = None
        elif block is not None:
            block.append(line)
        elif (line.strip() and line != "---" and not line.startswith("- ")
              and line not in GENERATED_LABELS):
            text.append(line.strip())
    if len(text) != 1 or not ALIAS_TEXT.fullmatch(text[0]):
        return section
    out = [lines[0], "---"]
    call = signature and short_signature(signature)
    if call:
        out += ["```lua", "function " + call, "```", ""]
    out.append(text[0])
    return "\n".join(out)


def short_signature(signature):
    """`function fun.f(a: T, ...: U)-> R` -> `fun.f(a, ...)`.

    None for a code block that is not a function's signature (fun.op shows
    the type of a table).
    """
    m = re.match(r"function\s+([\w.:]+)\(", signature)
    if not m:
        return None
    params = signature[m.end():]
    names = []
    start = 0  # where the current parameter begins in params
    depth = 0  # the brackets open inside a parameter's type
    for t in re.finditer(r"->|[()\[\]{}<>,]", params):
        token = t.group(0)
        if token == "->":  # the arrow of a function type, not a bracket
            continue
        if token in ("(", "[", "{", "<"):
            depth += 1
        elif token == ")" and depth == 0:  # the end of the parameter list
            names.append(params[start:t.start()])
            break
        elif token in (")", "]", "}", ">"):
            depth -= 1
        elif depth == 0:  # a comma between two parameters
            names.append(params[start:t.start()])
            start = t.end()
    names = [name.split(":")[0].strip() for name in names if name.strip()]
    return "%s(%s)" % (m.group(1), ", ".join(names))


def sort_members(page, fields):
    """Put the member entries of a type page in the order of `fields`.

    emmylua_doc_cli lists the members of a class alphabetically, in groups
    ("## methods", "## fields") of "### <Class>.<member>" entries. Each group
    is sorted by the position of the member in `fields`.
    """
    if not fields:
        return page

    def position(entry):
        name = re.match(r"### [\w.]*\.(\w+)", entry).group(1)
        return fields.index(name) if name in fields else len(fields)

    out = []
    entries = []  # the entries of the group being read
    for piece in re.split(r"(?m)^(?=#{1,3} )", page):
        if piece.startswith("### "):
            entries.append(piece.rstrip())
        else:
            out += sorted(entries, key=position)
            entries = []
            out.append(piece.rstrip())
    out += sorted(entries, key=position)
    return "\n\n".join(piece for piece in out if piece) + "\n"


# Type pages whose member entries show only their signature and text. The
# operators are `---@field`s without parameter or return texts, and
# emmylua_doc_cli fills the entry of such a field with the overloads,
# parameters and returns of a documented function of the same name in
# fun.lua: the `length` operator gets those of the reducer fun.length.
OWN_DOCS_ONLY = ("fun.OperatorTable",)


def drop_generated_parts(page):
    """Remove the Overloads, Parameters and Returns parts of the entries."""
    out = []
    skipping = False  # in a generated part: its label, list or code block
    in_code = False  # in a code block of a generated part
    for line in page.split("\n"):
        if in_code:  # up to the closing fence
            in_code = not line.startswith("```")
        elif line in GENERATED_LABELS:
            skipping = True
        elif skipping and line.startswith("```"):
            in_code = True
        elif skipping and line.strip() and not line.startswith("- "):
            skipping = False  # the text of the entry
        if not skipping:
            out.append(line)
    return "\n".join(out)


def parse_categories(path):
    """Return a function name -> category resolver built from `fun.lua`."""
    name_cat = {}
    alias = {}
    pending = None
    decl = re.compile(
        r"^local\s+([A-Za-z_]\w*)\s*=\s*(?:function|\{)"
        r"|^local\s+function\s+([A-Za-z_]\w*)\("
    )
    exports = re.compile(r"^exports\.([A-Za-z_]\w*)\s*=\s*(?:exports\.)?([A-Za-z_]\w*)\s*$")
    methods = re.compile(r"^methods\.([A-Za-z_]\w*)\s*=\s*(?:methods\.)?([A-Za-z_]\w*)\s*$")

    for line in open(path).read().split("\n"):
        m = re.match(r"^\s*---@category\s+(.+?)\s*$", line)
        if m:
            pending = m.group(1)
            continue
        m = decl.match(line)
        if m:
            if pending:
                name_cat[m.group(1) or m.group(2)] = pending
                pending = None
            continue
        m = exports.match(line)
        if m:
            alias[m.group(1)] = m.group(2)
            pending = None
            continue
        m = methods.match(line)
        if m:
            alias.setdefault(m.group(1), m.group(2))

    def resolve(name, seen=None):
        seen = seen or set()
        if name in name_cat:
            return name_cat[name]
        if name in alias and name not in seen:
            seen.add(name)
            return resolve(alias[name], seen)
        return None

    return resolve


def parse_exports(path):
    """Return name -> position of each `exports.<name> = ...` in `fun.lua`."""
    exported = {}
    for name in re.findall(r"(?m)^exports\.([A-Za-z_]\w*)\s*=", open(path).read()):
        exported.setdefault(name, len(exported))
    return exported


def parse_fields(path):
    """Return class name -> its member names, in the order of the `---@field`
    lines of its `---@class` comment in `fun.lua`."""
    fields = {}
    current = None  # the member names of the class whose comment is read
    for line in open(path).read().split("\n"):
        m = re.match(r"^---@class\s+([\w.]+)", line)
        if m:
            current = fields.setdefault(m.group(1), [])
        elif current is not None and line.startswith("---"):
            m = re.match(r"^---@field\s+([A-Za-z_]\w*)", line)
            if m:
                current.append(m.group(1))
        else:
            current = None
    return fields


def main():
    sphinx_src, fun_lua = sys.argv[1], sys.argv[2]
    resolve = parse_categories(fun_lua)
    exported = parse_exports(fun_lua)

    modules_dir = os.path.join(sphinx_src, "modules")
    page = os.path.join(modules_dir, "fun.md")
    text = open(page).read()

    sections = [s.rstrip("\n") for s in re.split(r"(?m)^(?=### )", text) if s.startswith("### ")]

    groups = {title: [] for _, title in CATEGORIES}
    other = []
    for section in sections:
        m = re.match(r"###\s+(?:fun|M)\.([A-Za-z_]\w*)", section)
        if not m:
            continue
        # drop tag lines rendered by the markdown templates
        section = "\n".join(
            line for line in section.split("\n") if not re.match(r"^\s*@category\s", line)
        )
        # a trailing "## fields" block (operator/op) belongs to the next
        # section; cut it off here
        lines = section.split("\n")
        kept = [lines[0]]
        for line in lines[1:]:
            if line.startswith("## "):
                break
            kept.append(line)
        section = "\n".join(kept).rstrip("\n")
        # demote "### fun.x" to "## fun.x" so that the page title (H1) is
        # followed by H2 headings (avoid MyST "non-consecutive header").
        # Accept the old hardcoded "M." prefix as well and normalize it so
        # that the links inside the annotations (#funx) keep working.
        section = re.sub(r"^###\s+(?:fun|M)\.", "## fun.", section, count=1)
        section = compact_alias(section)
        title = resolve(m.group(1))
        if title in TITLES:
            groups[title].append(section)
        else:
            other.append(section)

    # the pages live next to the other chapters (index.md, intro.md, ...),
    # so that their URLs match the old documentation (e.g. /filtering.html)
    for slug, title in CATEGORIES:
        if not groups[title]:
            continue
        with open(os.path.join(sphinx_src, slug + ".md"), "w") as f:
            f.write("# %s\n\n" % title)
            if slug in INTROS:
                f.write(INTROS[slug] + "\n\n")
            sections = sort_functions(slug, groups[title], exported)
            f.write("\n\n".join(add_subheadings(slug, sections)))
            f.write("\n")
    if other:
        with open(os.path.join(sphinx_src, "other.md"), "w") as f:
            f.write("# Other\n\n")
            f.write("\n\n".join(other))
            f.write("\n")
        print("warning: %d function(s) without a category -> other.md" % len(other))

    # remove tag lines rendered on the remaining type pages and put their
    # members in the order of fun.lua
    fields = parse_fields(fun_lua)
    types_dir = os.path.join(sphinx_src, "types")
    for root, _dirs, files in os.walk(types_dir):
        for name in files:
            if not name.endswith(".md"):
                continue
            path = os.path.join(root, name)
            with open(path) as f:
                content = f.read()
            cleaned = "\n".join(
                line
                for line in content.split("\n")
                if not re.match(r"^\s*@category\s", line)
            )
            class_name = os.path.splitext(name)[0]
            cleaned = sort_members(cleaned, fields.get(class_name))
            if class_name in OWN_DOCS_ONLY:
                cleaned = drop_generated_parts(cleaned)
            if cleaned != content:
                with open(path, "w") as f:
                    f.write(cleaned)

    # the flat pages are replaced by the per-category ones
    for flat in (page, os.path.join(sphinx_src, "types", "fun.md")):
        if os.path.exists(flat):
            os.remove(flat)


if __name__ == "__main__":
    main()
