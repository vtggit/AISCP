"""Publish the guidance the way the deployment does: from standard input.

``python -m app.guidance_publish``, run in the backend's own
environment, reads one JSON document from standard input and publishes
it exactly the way ``PUT /api/guidance`` does: the same checks, the
same comparison with what is current, the same rows, in one
transaction.  The only difference is who published: ``published_by``
is the string ``deployment``, because there is no signed-in caller —
the command opens no HTTP connection and reads no token.

The command and the route run the same code for the checks and for
the storing, so the rules are written once: ``validate_body`` here
holds the checks (the raw-body storeability walk and the body model's
own validation, the same walk the route's guard runs) and ``publish``
here holds the storing (the repository's one transaction).  ``main``
runs the command: any stream left as ``None`` is the process's own.

* published or unchanged — exactly one line on standard output, a
  JSON object with the five keys the route answers (``changed``,
  ``publication_id``, ``items``, ``new_versions``, ``withdrawn``),
  nothing on standard error, exit code 0;
* refused — standard input is not JSON, or is a body the route would
  refuse with a 422 (including one that holds a non-finite number):
  nothing is written to the database, nothing on standard output, one
  line on standard error starting with ``refused:``, exit code 2;
* failed — anything else went wrong while publishing (for example the
  database cannot be reached): nothing on standard output, one line on
  standard error starting with ``failed:``, exit code 1 — the command
  never ends with a traceback.
"""

import contextlib
import json
import math
import sys

from app.models.guidance import GuidanceIn
from app.repositories.guidance_postgres_repository import (
    GuidancePostgresRepository,
)

# The published_by the deployment command stores: there is no
# signed-in caller, so the caller's username is this string.
PUBLISHED_BY = "deployment"

_repo = GuidancePostgresRepository()


class GuidanceRefused(Exception):
    """A body the route would refuse with a 422.

    The reason (one line) rides in the message: what the route's
    answer would say, in the command's ``refused:`` line instead of
    the 422 envelope.
    """


def find_unstoreable(value):
    """The first value a 422 envelope could not echo back, or None.

    FastAPI's HTTPValidationError envelope echoes each rejected value
    back as the error's ``input`` — a missing-field error echoes the
    whole body, keys included — and the response serializer renders
    it with ``ensure_ascii=False`` and ``allow_nan=False`` before
    UTF-8 encoding.  Two kinds of hostile value break that rendering
    and would turn the 422 into a 500: a non-finite float (a JSON
    ``Infinity``/``NaN`` token, or a number so large it overflows to
    one on parse, such as 1e400) and a string holding a lone
    surrogate (the JSON string ``"\\ud800"``), which cannot be
    encoded as UTF-8 at all — as a body value or as a JSON key.  The
    body model's own 422 would echo such a value, so this guard must
    refuse it first; this walks the parsed body, keys included, and
    returns ``(loc, message)`` for the first such value.  The route's
    raw-body guard and the deployment command's checks both run this
    walk, so the rule is written once.
    """

    def _loc_part(key: object) -> object:
        if isinstance(key, str):
            try:
                key.encode("utf-8")
            except UnicodeEncodeError:
                # A lone surrogate in a key would ride along in the
                # error's loc and break the envelope's own rendering.
                return key.encode("unicode_escape").decode("ascii")
        return key

    def walk(node, loc):
        if isinstance(node, dict):
            for key, child in node.items():
                if isinstance(key, str):
                    try:
                        key.encode("utf-8")
                    except UnicodeEncodeError:
                        return (
                            loc + [_loc_part(key)],
                            "must be a UTF-8 storeable string",
                        )
                found = walk(child, loc + [_loc_part(key)])
                if found is not None:
                    return found
            return None
        if isinstance(node, list):
            for index, child in enumerate(node):
                found = walk(child, loc + [index])
                if found is not None:
                    return found
            return None
        if isinstance(node, float) and not math.isfinite(node):
            return loc, "must be a finite number"
        if isinstance(node, str):
            try:
                node.encode("utf-8")
            except UnicodeEncodeError:
                return loc, "must be a UTF-8 storeable string"
        return None

    return walk(value, ["body"])


def _one_line(text: str) -> str:
    """The text as one line (any newlines and runs of white space folded)."""
    return " ".join(text.split())


def _refusal_from_validation_error(exc: Exception) -> str:
    """The first of the body model's validation errors, as one line."""
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            first = errors()[0]
        except Exception:
            first = None
    else:
        first = None
    if isinstance(first, dict):
        loc = ".".join(str(part) for part in first.get("loc", ()))
        message = str(first.get("msg", ""))
        text = f"{loc}: {message}" if loc else message
        return _one_line(text)
    return _one_line(str(exc))


def validate_body(raw: str) -> GuidanceIn:
    """The route's checks on one raw body, before anything is written.

    The raw text is parsed as JSON, the parsed body is walked for the
    values the 422 envelope could not echo back (a non-finite number
    anywhere in it, or a lone surrogate in a value or a key) and the
    parsed body is then validated by the body model's own rules — the
    same checks the route runs, in the same order.  Raises
    ``GuidanceRefused`` — the 422 the route would answer — when the
    raw text is not JSON or the body breaks any rule.
    """
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise GuidanceRefused(f"not a JSON document: {exc}") from None
    found = find_unstoreable(payload)
    if found is not None:
        loc, message = found
        raise GuidanceRefused(
            f"{'.'.join(str(part) for part in loc)}: {message}"
        ) from None
    try:
        return GuidanceIn.model_validate(payload)
    except Exception as exc:
        raise GuidanceRefused(_refusal_from_validation_error(exc)) from None


def publish(body: GuidanceIn, published_by: str) -> dict:
    """The route's storing, run on the validated body.

    The comparison with what is current, the rows and the single
    transaction all live in the repository: when nothing changed
    nothing is written; otherwise one new ``guidance_publications``
    row (``source`` as sent, ``published_by`` the caller) and, per
    item, a bump of the matching current row's
    ``last_publication_id`` or a new ``guidance_versions`` row.
    Returns the five answer keys.
    """
    return _repo.publish(
        source=body.source,
        items=[item.model_dump() for item in body.items],
        published_by=published_by,
    )


def _report_failure(stderr, exc: BaseException) -> None:
    """One line starting with ``failed:`` on ``stderr`` — never a traceback."""
    try:
        text = f"failed: {type(exc).__name__}: {exc}"
        stderr.write(_one_line(text) + "\n")
    except Exception:
        pass


def main(stdin=None, stdout=None, stderr=None) -> int:
    """Run the command and return its exit code.

    Any stream left as ``None`` is the process's own stream.  One JSON
    document is read from ``stdin`` and published the way the route
    publishes it, with ``published_by`` the string ``deployment``: 0
    when published or unchanged (exactly one line on ``stdout``, the
    five-key answer, nothing on ``stderr``), 2 when refused (nothing
    on ``stdout``, one line on ``stderr`` starting with ``refused:``)
    and 1 when anything else went wrong (nothing on ``stdout``, one
    line on ``stderr`` starting with ``failed:``) — never a traceback.
    """
    if stdin is None:
        stdin = sys.stdin
    if stdout is None:
        stdout = sys.stdout
    if stderr is None:
        stderr = sys.stderr
    try:
        raw = stdin.read()
    except Exception as exc:
        _report_failure(stderr, exc)
        return 1
    try:
        body = validate_body(raw)
    except GuidanceRefused as exc:
        with contextlib.suppress(Exception):
            stderr.write(f"refused: {exc}\n")
        return 2
    except Exception as exc:
        _report_failure(stderr, exc)
        return 1
    try:
        answer = publish(body, PUBLISHED_BY)
        stdout.write(json.dumps(answer, ensure_ascii=False) + "\n")
    except Exception as exc:
        _report_failure(stderr, exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
