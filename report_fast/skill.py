"""The skill that ships inside the wheel, and how a reader reaches it.

Two ways to read the same procedure, neither of them an install. In a checkout,
`skills/reportfast/SKILL.md` is an ordinary file. With the library installed,
`reportfast skill show` prints the file that travelled inside the wheel. Nothing
here writes anything anywhere.

There used to be a third way — `reportfast skill install`, which copied the bundle
into `~/.claude/skills` on request. It was deleted because Claude Code already has
ways to take a skill: a project commits its `skills/` directory, or the skill is
installed as a plugin. A library writing into a home directory is neither, and
opt-in does not make it the official mechanism. What the library does have to do is
carry its own documentation, because a procedure that ships separately from the code
it describes drifts from it — hence the bundling below, and `skill show`.

Why the file lives in one place: the procedure the agent follows and the code it
describes must not drift. Copying a second `SKILL.md` into the package by hand would
restore exactly the two-sources-of-truth problem the derived schema was deleted for --
two copies of one set of facts, and no check that keeps them together.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

#: Directory name the skill is known by, which is also the name Claude Code
#: resolves when an agent asks for `/reportfast`.
SKILL_NAME = "reportfast"
SKILL_FILE = "SKILL.md"


class SkillError(RuntimeError):
    """The skill is not where an installed package should have it.

    Raised rather than printed: `reportfast skill show` failing quietly would tell
    an agent there is no procedure to follow, which is worse than the procedure
    being stale.
    """


def skill_dir() -> Path:
    """The bundled skill, from a wheel install or an editable checkout.

    Raises:
        SkillError: neither layout has it — a package built without the skill,
            which says to rebuild rather than falling back to a copy it does
            not have.
    """
    here = Path(__file__).resolve().parent
    candidates = (
        here / "skill",                       # wheel: force-include projection
        here.parent / "skills" / SKILL_NAME,  # editable: this repo's checkout
    )
    for candidate in candidates:
        if (candidate / SKILL_FILE).is_file():
            return candidate
    raise SkillError(
        f"no {SKILL_FILE} in this install (looked in "
        + ", ".join(str(path) for path in candidates)
        + "). Build it: `uv build` includes skills/reportfast/ in the wheel."
    )


def skill_path() -> Path:
    """The bundled `SKILL.md` itself."""
    return skill_dir() / SKILL_FILE


def skill_text() -> str:
    """The procedure an agent should read, as text — what `skill show` prints."""
    return skill_path().read_text(encoding="utf-8")


def examples_dir() -> Path:
    """The golden sessions the skill tells an agent to imitate.

    Bundled because a procedure that says "copy an example" has to arrive with the
    example — otherwise the instruction is a pointer into a checkout that an
    installed library does not have, which is the failure this module exists to
    remove. Its own directory rather than a folder inside the skill because in a
    checkout `examples/` is a sibling of `skills/`, and nesting would make the two
    layouts answer different paths for the same file.
    """
    here = Path(__file__).resolve().parent
    candidates = (here / "examples", here.parent / "examples")
    for candidate in candidates:
        if candidate.is_dir() and any(candidate.glob("*.json")):
            return candidate
    raise SkillError(
        "no example sessions in this install (looked in "
        + ", ".join(str(path) for path in candidates)
        + ")"
    )


def _inside(root: Path, name: str) -> Path:
    """`root / name`, refusing anything that escapes it."""
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise SkillError(f"{name!r} must be a path inside the bundle, not an absolute path or a .. escape")
    return root / relative


def bundled_roots() -> Dict[str, Optional[Path]]:
    """The directories `reference(name)` searches, by the prefix each answers to.

    `""` is the skill itself — the names `SKILL.md` prints for its references.
    `"examples"` is the golden sessions. A prefix maps to exactly one root rather
    than a name being tried against all of them, so `examples/SKILL.md` cannot
    quietly resolve to the skill's own `SKILL.md`. An absent examples directory is
    not an error here: the skill is the required half, examples are the bonus.
    """
    roots: Dict[str, Optional[Path]] = {"": skill_dir()}
    try:
        roots["examples"] = examples_dir()
    except SkillError:
        roots["examples"] = None
    return roots


def reference(name: str) -> str:
    """One bundled file by relative name: `references/xopat-v3.md`, `examples/x.json`.

    This is how the *advice* and the example sessions reach a reader who has an
    install and no checkout, without anyone copying a file into place first. Both
    spellings of an example work — `examples/dysplasia_case.json` as `SKILL.md`
    writes it, and the bare filename — because a name an agent has just read in a
    document should work as typed.

    Raises:
        SkillError: the name escapes the bundle, or nothing in it has it. The
            refusal lists what is there, since the caller is usually guessing.
    """
    roots = bundled_roots()
    wanted = Path(name)
    first = wanted.parts[0] if wanted.parts else ""
    if first in roots:
        # Prefixed: look under that root and nowhere else.
        target = roots[first]
        if target is None:
            raise SkillError(f"this install bundles no {first}/ directory")
        target = _inside(target, str(Path(*wanted.parts[1:])))
        if target.is_file():
            return target.read_text(encoding="utf-8")
    else:
        # Bare name: the skill's files, then the examples.
        for root in (roots[""], roots["examples"]):
            if root is None:
                continue
            target = _inside(root, name)
            if target.is_file():
                return target.read_text(encoding="utf-8")
    listed = sorted(
        str(item.relative_to(root))
        for root in bundled_roots().values()
        if root is not None
        for item in root.rglob("*")
        if item.is_file()
    )
    raise SkillError(f"the bundle has no {name}. It has: {', '.join(listed)}")


__all__ = [
    "SKILL_FILE",
    "SKILL_NAME",
    "SkillError",
    "bundled_roots",
    "examples_dir",
    "reference",
    "skill_dir",
    "skill_path",
    "skill_text",
]
