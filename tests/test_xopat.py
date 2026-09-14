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


from report_fast.shader import (  # noqa: E402
    PALETTE_COUPLED_TYPES,
    ShaderConfig,
    ShaderParameter,
    ShaderType,
    allowed_params,
    classify_shader,
    heatmap_shader,
)
from report_fast.xopat import (  # noqa: E402
    DEFAULT_BASE_URL,
    DEFAULT_WSI_BASE_URL,
    PARAM_KEYS,
    XopatEndpoint,
    XopatError,
    build_session,
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
    session = build_session("/data/Public/slide.tiff", endpoint=endpoint(mount_root=""))
    assert session["data"] == ["/data/Public/slide.tiff"]


def test_thumbnail_url_shape():
    url = thumbnail_url(SLIDE, endpoint(), size=256)
    assert url == (
        f"{TILES}v3/slides/thumbnail/max_size/256/256"
        "?slide_id=data%2Fslides%2Fslide_001.tif"
    )


# ── session shape ───────────────────────────────────────────────────────────


def test_minimal_session_matches_viewer_shape():
    session = build_session(SLIDE, name="Slide 1", endpoint=endpoint())
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
    session = build_session(
        SLIDE,
        layers=[
            {"path": OVERLAY, "type": "heatmap", "name": "Prob"},
            {"path": "/mnt/data/mask.tif", "type": "identity", "name": "Mask"},
        ],
        endpoint=endpoint(),
        lossless=False,
    )
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
    session = build_session(
        SLIDE, layers=[{"path": OVERLAY, "type": "heatmap"}], endpoint=endpoint()
    )
    assert session["data"] == [
        "data/slides/slide_001.tif",
        {"dataID": "data/predictions/prob_001.tif", "options": {"format": "png"}},
    ]
    assert session["visualizations"][0]["shaders"]["layer_shader_0"][
        "dataReferences"
    ] == [1]


def test_lossless_can_be_turned_off():
    session = build_session(
        SLIDE,
        layers=[{"path": OVERLAY, "type": "heatmap"}],
        endpoint=endpoint(),
        lossless=False,
    )
    assert session["data"] == [
        "data/slides/slide_001.tif",
        "data/predictions/prob_001.tif",
    ]


def test_shader_config_layer_is_accepted():
    shader = heatmap_shader(
        name="Prob", data_source=OVERLAY, color="#ff0000", threshold=20, opacity=0.5
    )
    session = build_session(SLIDE, layers=[shader], endpoint=endpoint())
    layer = session["visualizations"][0]["shaders"]["layer_shader_0"]
    assert layer["params"] == {
        "color": "#ff0000",
        "threshold": 20,
        "inverse": False,
        "opacity": 0.5,
    }
    assert layer["dataReferences"] == [1]


def test_legacy_shader_conf_still_binds():
    v2_layer = {
        "shader_conf": {
            "type": "heatmap",
            "name": "Prob",
            "data": OVERLAY,
            "color": [1.0, 0.0, 0.0],
            "threshold": 30,
            "min": 0,  # v2-only key, dropped by v3
            "max": 255,
        }
    }
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        session = build_session(SLIDE, layers=[v2_layer], endpoint=endpoint())
    layer = session["visualizations"][0]["shaders"]["layer_shader_0"]
    params = layer["params"]
    assert layer["name"] == "Prob"
    assert layer["dataReferences"] == [1]
    assert session["data"][1]["dataID"] == "data/predictions/prob_001.tif"
    assert params["threshold"] == 30
    assert params["color"] == [1.0, 0.0, 0.0]
    assert "min" not in params and "max" not in params
    assert any("min" in str(w.message) for w in caught)


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
    session = build_session(SLIDE, layers=[custom], endpoint=endpoint())
    assert session["data"] == ["data/slides/slide_001.tif"]
    assert session["visualizations"][0]["shaders"]["layer_shader_0"] == custom


def test_group_layer_carries_members():
    session = build_session(
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
    )
    layer = session["visualizations"][0]["shaders"]["layer_shader_0"]
    assert layer["shaders"] == {"a": {"type": "heatmap"}}
    assert layer["order"] == ["a"]


def test_session_params_are_validated():
    session = build_session(SLIDE, params={"toolBar": False}, endpoint=endpoint())
    assert session["params"] == {"toolBar": False}
    with raises(XopatError):
        build_session(SLIDE, params={"toolabr": False}, endpoint=endpoint())
    assert "toolBar" in PARAM_KEYS


def test_allowlist_covers_what_a_viewer_export_emits():
    # Shape of a session the viewer itself exported from a working v3 tab.
    exported = {
        "bypassCookies": True,
        "theme": "auto",
        "activeBackgroundIndex": [0],
        "bypassCacheLoadTime": True,
        "viewport": {"zoomLevel": 0.526, "point": {"x": 0.5, "y": 0.488}},
    }
    session = build_session(SLIDE, params=exported, endpoint=endpoint())
    assert session["params"] == exported
    # `point` is only read inside `viewport`; flat, the viewer drops it.
    assert "point" not in PARAM_KEYS
    with raises(XopatError):
        build_session(SLIDE, params={"point": {"x": 0.5}}, endpoint=endpoint())


def test_retired_shader_types_are_rejected():
    for retired, replacement in (
        ("classify", "colormap"),
        ("segmentation", "colormap"),
        ("bounding_box", "iconmap"),
    ):
        with raises(XopatError) as ctx:
            build_session(
                SLIDE, layers=[{"path": OVERLAY, "type": retired}], endpoint=endpoint()
            )
        assert replacement in str(ctx.exception)


def test_unknown_shader_type_is_rejected():
    with raises(XopatError):
        build_session(
            SLIDE,
            layers=[{"path": OVERLAY, "type": "not-a-shader"}],
            endpoint=endpoint(),
        )


def test_a_bare_mask_path_is_already_a_layer():
    # `masks=["/mnt/data/mask.tif"]` is the out-of-the-box spelling for a mask,
    # so a bare path must not be rejected; its stem labels the layer.
    session = build_session(SLIDE, layers=["/mnt/data/mask.tif"], endpoint=endpoint())
    layers = list(session["visualizations"][0]["shaders"].values())
    assert [layer["name"] for layer in layers] == ["mask"]
    assert [layer["type"] for layer in layers] == ["heatmap"]


def test_layer_without_path_is_rejected():
    with raises(XopatError):
        build_session(SLIDE, layers=[{"type": "heatmap"}], endpoint=endpoint())


def test_undeclared_params_warn_and_drop():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        session = build_session(
            SLIDE,
            layers=[
                {
                    "path": OVERLAY,
                    "type": "heatmap",
                    "name": "Prob",
                    "params": {"threshold": 5, "radius": 9},
                }
            ],
            endpoint=endpoint(),
        )
    params = session["visualizations"][0]["shaders"]["layer_shader_0"]["params"]
    assert params == {"threshold": 5}
    assert any("radius" in str(w.message) for w in caught)


def test_declared_params_survive_including_channel_override():
    session = build_session(
        SLIDE,
        layers=[
            {
                "path": OVERLAY,
                "type": "heatmap",
                "params": {"use_channel0": "g", "threshold": 2, "inverse": True},
            }
        ],
        endpoint=endpoint(),
    )
    params = session["visualizations"][0]["shaders"]["layer_shader_0"]["params"]
    assert params == {"use_channel0": "g", "threshold": 2, "inverse": True}


def test_non_tiff_background_needs_a_registered_protocol_name():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        session = build_session("/mnt/data/slide.png", endpoint=endpoint())
    assert session["data"][0] == "data/slide.png"
    assert "protocol" not in session["background"][0]
    assert any("XOPAT_IMAGE_PROTOCOL" in str(w.message) for w in caught)

    session = build_session(
        "/mnt/data/slide.png", endpoint=endpoint(image_protocol="my_images")
    )
    # v3 deprecated `background[].protocol`; the DataOverride owns it now.
    assert session["data"][0] == {"dataID": "data/slide.png", "protocol": "my_images"}
    assert session["background"][0]["dataReference"] == 0


def test_configured_protocol_covers_tiff_too():
    # A viewer-exported session carries a protocol on its `.tiff` entries, so an
    # explicitly configured one is not filtered by extension.
    session = build_session(SLIDE, endpoint=endpoint(image_protocol="iipimage"))
    assert session["data"][0] == {
        "dataID": "data/slides/slide_001.tif",
        "protocol": "iipimage",
    }


def test_inline_js_protocol_is_refused():
    with raises(XopatError):
        build_session(
            "/mnt/data/slide.png",
            endpoint=endpoint(image_protocol="`^({type:'image',url:`${x}`})`"),
        )


# ── viewer URL ──────────────────────────────────────────────────────────────


def test_viewer_url_roundtrips_and_stays_in_the_fragment():
    session = build_session(SLIDE, name="Slide 1", endpoint=endpoint())
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


# ── shader param validation ─────────────────────────────────────────────────


def test_opacity_is_declared_for_every_layer_type():
    for shader_type in ShaderType:
        assert "opacity" in allowed_params(shader_type)


def test_classify_shader_keeps_palette_and_breaks_coupled():
    shader = classify_shader(
        name="Classes",
        classes=3,
        colors=["#ff0000", "#00ff00", "#0000ff"],
        data_source=OVERLAY,
    )
    assert shader.shader_type is ShaderType.COLORMAP
    assert ShaderType.COLORMAP in PALETTE_COUPLED_TYPES
    params = shader.param_values()
    assert params["color"]["default"] == ["#ff0000", "#00ff00", "#0000ff"]
    assert params["color"]["steps"] == 3
    assert params["threshold"]["breaks"] == [0.3333, 0.6667]
    assert len(params["threshold"]["breaks"]) + 1 == len(params["color"]["default"])


def test_classify_shader_can_leave_a_class_undrawn():
    shader = classify_shader(
        name="Annotations",
        classes=3,
        colors=["#ffffff", "#ff0000", "#00ff00"],
        data_source=OVERLAY,
        breaks=[0.25, 0.75],
        mask=[0, 1, 1],
    )
    params = shader.param_values()
    assert params["threshold"]["breaks"] == [0.25, 0.75]
    assert params["threshold"]["mask"] == [0, 1, 1]
    with raises(ValueError):
        classify_shader(
            name="Annotations",
            classes=3,
            colors=["#fff", "#f00", "#0f0"],
            data_source=OVERLAY,
            mask=[1, 1],
        )


def test_classify_shader_converts_rgb_tuples():
    shader = classify_shader(
        name="Classes", classes=2, colors=[(1, 0, 0), (0, 0, 1)], data_source=OVERLAY
    )
    assert shader.param_values()["color"]["default"] == ["#ff0000", "#0000ff"]


def test_classify_shader_rejects_bad_class_counts():
    with raises(ValueError):
        classify_shader(name="c", classes=1, colors=["#fff"], data_source=OVERLAY)
    with raises(ValueError):
        classify_shader(
            name="c", classes=3, colors=["#fff", "#000"], data_source=OVERLAY
        )
    with raises(ValueError):
        classify_shader(
            name="c",
            classes=3,
            colors=["#fff", "#000", "#0f0"],
            data_source=OVERLAY,
            breaks=[0.5],
        )


def test_validate_rejects_decoupled_palette():
    shader = classify_shader(
        name="c", classes=3, colors=["#fff", "#000", "#0f0"], data_source=OVERLAY
    )
    for param in shader.params:
        if param.name == "threshold":
            param.value = {"type": "advanced_slider", "breaks": [0.5]}
    with raises(ValueError) as ctx:
        shader.validate()
    assert "threshold.breaks" in str(ctx.exception)


def test_validate_rejects_undeclared_param_and_bad_opacity():
    config = ShaderConfig(
        shader_type=ShaderType.SOBEL, name="Edges", data_source=OVERLAY
    )
    config.add_param(
        ShaderParameter(
            name="not_a_param",
            shader_type=ShaderType.SOBEL,
            data_type=None,
            value=1,
        )
    )
    with raises(ValueError):
        config.validate()
    config.params = []
    config.opacity = 1.5
    with raises(ValueError):
        config.validate()


def test_heatmap_shader_bounds_its_threshold():
    with raises(ValueError):
        heatmap_shader(name="h", data_source=OVERLAY, threshold=0)
    with raises(ValueError):
        heatmap_shader(name="h", data_source=OVERLAY, threshold=101)


def test_to_xopat_layer_emits_v3_keys():
    layer = heatmap_shader(
        name="Prob", data_source=OVERLAY, opacity=0.4
    ).to_xopat_layer(data_references=[2])
    assert layer == {
        "type": "heatmap",
        "name": "Prob",
        "visible": 1,
        "fixed": False,
        "params": {
            "color": "#fff700",
            "threshold": 1,
            "inverse": False,
            "opacity": 0.4,
        },
        "dataReferences": [2],
    }


def main() -> int:
    tests = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    failures = []
    for name, test in tests:
        try:
            test()
        except Exception as exc:  # noqa: BLE001 - report every failure kind
            failures.append((name, exc))
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
