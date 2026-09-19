# Deployment

Where things live on this cluster, and how a path becomes an address the tile
server answers. Every default here is read off the code in this repo; when the
deployment moves, this page is stale and the code is not, so check the name it
cites.

## The probe, run before anything else

Batched and bounded — `--max-time` is load-bearing, see below:

```bash
printf '%s\n' "$DATA_IDS" | xargs -P 8 -I{} -n1 sh -c \
  'printf "%s %s\n" "$(curl -s --max-time 5 -o /dev/null -w "%{http_code}" \
  "https://xopat.rationai.cloud.trusted.e-infra.cz/wsi-service/v3/slides/info?slide_id={}")" "{}"' \
  | sort
```

**Bound every probe, and run them together.** `curl` on an unreachable host waits
its full default of 60 seconds, so one-at-a-time probing costs a minute per
DataID. Measured here: four DataIDs took 241s unbounded and sequential, 5s bounded
and parallel. On a 32-card report that is the difference between a step that
finishes and one that gets skipped — and a skipped probe is a report that says
"works" and shows black cards. (This is a cost that *can* explain an hour-long
build on a machine that cannot reach the tile server; it is not confirmed as the
cause of any particular slow report, since an older build's probe had its own
10s timeout and an agent may have been doing other work between probes.)

Three outcomes, and the third is the one most often misreported:

- **200** — the tile server can read a file at that address.
- **4xx** — the address is wrong, or the file is not there. Fix the DataID.
- **`000`** — no connection, so *nothing* was learned about the slide. `000` is
  curl's own no-answer code (`curl: (28) Operation timed out`); it is not a
  status the server sent, and reading it as one turns an unverifiable report into
  a confidently wrong one. Say "built, links unverified from here"; never
  "works". Run the probe from inside the cluster if the answer matters.

**From this pod, the whole `xopat.rationai.cloud.trusted.e-infra.cz` host times
out** — the tile endpoints *and* the viewer root — so neither a link's reach nor
the page's rendering can be checked here. Only the two endpoints off that host
are: `https://xopat.org/`, and the MLflow tracking API, which answers fine.
A deployment reachable to the reader may still be unreachable to the machine
building the report; that asymmetry is a fact about the report, so state it in
the preamble rather than resolving it by assumption.

## Viewer root — the mount selects the major version

- `https://xopat.rationai.cloud.trusted.e-infra.cz/v3/` is the v3 root
  (`DEFAULT_BASE_URL` in `report_fast/xopat.py`). The session is appended to it
  as `#<percent-encoded json>`.
- The same host serves **v2 at `/xopat/`** and **v3 at `/v3/`**. The mount picks
  the version, so a v3 session sent to `/xopat/` is parsed by the v2 viewer, and
  that *looks* like it loaded. A wrong base fails quietly rather than 404ing:
  check the root against a link known to work rather than guessing from the app.
- `XOPAT_BASE_URL` overrides per process, `XopatEndpoint(base_url=…)` per call.

## Tile server — its own mount, not under `/v3/`

- `https://xopat.rationai.cloud.trusted.e-infra.cz/wsi-service/`
  (`DEFAULT_WSI_BASE_URL`) answers the image endpoints:
  - `/v3/slides/info?slide_id=<DataID>` — the viewer's first request for a slide,
    and the one the probe above repeats.
  - `/v3/slides/thumbnail/max_size/{w}/{h}?slide_id=<DataID>` — the card's
    picture, the shape `thumbnail_url()` builds; default size 500 on both axes
    (`DEFAULT_THUMBNAIL_SIZE`).
- It is **not** under the viewer's `/v3/` — two mounts on one host, which is why
  `XopatEndpoint` carries both roots (`XOPAT_WSI_BASE_URL` for the second).
- A wrong tile root fails at nginx and the card just shows nothing: no error
  page, no exception, a report full of plausible blank cards.
- Only the WSI-Service tile source answers `/v3/slides/thumbnail`, so a data
  entry naming some other registered protocol (`iipimage` is the common one)
  yields no thumbnail rather than a broken image. `XopatSession.has_thumbnail`
  is that check, and `SlideCard` uses it to decide whether to draw an `<img>`.

## A path becomes a DataID

`mount_path()` is the whole rule — the DataID is the path relative to the mount
root, and anything it cannot make relative passes through untouched:

| Path | `mount_root` | DataID |
| --- | --- | --- |
| `/mnt/data/IKEM/…/case_001.svs` | `/mnt` (default) | `data/IKEM/…/case_001.svs` |
| `data/IKEM/…/case_001.svs` | `/mnt` | unchanged — already relative |
| `/srv/slides/case.svs` | `/mnt` | unchanged — outside the root, and no error |
| any of the above | `""` | unchanged, absolute kept |

- `/mnt` is `DEFAULT_MOUNT_ROOT`, overridable by `XOPAT_MOUNT_ROOT`. `""` means
  "strip nothing" — the form for deployments whose protocol entry interpolates a
  whole server-side path (an IIP-style entry, say). Note `mount_root=None` means
  something different: *no endpoint was chosen*, and it takes the default root.
- A `/mnt` left on a DataID is a blank card, not a message — the tile server
  resolves names, and `mnt/data/…` names nothing it has.

## Artifacts are addressed, never downloaded

- One artifact is `mflow/<experiment_id>/<run_id>/artifacts/<path>` — that is
  `artifact_data_id()`'s entire format. `mflow` is the DataID namespace this
  deployment mounts the artifact store under (`DEFAULT_ARTIFACT_PREFIX`), **not**
  a directory on the machine writing the report. This pod has no `/mflow`, and
  the address works without it.
- The result is already relative, so `mount_path()` leaves it untouched; the tile
  server resolves the store itself and nothing is fetched to build a report. Only
  `Mlflow.download()` moves bytes.
- `<experiment_id>` comes from the run (`Mlflow.experiment_id`), so a run id
  alone is not enough to write an artifact DataID by hand — which is why masks
  come from an `MlflowRun` source and the DataID is built for you.

## MLflow is two addresses, not one

| | Address | Reachable from |
| --- | --- | --- |
| Tracking API | `http://mlflow.rationai-mlflow:5000/` | inside the cluster — **including this pod** |
| Web UI | `https://mlflow.rationai.cloud.trusted.e-infra.cz/` | a browser; the links inside a report |

- The tracking server speaks the **2.x** API. The extra is capped
  `mlflow>=2.8,<3`: a 3.x client calls endpoints this server does not have and
  listing artifacts 404s.
- `web_url=None` disables `link()`, leaving a report with no run links rather
  than links to a host its reader cannot reach.

## Environment

| Variable | Default | Read in |
| --- | --- | --- |
| `XOPAT_BASE_URL` | `…/v3/` (above) | `XopatEndpoint.from_env` |
| `XOPAT_WSI_BASE_URL` | `…/wsi-service/` (above) | `XopatEndpoint.from_env` |
| `XOPAT_IMAGE_PROTOCOL` | *unset* → `None` | `XopatEndpoint.from_env` |
| `XOPAT_MOUNT_ROOT` | `/mnt` | `XopatEndpoint.from_env` |
| `MLFLOW_TRACKING_URI` | *unset* → whatever mlflow is configured with | `Mlflow.from_env` |
| `MLFLOW_WEB_URL` | the Web UI above | `Mlflow.from_env` |
| `REPORTFAST_MLFLOW_ARTIFACT_PREFIX` | `mflow` | `Mlflow.from_env` |
| `USER`, else `MLFLOW_USER` | owner tag of a run `publish()` creates | `Mlflow.publish` |

- `DEFAULT_ENDPOINT` is built at import, so setting `os.environ["XOPAT_*"]`
  afterwards does not move it — set the variables before importing, or pass an
  `endpoint=`.
- `XOPAT_IMAGE_PROTOCOL` unset leaves backgrounds to the deployment's
  `default_background_protocol`, which reads TIFFs natively; a non-TIFF
  background then only warns (`background_protocol`). `wsi_service` is the
  `slide_protocols` entry registered on this deployment. Only the *name* of a
  registered entry is accepted — see `xopat-v3.md`.

## Slide paths are shared state

A session names slides by path, never by bytes: the machine building the report
and the tile server must see the same file under the same root. Nothing checks
it — the builder never opens a slide, and a DataID that resolves on one host and
not the other stays invisible until someone opens the report.

Probing distinct DataIDs against `/v3/slides/info` is the cheap check, and it
proves an **address**, not a picture. A 200 means the server can read a file
there — not that it is the right file, is aligned with its slide, or holds the
classes the layer claims. Where the probe cannot reach (see the top of this
page), say so in the report's preamble rather than asserting the cards work.
