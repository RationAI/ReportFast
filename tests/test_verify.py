"""Tests for the DataID probe: what a report links to, and whether it opens.

No network. The three parts are tested separately because they fail separately:
reading DataIDs out of a session, reading them back out of a finished report,
and asking the tile server about them. The last one is stubbed at the one place
it touches the socket -- ``urllib.request.urlopen`` -- with the three answers it
gives: opened, refused, and never answered at all.

Run:      cd /home/jovyan/report_fast && python tests/test_verify.py
Pytest:   pytest tests/test_verify.py
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from report_fast import XopatSession  # noqa: E402
from report_fast.verify import (  # noqa: E402
    Check,
    data_ids_from_html,
    info_url,
    probe,
    session_data_ids,
    summary,
)
from report_fast.xopat import XopatEndpoint  # noqa: E402


def session(*data_ids: str) -> XopatSession:
    """The smallest session that references the given DataIDs.

    Built the way the library builds them -- `data` positional, `shaders` a dict
    keyed by id, one `dataReference` per background -- because a stub shaped
    unlike the real thing tests the parser and not the sessions in a report.
    """
    return XopatSession.from_config(
        {
            "data": list(data_ids),
            "background": [{"dataReference": 0, "name": "case"}],
            "visualizations": [{"name": "case", "shaders": {}}],
        }
    )


def page(*data_ids: str) -> str:
    """HTML shaped like a real report: links whose fragment is a session."""
    links = []
    for number, data_id in enumerate(data_ids):
        config = {
            "params": {},
            "data": [data_id],
            "background": [{"dataReference": 0, "name": f"case{number}"}],
            "visualizations": [{"name": f"case{number}", "shaders": {}}],
        }
        payload = urllib.parse.quote(json.dumps(config, sort_keys=True))
        links.append(f'<a href="https://viewer.test/v3/view/#{payload}">case</a>')
    return "<html><body>" + "\n".join(links) + "</body></html>"


# ── what a session references ───────────────────────────────────────────────


def test_session_data_ids_finds_background_and_overlay_together():
    # A layer pointing at the wrong file is the more expensive mistake and is
    # invisible on the card, so overlays are checked with the same weight as
    # backgrounds -- not treated as "the background was fine, ship it".
    made = XopatSession.from_config(
        {
            "data": ["slides/case_001.svs", {"dataID": "slides/case_001_labels.tif"}],
            "background": [{"dataReference": 0, "name": "case_001"}],
            "visualizations": [
                {
                    "name": "case_001",
                    "shaders": {
                        "layer_shader_0": {
                            "type": "heatmap",
                            "name": "Tissue",
                            "dataReferences": [1],
                        }
                    },
                }
            ],
        }
    )
    assert session_data_ids(made) == [
        "slides/case_001.svs",
        "slides/case_001_labels.tif",
    ]


def test_duplicates_across_sessions_collapse_to_one_question():
    # Forty cases sharing one mask file means one HTTP request, not forty --
    # this is what keeps a large report's probe in seconds.
    found = session_data_ids([session("shared/mask.tif"), session("shared/mask.tif")])
    assert found == ["shared/mask.tif"]


def test_an_empty_data_entry_is_not_asked_about():
    assert session_data_ids(session("")) == []


# ── what a finished report references ───────────────────────────────────────


def test_data_ids_are_read_from_the_link_not_the_prose():
    # A DataID mentioned in a paragraph is not a link the viewer opens. Reading
    # the fragment instead of grepping the text is what keeps a report about
    # `mflow/…/ghost.tif` from being reported as linking to it.
    html = page("slides/case_001.svs") + "<p>note: mflow/x/y/ghost.tif is missing</p>"
    assert data_ids_from_html(html) == ["slides/case_001.svs"]


def test_a_report_built_by_someone_else_can_still_be_checked():
    # The published-artifact case, against HTML this library actually wrote
    # rather than a hand-typed approximation: read the DataIDs back out of a
    # finished file instead of rebuilding it and hoping the rebuild matches.
    import io
    import tempfile
    from contextlib import redirect_stderr, redirect_stdout

    from report_fast.__main__ import main

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for stem in ("case_001", "case_002"):
            (root / f"{stem}.tif").write_bytes(b"x")
        (root / "m.yaml").write_text(
            f"title: T\nbackground: {{drive: {root}}}\n", encoding="utf-8"
        )
        # The build prints its plan; that is the CLI's business, not this test's.
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            main(["build", str(root / "m.yaml"), "--no-check"])
        found = data_ids_from_html((root / "m.html").read_text(encoding="utf-8"))
    assert sorted(Path(item).name for item in found) == ["case_001.tif", "case_002.tif"]
    # Either form is a correct answer -- a mount-relative DataID when an endpoint
    # was chosen, the path as written when it was not. What must not happen is a
    # name that is not the file the case was built from.
    assert all(item.startswith("mflow/") or str(root) in item for item in found)


def test_a_share_of_one_file_between_cases_is_asked_about_once():
    # Two cards, one mask: the same DataID appears in several fragments and the
    # probe is built on the distinct set.
    assert data_ids_from_html(page("a.svs", "b.svs") + page("a.svs")) == [
        "a.svs",
        "b.svs",
    ]


def test_a_fragment_that_is_not_json_is_skipped():
    html = '<a href="https://x.test/#notafragment">x</a>' + page("real/case.svs")
    assert data_ids_from_html(html) == ["real/case.svs"]


# ── the question asked of the tile server ───────────────────────────────────


def test_the_info_url_is_the_viewers_own_first_request():
    # Same endpoint, same path, same parameter name as the deployment's
    # registered slide_protocols entry -- if this drifts, a green probe means
    # nothing. The trailing slash on the base is the caller's mess to survive.
    endpoint = XopatEndpoint(wsi_base_url="https://tiles.test/prefix/")
    url = info_url("mflow/1/run/artifacts/case_1.svs", endpoint)
    assert url == (
        "https://tiles.test/prefix/v3/slides/info?slide_id=mflow%2F1%2Frun%2F"
        "artifacts%2Fcase_1.svs"
    )
    # The separators are escaped and the server-side path still resolves: the
    # tile service decodes the parameter before looking the file up.
    assert "slide_id=mflow/1" not in url


def test_a_path_the_shell_hates_still_round_trips():
    # Real names in this deployment: spaces, parentheses, non-ASCII. The DataID
    # must arrive as one parameter, not as a truncated one at the first space.
    endpoint = XopatEndpoint(wsi_base_url="https://tiles.test")
    for awkward in ("case 1 (H&E).svs", "Køle svs/slide.svs", "a&b=c.svs"):
        url = info_url(awkward, endpoint)
        query = urllib.parse.urlparse(url).query
        assert urllib.parse.parse_qs(query)["slide_id"][0] == awkward


# ── the probe's three answers ───────────────────────────────────────────────


class _Answer:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def stub_urlopen(answer):
    """Replace the socket. `answer(data_id)` returns a status or raises.

    Returns the restorer, so every test puts the real one back in a `finally`
    and one broken stub cannot leak into the next test -- or into a later file
    that really did mean to hit the network.
    """
    real = urllib.request.urlopen

    def fake(request, timeout=None):
        query = urllib.parse.urlparse(request.full_url).query
        data_id = urllib.parse.parse_qs(query)["slide_id"][0]
        result = answer(data_id)
        if isinstance(result, Exception):
            raise result
        return _Answer(result)

    urllib.request.urlopen = fake
    return lambda: setattr(urllib.request, "urlopen", real)


def test_an_openable_dataid_is_ok():
    restore = stub_urlopen(lambda data_id: 200)
    try:
        checks = probe(["case/one.svs"], XopatEndpoint(wsi_base_url="https://t.test"))
    finally:
        restore()
    assert checks == [Check("case/one.svs", 200, "openable")]
    assert checks[0].ok and checks[0].checked


def test_a_refused_dataid_arrives_as_a_line_of_text_not_a_black_card():
    restore = stub_urlopen(
        lambda data_id: urllib.error.HTTPError(data_id, 404, "not found", None, None)
    )
    try:
        checks = probe(["case/ghost.svs"], XopatEndpoint(wsi_base_url="https://t.test"))
    finally:
        restore()
    assert checks[0].status == 404 and not checks[0].ok
    assert checks[0].checked  # the server answered; the answer was no
    assert "404 case/ghost.svs" in summary(checks)


def test_no_answer_is_a_network_fact_not_a_verdict():
    # status=None is the difference between "your run id is wrong" and "you are
    # on a plane": a refused DataID is a broken report, an unreachable one is a
    # build environment, and only the first is worth failing CI for.
    restore = stub_urlopen(
        lambda data_id: urllib.error.URLError("connection refused")
    )
    try:
        checks = probe(["case/one.svs"], XopatEndpoint(wsi_base_url="https://t.test"))
    finally:
        restore()
    assert checks[0].status is None and not checks[0].checked and not checks[0].ok
    assert "unreachable" in summary(checks)


def test_the_probe_keeps_the_order_it_was_given():
    answers = {"a.svs": 200, "b.svs": 403, "c.svs": 404}
    restore = stub_urlopen(
        lambda data_id: (
            urllib.error.HTTPError(data_id, answers[data_id], "no", None, None)
            if answers[data_id] != 200
            else 200
        )
    )
    try:
        checks = probe(
            ["c.svs", "a.svs", "b.svs"], XopatEndpoint(wsi_base_url="https://t.test")
        )
    finally:
        restore()
    assert [check.data_id for check in checks] == ["c.svs", "a.svs", "b.svs"]


def test_duplicates_are_collapsed_before_the_socket():
    asked = []
    real_urlopen = urllib.request.urlopen

    def fake(request, timeout=None):
        asked.append(request.full_url)
        return _Answer(200)

    urllib.request.urlopen = fake
    try:
        checks = probe(["x/mask.tif"] * 5, XopatEndpoint(wsi_base_url="https://t.test"))
    finally:
        urllib.request.urlopen = real_urlopen
    assert len(asked) == 1
    assert len(checks) == 1


def test_nothing_to_ask_is_not_an_error():
    assert probe([]) == []


# ── the printable verdict ───────────────────────────────────────────────────


def test_a_clean_probe_prints_one_line():
    verdict = summary([Check("a", 200, "openable"), Check("b", 204, "openable")])
    assert verdict == "2/2 DataIDs open; 0 refused"


def test_the_totals_line_comes_first_and_the_problems_underneath():
    checks = [
        Check("a", 200, "openable"),
        Check("b", 404, "not found"),
        Check("c", None, "connection refused"),
    ]
    lines = summary(checks).splitlines()
    assert lines[0] == "1/3 DataIDs open; 1 refused, 1 unreachable from here"
    assert "404 b" in lines[1] and "(not found)" in lines[1]
    assert "? c" in lines[2]


if __name__ == "__main__":
    failures = []
    for name, function in sorted(globals().items()):
        if name.startswith("test_") and callable(function):
            try:
                function()
                print(f"ok   {name}")
            except Exception as error:  # noqa: BLE001 - the runner reports everything
                failures.append(name)
                print(f"FAIL {name}: {type(error).__name__}: {error}")
    print(f"\n{len(failures)} failed")
    raise SystemExit(1 if failures else 0)
