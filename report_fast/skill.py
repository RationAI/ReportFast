"""The skill that ships inside the wheel, found and placed.

An installed library cannot install its own documentation. Packaging forbids a
build or install hook from writing outside the environment — correctly, since the
alternative is a package that edits your home directory during `uv add`. So the
skill travels *inside* the package and one explicit command moves it where an
agent looks. Nothing here happens unless someone runs that command.

Two layouts, one answer. A wheel install carries the skill at
`report_fast/skill/` (a build-time projection of `skills/reportfast/`, see
`pyproject.toml`'s force-include); an editable install has no such copy, so the
checkout two directories up is checked too. An agent in either case is told the
same thing: `reportfast skill show`.

Why the file lives in one place: the skill and the gate must not drift, which is
the same argument `contract.py` makes about the schema. Copying a second `SKILL.md`
into the package by hand would put back the two sources of truth that `derive_schema.py
--check` exists to prevent.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Dict, List, Optional, Union

#: Directory name the skill is known by, which is also the name Claude Code
#: resolves when an agent asks for `/reportfast`.
SKILL_NAME = "reportfast"
SKILL_FILE = "SKILL.md"

#: Where a personal install goes: read by every project on this machine.
PERSONAL_DIR = Path.home() / ".claude" / "skills"
#: Where a project install goes: read by that project alone, and committable, so
#: `uv add report-fast` plus one command configures a fresh clone.
PROJECT_DIR = Path(".claude") / "skills"


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
            which says how to rebuild rather than falling back to a copy it does
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

    The gate reads `report_fast/schema/`; this is how the *advice* and the example
    sessions reach a reader who has an install and no checkout. Both spellings of
    an example work — `examples/dysplasia_case.json` as `SKILL.md` writes it, and
    the bare filename — because a name an agent has just read in a document should
    work as typed.

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


def target_for(
    dest: Optional[Union[str, Path]], *, project: bool = False
) -> Path:
    """Where `install` would put it: `--dest`, else the project, else personal.

    Personal is the default because the question an agent asks — "how do I build a
    report of these slides?" — is rarely about one repository, and because a
    project install adds a directory that has to be gitignored or committed, which
    is a decision about that repository, not about this package.
    """
    if dest is not None:
        return Path(dest) / SKILL_NAME
    base = PROJECT_DIR if project else PERSONAL_DIR
    return base / SKILL_NAME


def find_installed(dest: Optional[Union[str, Path]] = None, *, project: bool = False) -> List[Path]:
    """Every copy of the skill an agent would currently be shown.

    Both scopes are checked when no destination is given, because "is it
    installed?" is asked after an agent failed to use it, and a stale copy in the
    other scope is the usual answer.
    """
    if dest is not None or project:
        candidates = [target_for(dest, project=True)]
    else:
        candidates = [target_for(None), target_for(None, project=True)]
    return [path for path in candidates if (path / SKILL_FILE).is_file()]


def install(
    dest: Optional[Union[str, Path]] = None,
    *,
    project: bool = False,
    force: bool = False,
    link: bool = False,
) -> Path:
    """Put the bundled skill where Claude Code looks for it. Returns the path.

    Args:
        dest: Parent directory; defaults per :func:`target_for`.
        project: Install into `./.claude/skills/` for this project only.
        force: Replace an existing copy. Without it an existing install is an
            error, because overwriting a skill silently is how a hand-edited
            procedure disappears.
        link: Symlink to the bundled files instead of copying — what you want in
            this checkout, where edits to `skills/reportfast/` should be live
            without reinstalling.

    Raises:
        SkillError: the bundle is missing, or something is already installed and
            `force` was not given.
    """
    source = skill_dir()
    target = target_for(dest, project=project)
    if target.exists() and not force:
        raise SkillError(
            f"{target} already holds a skill. --force replaces it; check first if "
            "anyone edited it, since this command overwrites what is there."
        )
    if target.is_symlink() or target.is_dir():
        shutil.rmtree(target, ignore_errors=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    if link:
        target.symlink_to(source, target_is_directory=True)
    else:
        shutil.copytree(source, target, symlinks=True)
    return target


__all__ = [
    "SKILL_FILE",
    "SKILL_NAME",
    "PERSONAL_DIR",
    "PROJECT_DIR",
    "SkillError",
    "bundled_roots",
    "examples_dir",
    "find_installed",
    "install",
    "reference",
    "skill_dir",
    "skill_path",
    "skill_text",
    "target_for",
]
