"""Whether the image server can open what a report points at.

A report is a file of links, so a wrong DataID is invisible until someone
clicks it: the viewer loads, the card is black, nothing anywhere reports an
error. This asks the tile server instead -- ``/v3/slides/info?slide_id=<DataID>``
for every distinct DataID in the report, the same call the viewer makes when a
session opens -- so a mistyped run id or a path outside the mount root arrives
as a line of text at build time rather than as a screenshot in a meeting.

    from report_fast.verify import data_ids_from_html, probe, summary

    checks = probe(data_ids_from_html(Path("report.html").read_text()))
    print(summary(checks))

It verifies addresses, not pictures: a DataID that answers 200 is a file the
server can read, which says nothing about whether it is the right file, is
aligned with its slide, or holds the classes the layer claims.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Iterable, List, Mapping, Optional, Sequence, Union

from .session import XopatSession
from .xopat import XopatEndpoint, resolve_endpoint

__all__ = [
    "Check",
    "data_ids_from_html",
    "info_url",
    "probe",
    "session_data_ids",
    "summary",
]

#: Session fragments in a report are `href="…#<percent-encoded json>"`.
_FRAGMENT_HREF = 'href="[^"]*#([^"]+)"'


@dataclass(frozen=True)
class Check:
    """What the image server said about one DataID.

    Attributes:
        data_id: The address that was asked about.
        status: HTTP status, or ``None`` when the server never answered --
            which is a network fact about the machine building the report, not
            a verdict on the DataID.
        detail: Status text, or the reason the request did not complete.
    """

    data_id: str
    status: Optional[int] = None
    detail: str = ""

    @property
    def checked(self) -> bool:
        """Whether the server answered at all."""
        return self.status is not None

    @property
    def ok(self) -> bool:
        """Whether the server says it can open the file."""
        return self.status is not None and 200 <= self.status < 300


def info_url(data_id: str, endpoint: Optional[XopatEndpoint] = None) -> str:
    """The tile server's `/info` URL for a DataID.

    Shape taken from the deployment's registered `slide_protocols` entry, which
    resolves to `{wsi_base_url}/v3/slides/info?slide_id=…` -- the viewer's own
    first request for a slide.
    """
    target = resolve_endpoint(endpoint)
    return (
        f"{target.wsi_base_url.rstrip('/')}/v3/slides/info?"
        f"{urllib.parse.urlencode({'slide_id': data_id})}"
    )


def session_data_ids(
    sessions: Union[XopatSession, Iterable[XopatSession]],
) -> List[str]:
    """Every distinct DataID the sessions reference, in first-seen order.

    Backgrounds and overlay layers alike -- a layer pointing at the wrong file
    is the more expensive mistake, and it is invisible on the card.
    """
    one = isinstance(sessions, XopatSession)
    every = [sessions] if one else list(sessions)
    found: List[str] = []
    for session in every:
        config = session.to_config() if isinstance(session, XopatSession) else session
        for entry in config.get("data", ()):  # positional pool: str or DataOverride
            data_id = entry if isinstance(entry, str) else (entry or {}).get("dataID")
            if data_id and data_id not in found:
                found.append(str(data_id))
    return found


def data_ids_from_html(html: str) -> List[str]:
    """DataIDs carried in the viewer links of a report.

    Reads the session out of each URL fragment rather than searching the text,
    so a DataID mentioned in prose is not mistaken for one that gets opened.
    This is how a report built by someone else -- a published artifact, say --
    is checked without rebuilding it.
    """
    found: List[str] = []
    for payload in re.findall(_FRAGMENT_HREF, html):
        try:
            config = json.loads(urllib.parse.unquote(payload))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue  # a link to somewhere that is not a session
        if isinstance(config, Mapping):
            found.extend(
                item for item in session_data_ids([config]) if item not in found
            )
    return found


def probe(
    data_ids: Iterable[str],
    endpoint: Optional[XopatEndpoint] = None,
    timeout: float = 10.0,
    parallel: int = 8,
) -> List[Check]:
    """Ask the image server whether it can open each DataID.

    Args:
        data_ids: Addresses to check; duplicates are collapsed.
        endpoint: Deployment to ask. Its `wsi_base_url` is the tile server.
        timeout: Seconds per request.
        parallel: Requests in flight. A report over forty cases is a few hundred
            DataIDs and this should take seconds, not minutes.

    Returns:
        One :class:`Check` per DataID, in the order given.
    """
    target = resolve_endpoint(endpoint)
    unique = list(dict.fromkeys(data_ids))

    def ask(data_id: str) -> Check:
        request = urllib.request.Request(
            info_url(data_id, target), headers={"User-Agent": "report-fast"}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as answer:
                return Check(data_id, answer.status, "openable")
        except urllib.error.HTTPError as error:
            return Check(data_id, error.code, error.reason or "")
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            return Check(data_id, None, str(getattr(error, "reason", error)))

    if not unique:
        return []
    workers = max(1, min(parallel, len(unique)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(ask, unique))


def summary(checks: Sequence[Check]) -> str:
    """A printable verdict: one line per problem, one line of totals."""
    bad = [check for check in checks if check.checked and not check.ok]
    silent = [check for check in checks if not check.checked]
    lines = [
        f"  {check.status} {check.data_id}"
        + (f"  ({check.detail})" if check.detail else "")
        for check in bad
    ]
    lines += [f"  ? {check.data_id}  ({check.detail})" for check in silent]
    head = (
        f"{len(checks) - len(bad) - len(silent)}/{len(checks)} DataIDs open; "
        f"{len(bad)} refused"
    )
    if silent:
        head += f", {len(silent)} unreachable from here"
    return "\n".join([head, *lines]) if lines else head
