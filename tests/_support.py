"""The two things every test file in this directory needs to run without pytest.

Run:      python tests/test_contract.py         (no pytest installed, no plugin)
Pytest:   pytest tests

Every test module here is runnable both ways, and the difference is not cosmetic:
the files are the documentation of what a gate does, and a gate you can only
demonstrate through a test runner is a gate nobody reads. So the standard library
has to be enough.

Two things stop that being true on its own. `pytest.raises` does not exist without
pytest, and `pytest.skip` raises `Skipped`, which subclasses `BaseException` -- so
a bare `function()` loop lets it escape as a crash and a `try/except Exception`
loop reports it as a FAIL. `unittest.SkipTest` is the third possibility, and it
does subclass `Exception`. Three exception types, two wrong answers each.

`run()` handles all three, which is why a module whose body can skip imports this
instead of writing its own loop. A module that never skips or raises could write
its own and be correct; it would just be correct by an argument nobody re-checks.
"""

from __future__ import annotations

import sys
from typing import Any, Callable, Dict, Optional


class _Raises:
    """Minimal `pytest.raises` stand-in, so the file runs without pytest."""

    def __init__(self, expected):
        self.expected = expected
        self.exception: Optional[Exception] = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc is None:
            raise AssertionError(f"expected {self.expected.__name__} to be raised")
        if not isinstance(exc, self.expected):
            return False
        self.exception = exc
        return True


def raises(expected):
    """`pytest.raises`, for a file that has to work when pytest is not installed.

    Weaker than pytest's on purpose: it does not match on `match=` and does not
    keep a traceback. Both would need to be reimplemented to mean the same thing,
    and what these tests assert is the exception type -- the message is for a human
    reading a failure, and duplicating it here would make the assertion fail twice
    for one edit.
    """
    return _Raises(expected)


def skipped(error: BaseException) -> Optional[str]:
    """The skip reason if `error` means "cannot run here", else None.

    Checked as a *class name* rather than by import: pytest's `Skipped` is not
    importable without pytest, which is the whole situation this file exists for.
    `unittest.SkipTest` is reachable but is a different class from pytest's, so a
    single `isinstance` against either one misses the other.
    """
    for klass in type(error).__mro__:
        if klass.__name__ in {"Skipped", "SkipTest"}:
            return str(error) or "skipped"
    return None


def run(namespace: Dict[str, Any], *, origin: str = "") -> int:
    """Every `test_*` callable in `namespace`; returns the process exit code.

    A skip prints as `skip` and is excluded from the denominator, because a red
    line for something this interpreter or machine cannot do teaches people to
    ignore red lines -- and the checks worth reading are the ones that stayed red
    for a reason.

    `origin` names the file in the summary line; it comes from `__name__` at the
    call site so the runner does not have to guess how it was invoked.
    """
    every = [
        (name, made)
        for name, made in sorted(namespace.items())
        if name.startswith("test_") and callable(made)
    ]
    if not every:
        print(f"{origin}: no tests found -- that is not a pass")
        return 1
    failed = skipped_count = 0
    for name, made in every:
        try:
            made()
            print(f"ok   {name}")
        except BaseException as error:  # noqa: BLE001 - the runner's whole job
            reason = skipped(error)
            if reason is not None:
                skipped_count += 1
                print(f"skip {name}: {reason}")
                continue
            failed += 1
            print(f"FAIL {name}: {type(error).__name__}: {error}")
    ran = len(every) - skipped_count
    tail = f" ({skipped_count} skipped)" if skipped_count else ""
    where = f" in {origin}" if origin else ""
    print(f"\n{ran - failed}/{ran} passed{tail}{where}")
    return 1 if failed else 0


def main(namespace: Dict[str, Any]) -> None:
    """The one line a module's `__main__` needs: `sys.exit(_support.main(globals()))`."""
    sys.exit(run(namespace, origin=namespace.get("__file__", "")))
