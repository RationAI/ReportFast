"""Tests for the xOpat v3 integration (session shape, URLs, shader params).

Run:      cd /home/jovyan/report_fast && python tests/test_xopat.py
Pytest:   pytest tests/test_xopat.py        # functions are plain asserts too

The assertions encode what xOpat 3.1.0 actually accepts, so they double as the
specification for the v2 -> v3 migration:

  * `src/parse-input.js`        -- fragment sessions are parsed client-side
  * `src/types/app.d.ts`        -- DataID / BackgroundItem / VisualizationItem
  * `src/config.json` (`setup`) -- the session `params` allowlist
  * `src/classes/slide-protocols.ts` -- inline JS `protocol` is rejected
  * `src/libs/flex-renderer/flex-renderer.js` -- per-layer params,
    `_sanitizeShaderParams` drops undeclared ones, `dataReferences` is plural
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import warnings
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


from report_fast.session import XopatSession  # noqa: E402
from report_fast.xopat import (  # noqa: E402
    DEFAULT_BASE_URL,
    DEFAULT_WSI_BASE_URL,
    XopatEndpoint,
    XopatError,
    mount_path,
    session_fragment,
    thumbnail_url,
    viewer_url,
)

SLIDE = "/mnt/data/slides/slide_001.tif"
OVERLAY = "/mnt/data/predictions/prob_001.tif"
BASE = "https://xopat.example.org/v3/"
TILES = "https://tiles.example.org/wsi-service/"


class _Capture:
    exception: Exception | None = None


@contextmanager
def raises(error):
    capture = _Capture()
    try:
        yield capture
    except error as exc:
        capture.exception = exc
    else:
        raise AssertionError(f"expected {error.__name__} to be raised")


def endpoint(**overrides):
    values = {"base_url": BASE, "wsi_base_url": TILES, "mount_root": "/mnt"}
    values.update(overrides)
    return XopatEndpoint(**values)


def decode(url):
    """Session a v3 viewer would read out of `url`'s fragment."""
    return json.loads(urllib.parse.unquote(url.split("#", 1)[1]))


# ── mount paths & thumbnails ────────────────────────────────────────────────


def test_mount_path_strips_shared_root():
    assert mount_path(SLIDE) == "data/slides/slide_001.tif"
    assert mount_path(SLIDE, "/mnt/data") == "slides/slide_001.tif"


def test_mount_path_passes_through_unmatched_or_relative():
    assert mount_path("/srv/other/slide.tif") == "/srv/other/slide.tif"
    assert mount_path("slides/slide.tif") == "slides/slide.tif"


def test_empty_mount_root_keeps_absolute_data_ids():
    # Sessions exported by the viewer itself address data by absolute server
    # path (`/data/Public/.../O16-11870.tiff`), which protocols templating a
    # whole path need preserved.
    assert mount_path("/data/Public/slide.tiff", "") == "/data/Public/slide.tiff"
    session = XopatSession.from_slide(
        "/data/Public/slide.tiff", endpoint=endpoint(mount_root=""),
    ).to_config()
    assert session["data"] == ["/data/Public/slide.tiff"]


def test_thumbnail_url_shape():
    url = thumbnail_url(SLIDE, endpoint(), size=256)
    assert url == (
        f"{TILES}v3/slides/thumbnail/max_size/256/256"
        "?slide_id=data%2Fslides%2Fslide_001.tif"
    )


# ── session shape ───────────────────────────────────────────────────────────


def test_minimal_session_matches_viewer_shape():
    session = XopatSession.from_slide(
        SLIDE, name="Slide 1", endpoint=endpoint(),
    ).to_config()
    assert session["params"] == {}
    assert session["data"] == ["data/slides/slide_001.tif"]
    assert session["background"] == [
        {
            "dataReference": 0,
            "visualizationIndex": 0,
            "name": "Slide 1",
        }
    ]
    assert session["visualizations"] == [{"name": "Slide 1", "shaders": {}}]
    # v2 keys that v3 has no notion of must not survive.
    assert "lossless" not in json.dumps(session)
    assert "slides" not in session and "visualisation" not in session


def test_layers_bind_with_plural_data_references():
    session = XopatSession.from_slide(
        SLIDE,
        layers=[
            {"path": OVERLAY, "type": "heatmap", "name": "Prob"},
            {"path": "/mnt/data/mask.tif", "type": "identity", "name": "Mask"},
        ],
        endpoint=endpoint(),
        lossless=False,
    ).to_config()
    shaders = session["visualizations"][0]["shaders"]
    assert list(shaders) == ["layer_shader_0", "layer_shader_1"]
    assert session["data"] == [
        "data/slides/slide_001.tif",
        "data/predictions/prob_001.tif",
        "data/mask.tif",
    ]
    assert shaders["layer_shader_0"]["dataReferences"] == [1]
    assert shaders["layer_shader_1"]["dataReferences"] == [2]
    assert "dataReference" not in shaders["layer_shader_0"]
    assert shaders["layer_shader_0"]["visible"] == 1
    assert shaders["layer_shader_0"]["type"] == "heatmap"


def test_overlay_data_asks_for_lossless_tiles_by_default():
    # v2 asked for this with `visualizations[].lossless`; v3 spells it per data
    # entry, and the background keeps the deployment default.
    session = XopatSession.from_slide(
        SLIDE, layers=[{"path": OVERLAY, "type": "heatmap"}], endpoint=endpoint()
    ).to_config()
    assert session["data"] == [
        "data/slides/slide_001.tif",
        {"dataID": "data/predictions/prob_001.tif", "options": {"format": "png"}},
    ]
    assert session["visualizations"][0]["shaders"]["layer_shader_0"][
        "dataReferences"
    ] == [1]


def test_lossless_can_be_turned_off():
    session = XopatSession.from_slide(
        SLIDE,
        layers=[{"path": OVERLAY, "type": "heatmap"}],
        endpoint=endpoint(),
        lossless=False,
    ).to_config()
    assert session["data"] == [
        "data/slides/slide_001.tif",
        "data/predictions/prob_001.tif",
    ]


def test_legacy_shader_conf_still_binds():
    # v2 sat the params beside `type` instead of under `params`, so the wrapper
    # is unwrapped. Keys v3 has no use for ride along -- the viewer drops what
    # it does not read, and this library does not decide what exists.
    v2_layer = {
        "shader_conf": {
            "type": "heatmap",
            "name": "Prob",
            "data": OVERLAY,
            "color": [1.0, 0.0, 0.0],
            "threshold": 30,
            "min": 0,
            "max": 255,
        }
    }
    session = XopatSession.from_slide(
        SLIDE, layers=[v2_layer], endpoint=endpoint(),
    ).to_config()
    layer = session["visualizations"][0]["shaders"]["layer_shader_0"]
    params = layer["params"]
    assert layer["type"] == "heatmap"
    assert layer["name"] == "Prob"
    assert layer["dataReferences"] == [1]
    assert session["data"][1]["dataID"] == "data/predictions/prob_001.tif"
    assert params["threshold"] == 30
    assert params["color"] == [1.0, 0.0, 0.0]
    assert params["min"] == 0 and params["max"] == 255


def test_finished_layer_is_emitted_verbatim():
    custom = {
        "type": "group",
        "name": "Combo",
        "params": {"whatever": True},
        "shaders": {"a": {"type": "heatmap"}},
        "order": ["a"],
        "dataReferences": [0],
        "visible": 1,
        "fixed": False,
    }
    session = XopatSession.from_slide(
        SLIDE, layers=[custom], endpoint=endpoint(),
    ).to_config()
    assert session["data"] == ["data/slides/slide_001.tif"]
    assert session["visualizations"][0]["shaders"]["layer_shader_0"] == custom


def test_group_layer_carries_members():
    session = XopatSession.from_slide(
        SLIDE,
        layers=[
            {
                "path": OVERLAY,
                "type": "group",
                "name": "Combo",
                "shaders": {"a": {"type": "heatmap"}},
                "order": ["a"],
            }
        ],
        endpoint=endpoint(),
    ).to_config()
    layer = session["visualizations"][0]["shaders"]["layer_shader_0"]
    assert layer["shaders"] == {"a": {"type": "heatmap"}}
    assert layer["order"] == ["a"]


def test_a_bare_mask_path_is_already_a_layer():
    # `masks=["/mnt/data/mask.tif"]` is the out-of-the-box spelling for a mask,
    # so a bare path must not be rejected; its stem labels the layer.
    session = XopatSession.from_slide(
        SLIDE, layers=["/mnt/data/mask.tif"], endpoint=endpoint(),
    ).to_config()
    layers = list(session["visualizations"][0]["shaders"].values())
    assert [layer["name"] for layer in layers] == ["mask"]
    assert [layer["type"] for layer in layers] == ["heatmap"]


def test_layer_without_path_is_rejected():
    with raises(XopatError):
        XopatSession.from_slide(
            SLIDE, layers=[{"type": "heatmap"}], endpoint=endpoint(),
        ).to_config()


def test_declared_params_survive_including_channel_override():
    session = XopatSession.from_slide(
        SLIDE,
        layers=[
            {
                "path": OVERLAY,
                "type": "heatmap",
                "params": {"use_channel0": "g", "threshold": 2, "inverse": True},
            }
        ],
        endpoint=endpoint(),
    ).to_config()
    params = session["visualizations"][0]["shaders"]["layer_shader_0"]["params"]
    assert params == {"use_channel0": "g", "threshold": 2, "inverse": True}


def test_non_tiff_background_needs_a_registered_protocol_name():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        session = XopatSession.from_slide(
            "/mnt/data/slide.png", endpoint=endpoint(),
        ).to_config()
    assert session["data"][0] == "data/slide.png"
    assert "protocol" not in session["background"][0]
    assert any("XOPAT_IMAGE_PROTOCOL" in str(w.message) for w in caught)

    session = XopatSession.from_slide(
        "/mnt/data/slide.png", endpoint=endpoint(image_protocol="my_images")
    ).to_config()
    # v3 deprecated `background[].protocol`; the DataOverride owns it now.
    assert session["data"][0] == {"dataID": "data/slide.png", "protocol": "my_images"}
    assert session["background"][0]["dataReference"] == 0


def test_configured_protocol_covers_tiff_too():
    # A viewer-exported session carries a protocol on its `.tiff` entries, so an
    # explicitly configured one is not filtered by extension.
    session = XopatSession.from_slide(
        SLIDE, endpoint=endpoint(image_protocol="iipimage"),
    ).to_config()
    assert session["data"][0] == {
        "dataID": "data/slides/slide_001.tif",
        "protocol": "iipimage",
    }


def test_inline_js_protocol_is_refused():
    with raises(XopatError):
        XopatSession.from_slide(
            "/mnt/data/slide.png",
            endpoint=endpoint(image_protocol="`^({type:'image',url:`${x}`})`"),
        ).to_config()


# ── viewer URL ──────────────────────────────────────────────────────────────


def test_viewer_url_roundtrips_and_stays_in_the_fragment():
    session = XopatSession.from_slide(
        SLIDE, name="Slide 1", endpoint=endpoint(),
    ).to_config()
    url = viewer_url(session, endpoint())
    assert url.startswith(BASE.rstrip("/") + "/#%7B")
    assert "?" not in url
    assert decode(url) == session
    assert session_fragment(session) == url.split("#", 1)[1]


def test_endpoint_normalises_trailing_slash():
    url = viewer_url({"params": {}}, XopatEndpoint(base_url="https://x.test/v3"))
    assert url == "https://x.test/v3/#%7B%22params%22%3A%7B%7D%7D"


def test_default_base_url_is_the_v3_mount():
    # Where a host serves both majors the mount decides the version: a v3
    # session put on the v2 mount still opens, parsed by v2, so a wrong base
    # fails silently instead of loudly.
    assert DEFAULT_BASE_URL.endswith("/v3/")
    assert "/xopat/" not in DEFAULT_BASE_URL


def test_default_thumbnail_url_reaches_the_tile_server():
    # The tile server is mounted separately from the viewer (the deployment's
    # own wsi_service entry points at /wsi-service), so its root cannot be
    # guessed from the viewer's URL -- a wrong one 403s at nginx and the card
    # silently loses its picture. Pinned to the request the viewer itself makes.
    target = XopatEndpoint(wsi_base_url=DEFAULT_WSI_BASE_URL, mount_root="/mnt")
    assert thumbnail_url("/mnt/x.tif", target, 500) == (
        "https://xopat.rationai.cloud.trusted.e-infra.cz/wsi-service"
        "/v3/slides/thumbnail/max_size/500/500?slide_id=x.tif"
    )


def test_endpoint_reads_environment():
    saved = {k: os.environ.get(k) for k in os.environ if k.startswith("XOPAT_")}
    os.environ.update(
        {
            "XOPAT_BASE_URL": "https://a.test/v3/",
            "XOPAT_WSI_BASE_URL": "https://b.test/tiles/",
            "XOPAT_IMAGE_PROTOCOL": "zarr",
            "XOPAT_MOUNT_ROOT": "/data",
        }
    )
    try:
        target = XopatEndpoint.from_env()
        assert target.base_url == "https://a.test/v3/"
        assert target.wsi_base_url == "https://b.test/tiles/"
        assert target.image_protocol == "zarr"
        assert target.mount_root == "/data"
        assert thumbnail_url("/data/x.tif", target).endswith("?slide_id=x.tif")
        # Explicit overrides still win over the environment.
        assert XopatEndpoint.from_env(base_url="https://c.test/").base_url == (
            "https://c.test/"
        )
    finally:
        for key in [k for k in os.environ if k.startswith("XOPAT_")]:
            os.environ.pop(key)
        os.environ.update(saved)


def main() -> int:
    # This file's docstring has said `Run: python tests/test_xopat.py` since it was
    # written, and until now the file had no `__main__` block, so that command ran
    # 22 tests' worth of imports and then exited 0 without calling one of them --
    # a green run that measured nothing. Every other file here has the runner.
    tests = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    failed = 0
    for name, test in tests:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                test()
        except Exception as exc:  # noqa: BLE001 - the runner reports, it does not handle
            failed += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
