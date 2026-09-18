"""Tests for `report_fast.contract` -- the generated xOpat contract.

Run:      cd /home/jovyan/report_fast && python tests/test_contract.py
Pytest:   pytest tests/test_contract.py

The contract is the thing every other gate in this package trusts, so these
tests are mostly about the trust holding:

  * the artifacts are present, complete, and re-derivable to the byte (the
    two-source check, run as a test rather than as a habit);
  * the lock names the commit everything was verified against, so a claim in a
    docstring can be checked instead of believed;
  * and the specific facts the derivation was written to catch are still
    catchable -- the four flat `params.ui` aliases that produce JSON the browser
    deletes, and the `icon0..iconN` family a fixed table cannot express.

`--check` compares the parsed viewer against the committed artifacts *and*
against the hand-written tables in `shader.py`/`xopat.py`. That second half is
the point: a generated file checked against a projection of itself proves
nothing, so `PARAM_KEYS` and `SHADER_PARAMS` stay independent statements about
the viewer, and drift in either direction fails here.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _support  # noqa: E402
from report_fast import contract  # noqa: E402
from report_fast.contract import ContractError  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DERIVE = ROOT / "scripts" / "derive_schema.py"
VIEWER = Path(os.environ.get("XOPAT_VIEWER", "/home/jovyan/xopat"))

#: The generation everything in this repo was verified against. DESIGN.md's "xOpat
#: v3 facts" section cites the same commit; if these disagree, one of them moved.
PINNED_COMMIT = "18c94f2b1991380e703e44366552e48faca62cd0"
PINNED_VERSION = "3.1.0"


# ── the artifacts ───────────────────────────────────────────────────────────


def test_every_artifact_is_shipped():
    for name in contract.FILES:
        assert (contract.SCHEMA_DIR / name).is_file(), f"{name} is missing from the wheel"


def test_a_missing_artifact_says_how_to_regenerate_rather_than_falling_back():
    # A baked-in fallback would put back the second source of truth the whole
    # module exists to remove, so the failure has to be loud.
    from unittest import mock

    with mock.patch.object(contract, "SCHEMA_DIR", ROOT / "no-such-schema-dir"):
        # Cleared *before* as well as after: `_load` is cached by file name, so a
        # contract read by any earlier test in the process would answer the patched
        # call from cache and the missing directory would go unnoticed. (Which is
        # exactly what happened once a build started reading the viewer stamp.)
        contract._load.cache_clear()
        contract._params.cache_clear()
        try:
            contract.viewer_stamp()
        except ContractError as error:
            assert "derive_schema.py" in str(error)
        else:  # pragma: no cover
            raise AssertionError("a missing contract was silently tolerated")
        finally:
            contract._load.cache_clear()
            contract._params.cache_clear()


def test_the_lock_stamps_the_pinned_generation():
    stamp = contract.viewer_stamp()
    assert stamp["commit"] == PINNED_COMMIT
    assert str(stamp["version"]) == PINNED_VERSION
    assert stamp["inputs_dirty"] == [], (
        "this contract came from a dirty viewer tree, so no claim in this repo is "
        "reproducible; re-run derive_schema.py from a clean checkout"
    )
    for name, digest in (stamp.get("inputs") or {}).items():
        assert len(str(digest)) >= 16, f"{name} has no usable digest"


def test_the_viewer_checkout_is_where_the_docs_say_it_is():
    # Only meaningful on this pod; CI has no clone and skips. When the directory
    # exists it had better be the right one, because the derivation trusts it
    # blindly and `/home/jovyan/xopat2` is a stale v2 tree that would parse fine.
    if not VIEWER.is_dir():
        return
    assert (VIEWER / "src/types/app.d.ts").is_file()
    assert (VIEWER / "src/libs/flex-renderer/flex-renderer.js").is_file()
    head = subprocess.run(
        ["git", "-C", str(VIEWER), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head == PINNED_COMMIT, (
        f"{VIEWER} is at {head[:8]}, not the pinned generation -- anything derived "
        "from it describes a different viewer than this package claims"
    )


# ── what the params model has to keep saying ────────────────────────────────


def test_the_params_model_separates_the_surviving_aliases_from_the_dead_ones():
    """The finding this derivation existed to produce.

    `getUiOption` (src/application-context.ts) falls back to a flat `params[key]`,
    which reads like every ui key works flat. `sanitizeAgainst` (src/app.ts) runs
    first and strips any top-level key the setup defaults do not carry, so only
    three ever got the chance. Four dead keys lived in PARAM_KEYS for a release
    because the code that *reads* them looked like the evidence.
    """
    surviving = contract.flat_ui_aliases()
    dead = contract.stripped_flat_ui_aliases()
    assert surviving == frozenset({"scaleBar", "statusBar", "toolBar"})
    assert {"appBar", "globalMenu", "mainMenu", "navigator"} <= dead
    assert not surviving & dead
    assert dead < contract.ui_param_keys(), "they are real settings, in the wrong place"
    for key in surviving:
        assert key in contract.accepted_param_keys()
    for key in dead:
        assert key not in contract.accepted_param_keys()


def test_a_typed_key_without_a_default_and_a_defaulted_key_without_a_type_are_both_accepted():
    # accepted = XOpatSetup keys UNION config.json::setup keys, and each half of
    # the union contributes keys the other half does not have.
    accepted = contract.accepted_param_keys()
    assert "branding" in accepted, "typed in XOpatSetup, no default in config.json"
    assert "webGlPrecision" in accepted, "defaulted in config.json, absent from the type"


def test_ui_is_the_only_nested_object_with_a_vocabulary():
    assert contract.param_keys_for("") == contract.accepted_param_keys()
    assert contract.param_keys_for("ui") == contract.ui_param_keys()
    assert contract.param_keys_for("background") is None, (
        "`sanitizeAgainst` recurses only into plain-object defaults; inventing a "
        "vocabulary where the viewer declares none would be inventing errors"
    )


# ── the layer vocabulary a fixed table cannot hold ──────────────────────────


def test_the_generated_families_accept_names_no_table_could_list():
    assert contract.accepts_layer_param("iconmap", "icon0")
    assert contract.accepts_layer_param("iconmap", "icon147")
    assert not contract.accepts_layer_param("iconmap", "iconx")
    assert contract.layer_field_families("iconmap"), (
        "`_expandControlDefinitions` turns an `array:` definition into one control "
        "per class interval -- a hand table listing `iconN` literally matches nothing"
    )


def test_filter_flags_are_accepted_for_every_closed_layer():
    assert contract.accepts_layer_param("heatmap", "use_channel0")
    assert contract.accepts_layer_param("colormap", "use_mask")


def test_both_spellings_of_a_control_are_legal_because_the_viewer_names_both():
    aliases = contract.layer_param_aliases("outer_border") or {}
    for snake, camel in aliases.items():
        assert contract.accepts_layer_param("outer_border", snake)
        assert contract.accepts_layer_param("outer_border", camel)


def test_the_closed_layers_are_closed_and_the_open_ones_are_not_checked():
    # VisualizationShaderLayer declares no index signature, so an undeclared param
    # there is a real finding; BackgroundItem/VisualizationItem declare one on
    # purpose and forward to plugins, so flagging their keys would be noise.
    assert contract.layer_field_names("heatmap")
    assert not contract.accepts_layer_param("heatmap", "totallyMadeUp")
    assert {"dataReferences", "tiledImages"} <= contract.index_fields()


def test_every_registered_layer_type_documents_itself():
    for shader_type in sorted(contract.shader_types()):
        document = contract.layer_documentation(shader_type)
        assert "fields" in document, f"{shader_type} has no field list"

    with _support.raises(ContractError):
        contract.layer_documentation("classify")  # retired in v3


# ── the two-source check, as a test ────────────────────────────────────────


def test_the_committed_contract_still_matches_the_pinned_viewer():
    """`derive_schema.py --check`, in the suite.

    Without the viewer checkout there is nothing to re-parse, and re-deriving from
    a *different* commit would fail for a reason that is not a bug -- so the test
    skips rather than lies. What it catches when it runs: the viewer moved under
    the artifacts, or somebody edited `shader.py`'s tables away from the viewer.

    `unittest.SkipTest`, not `pytest.skip`: this file is runnable standalone, and
    pytest's `Skipped` subclasses BaseException, so it walked straight past the old
    two-line runner and out of the process. The README tells people to run this
    file by hand, and on any machine without a viewer checkout at $XOPAT_VIEWER --
    that is, on every machine but this one -- the documented command ended in a
    traceback.
    """
    if not DERIVE.is_file():
        raise unittest.SkipTest("derive_schema.py is not installed")
    if not VIEWER.is_dir():
        raise unittest.SkipTest(f"no xOpat checkout at {VIEWER}; set $XOPAT_VIEWER")
    result = subprocess.run(
        [sys.executable, str(DERIVE), "--check", "--viewer", str(VIEWER)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_hand_tables_and_the_derived_allowlist_describe_the_same_viewer():
    """The same comparison, runnable without the viewer.

    Weaker than `--check` on purpose -- it cannot tell whether *the viewer*
    changed, only whether the two descriptions of it have diverged -- which is
    exactly what a stale `PARAM_KEYS` edit looks like in CI, and worth catching
    there rather than only on someone's pod.
    """
    from report_fast import xopat
    from report_fast.shader import SHADER_PARAMS, ShaderType

    hand, derived = set(xopat.PARAM_KEYS), set(contract.accepted_param_keys())
    assert hand - derived == set(), (
        f"PARAM_KEYS carries {sorted(hand - derived)}, which the pinned viewer does "
        "not declare -- the browser would drop those and say nothing"
    )
    # The other direction is allowed: a key the viewer added and nobody
    # transcribed still loads (param_keys() is the union), and --check on a pod
    # with the checkout is where that gets transcribed.

    for shader_type in ShaderType:
        if shader_type.value not in contract.shader_types():
            continue  # retired aliases are in the enum for error messages
        declared = set(SHADER_PARAMS[shader_type])
        missing = {key for key in declared if not contract.accepts_layer_param(shader_type.value, key)}
        assert missing == set(), (
            f"{shader_type.value}: SHADER_PARAMS lists {sorted(missing)}, which the "
            "viewer does not declare"
        )


def test_the_contract_reads_as_text_for_an_agent():
    text = contract.dump("params")
    assert "accepted" in json.dumps(json.loads(text))
    assert "viewer" in contract.summary()
    assert contract.summary()["shader_types"] == len(contract.shader_types())
    try:
        contract.dump("nonsense")
    except ContractError as error:
        assert "unknown contract part" in str(error)
    else:  # pragma: no cover
        raise AssertionError("dump() accepted an unknown part")


# ── the skill's copy of these facts ─────────────────────────────────────────
#
# `SKILL.md` carries a version stamp and points at generated files. Both rot the
# same way: the viewer moves, nobody re-derives, and the skill confidently teaches
# a key name the gate now refuses. Checked here rather than by whoever remembers.

SKILL = ROOT / "skills" / "reportfast" / "SKILL.md"


def test_the_skill_s_stamp_is_the_stamp_the_gate_uses():
    stamp = contract.viewer_stamp()
    text = SKILL.read_text(encoding="utf-8")
    block = text.split("<!-- STAMP", 1)
    assert len(block) == 2, "SKILL.md lost its stamp comment"
    comment = block[1].split("-->", 1)[0]
    assert str(stamp["version"]) in comment, comment
    assert str(stamp["commit"])[:7] in comment, comment
    # The date in the comment is `derived_at`'s date, not a date someone typed.
    assert str(stamp["derived_at"])[:10] in comment, comment


def test_every_file_the_skill_points_at_exists():
    """The skill is instructions, and a path in instructions is a command.

    Checked as a list of the paths the prose names, not as a general link checker:
    the point is the specific directories an agent is told to read, and a
    `schema/` that resolves to nothing from `skills/` is exactly the wrong-path
    mistake the skill itself warns about.
    """
    text = SKILL.read_text(encoding="utf-8")
    for named in ("references/xopat-v3.md", "references/deployment.md",
                  "references/hydra-v2-to-v3.md", "manifest.example.yaml"):
        assert (SKILL.parent / named).is_file(), named
        assert named in text, f"{named} exists but the skill stopped naming it"
    # The two the skill says to *find*, because they live outside the bundle.
    assert "report_fast/schema" in text and "examples/" in text
    assert contract.SCHEMA_DIR.is_dir()
    assert (ROOT / "examples").is_dir()


if __name__ == "__main__":
    raise SystemExit(_support.run(globals(), origin="test_contract.py"))
