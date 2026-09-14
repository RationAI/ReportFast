# Deployment

Where things live on this cluster and how a path becomes an address the tile
server answers. Every default here is read off the code, with its line.

## Viewer root — the mount selects the major version

- `https://xopat.rationai.cloud.trusted.e-infra.cz/v3/` is the v3 root
  (`DEFAULT_BASE_URL`, `report_fast/xopat.py:64`). The session is appended to it
  as `#<percent-encoded json>`.
- The same host serves **v2 at `/xopat/`** — where the deleted `redirect.php`
  relay used to live — and **v3 at `/v3/`**. The mount picks the version, so a
  v3 session sent to `/xopat/` is parsed by the v2 viewer, and that *looks* like
  it loaded. A wrong base fails quietly rather than 404ing: pin the root, and
  check it against a link that is known to work rather than guessing from the app.
- `XOPAT_BASE_URL` overrides it per process, `XopatEndpoint(base_url=…)` per call.

## Tile server — its own mount, not under `/v3/`

- `https://xopat.rationai.cloud.trusted.e-infra.cz/wsi-service/`
  (`DEFAULT_WSI_BASE_URL`, `xopat.py:65`) answers the image endpoints:
  - `/v3/slides/info?slide_id=<DataID>` — the viewer's first request for a
    slide, and the probe `report_fast/verify.py:83` repeats at build time.
  - `/v3/slides/thumbnail/max_size/{w}/{h}?slide_id=<DataID>` — the card's
    picture, the shape `thumbnail_url()` builds (`xopat.py:247`); default size
    500 on both axes (`DEFAULT_THUMBNAIL_SIZE`, `xopat.py:67`).
- It is **not** under the viewer's `/v3/` — two mounts on one host, which is why
  `XopatEndpoint` carries both roots (`XOPAT_WSI_BASE_URL` for the second).
- A wrong tile root 403s at nginx and the card just shows nothing: no error
  page, no exception, a report full of plausible blank cards.
- Only the WSI-Service tile source answers `/v3/slides/thumbnail`
  (`session.py:300`), so a data entry naming some other registered protocol
  yields no thumbnail rather than a broken image.

## A path becomes a DataID

`mount_path()` (`xopat.py:217`) is the whole rule — the DataID is the path
relative to the mount root:

| Path | `mount_root` | DataID |
| --- | --- | --- |
| `/mnt/data/IKEM/…/case_001.svs` | `/mnt` (default) | `data/IKEM/…/case_001.svs` |
| `data/IKEM/…/case_001.svs` | `/mnt` | unchanged — already relative |
| `/srv/slides/case.svs` | `/mnt` | unchanged — outside the root, and no error |
| any of the above | `""` | unchanged, absolute kept |

- `/mnt` is `DEFAULT_MOUNT_ROOT` (`xopat.py:66`), overridable by
  `XOPAT_MOUNT_ROOT`; `""` is for deployments whose protocol template
  interpolates a whole server-side path (an IIP-style entry, say).
- A `/mnt` left on a DataID is a blank card, not a message — the tile server
  resolves names, and `mnt/data/…` names nothing it has.

## Artifacts are addressed, never downloaded

- One artifact is `mflow/<experiment_id>/<run_id>/artifacts/<path>`
  (`artifact_data_id`, `mlflow.py:88-102`). `mflow` is the DataID namespace this
  deployment mounts the artifact store under (`DEFAULT_ARTIFACT_PREFIX`,
  `mlflow.py:60`) — **not** a directory on the machine writing the report. This
  pod has no `/mflow`, and the API path works without it.
- The result is already relative, so `mount_path()` leaves it untouched; the tile
  server resolves the store itself, and nothing is fetched to build a report.
  Only `Mlflow.download()` moves bytes.
- `<experiment_id>` comes from the run (`Mlflow.experiment_id`, `mlflow.py:174`),
  so a run id alone is not enough to write an artifact DataID by hand.

## MLflow is two addresses, not one

| | Address | Reachable from |
| --- | --- | --- |
| Tracking API | `http://mlflow.rationai-mlflow:5000/` | inside the cluster only |
| Web UI | `https://mlflow.rationai.cloud.trusted.e-infra.cz/` | a browser; the links inside a report |

- The tracking server speaks the **2.x** API. The extra is capped
  `mlflow>=2.8,<3` (`pyproject.toml:16`): a 3.x client calls endpoints this
  server does not have and listing artifacts 404s.
- `web_url=None` disables `link()`, leaving a report with no run links rather
  than links to a host its reader cannot reach.

## Environment

| Variable | Default | Read in |
| --- | --- | --- |
| `XOPAT_BASE_URL` | `https://xopat.rationai.cloud.trusted.e-infra.cz/v3/` | `xopat.py:198` |
| `XOPAT_WSI_BASE_URL` | `https://xopat.rationai.cloud.trusted.e-infra.cz/wsi-service/` | `xopat.py:199` |
| `XOPAT_IMAGE_PROTOCOL` | *unset* → `None` | `xopat.py:200` |
| `XOPAT_MOUNT_ROOT` | `/mnt` | `xopat.py:201` |
| `XOPAT_SESSION_CONFIG` | *unset* — builtin preset only | `config.py:25,166` |
| `MLFLOW_TRACKING_URI` | *unset* → whatever mlflow is configured with | `mlflow.py:147` |
| `MLFLOW_WEB_URL` | `https://mlflow.rationai.cloud.trusted.e-infra.cz/` | `mlflow.py:148` |
| `REPORTFAST_MLFLOW_ARTIFACT_PREFIX` | `mflow` | `mlflow.py:150` |
| `USER`, else `MLFLOW_USER` | owner tag of a run `publish()` creates | `mlflow.py:369` |

- `DEFAULT_ENDPOINT` is built at import (`xopat.py:209`), so setting
  `os.environ["XOPAT_*"]` afterwards does not move it — set the variables before
  importing, or pass an `endpoint=`.
- `XOPAT_IMAGE_PROTOCOL` unset leaves backgrounds to the deployment's
  `default_background_protocol`, which reads TIFFs natively; a non-TIFF
  background then only warns (`xopat.py:405-414`). `wsi_service` is the
  `slide_protocols` entry registered on this deployment — the name
  `scripts/dysplasia_tile_masks.py:33` gives its `.czi` backgrounds. Only the
  name of a registered entry is accepted; see `xopat-v3.md`.

## Slide paths are shared state

A session names slides by path, never by bytes: the machine building the report
and the tile server must see the same file under the same root. Nothing checks
it — the builder never opens a slide, and a DataID that resolves on one host and
not the other stays invisible until someone opens the report.

Probing every distinct DataID against `/v3/slides/info` (`report_fast/verify.py`)
is the cheap check: it proves an *address*, not a picture. A 200 means the server
can read a file there, not that it is the right file, is aligned with its slide,
or holds the classes the layer claims.
