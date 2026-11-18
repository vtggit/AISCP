"""The per-context read cache: one read per call of the exceptions list.

The exceptions call (``app.api.exceptions``) gathers its sixteen
sources in one call, and several of the sources read the very same
rows — the newest open-purchase snapshot
(``OpenPurchaseLinePostgresRepository.list_latest``), the reorder
recommendations (``app.recommend.reorder.recommendations_for_all``)
with the demand statistics
(``app.stats.demand.demand_stats_for_all``) and the 30-day history
buckets (``app.forecast.history.history_for_all``) they load, the
ranked stockout rows
(``app.recommend.stockout.stockout_ranked``), the supplier
performance (``app.recommend.supplier_performance.supplier_performance``)
and the lead-time observations
(``LeadTimeObservationPostgresRepository.list_rows``).  The cache
answers each of those reads once per call: while a ``read_once()``
block is active — held in a ``contextvars.ContextVar`` for the
block's duration — a function decorated with ``cached_read`` (a
module function) or ``cached_read_method`` (a method, ``self`` left
out of the key so the instances of one repository class share the
entries) answers every repeated call with the same arguments from
the first call's result — a ``copy.deepcopy`` of it, so a caller
that changes what it was given changes nothing another caller sees —
and runs the function itself on the first call for an argument
combination.  The key of a call is the function's module and
qualified name plus its positional and keyword arguments, the
keyword arguments in sorted order; a call with any argument that is
not hashable calls through, uncached, and a call that raises stores
nothing, so the next call calls through again.  A nested
``read_once()`` block shares the outer block's cache, the cache is
gone when the outermost block exits (an exception included), and
outside any block a decorated function calls through, exactly as
without this module.  Only the exceptions call runs its sources
inside a ``read_once()`` block, so it is the only caller that ever
answers a read from the cache.
"""

from __future__ import annotations

import contextvars
import copy
import functools
from collections.abc import Generator
from contextlib import contextmanager

# The per-context cache of the first result of each decorated read,
# keyed by the key the decorators build.  ``None`` (the default)
# means no ``read_once()`` block is active: the decorated functions
# then call through on every call.  Each context runs in its own
# block, so two concurrent callers never share a cache.
_read_cache: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "aierp_read_cache", default=None
)


@contextmanager
def read_once() -> Generator[None, None, None]:
    """The block in which the decorated reads cache their first result.

    While the block is active the cache is held in the ``_read_cache``
    context variable: a decorated function answers every repeated
    call with the same arguments from the first call's result, a
    deep copy of it, and runs the function itself on the first call
    for an argument combination.  A nested block shares the outer
    block's cache, the cache is gone when the outermost block exits
    (an exception included), and outside any block a decorated
    function calls through, exactly as without this module.
    """
    cache = _read_cache.get()
    if cache is None:
        cache = {}
    token = _read_cache.set(cache)
    try:
        yield
    finally:
        _read_cache.reset(token)


def _key(func, args: tuple, kwargs: dict) -> tuple:
    """The cache key of one call: the function's module and
    qualified name plus its positional and keyword arguments, the
    keyword arguments in sorted order.  ``self`` is left out by the
    caller, never here."""
    return (
        func.__module__,
        func.__qualname__,
        args,
        tuple(sorted(kwargs.items())),
    )


def _cached_call(func, self, args: tuple, kwargs: dict):
    """One call of a decorated function, through the active cache.

    Outside any ``read_once()`` block, or with any argument that is
    not hashable, the call goes straight to the function, uncached.
    Inside, the first call for an argument combination runs the
    function and stores a deep copy of its result, and every later
    call for it answers a deep copy of the stored result — a caller
    that changes what it was given changes nothing another caller
    sees.  A call that raises stores nothing, so the next call calls
    through.
    """
    if self is None:

        def call():
            return func(*args, **kwargs)

    else:

        def call():
            return func(self, *args, **kwargs)

    cache = _read_cache.get()
    if cache is None:
        return call()
    key = _key(func, args, kwargs)
    try:
        if key in cache:
            return copy.deepcopy(cache[key])
    except TypeError:
        # An unhashable argument: this call is never cached.
        return call()
    result = call()
    cache[key] = copy.deepcopy(result)
    return result


def cached_read(func):
    """Cache the repeated reads of a module function.

    While a ``read_once()`` block is active, a decorated function
    answers every repeated call with the same positional and keyword
    arguments — the keyword arguments in sorted order, the key the
    function's module and qualified name plus the arguments — from
    the first call's result, a ``copy.deepcopy`` of it, and runs the
    function itself on the first call for an argument combination.
    A call with any argument that is not hashable calls through,
    uncached, and a call that raises stores nothing, so the next call
    calls through again.  Outside any block the decorated function
    calls through, exactly as without this decorator.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return _cached_call(func, None, args, kwargs)

    return wrapper


def cached_read_method(method):
    """Cache the repeated reads of a method.

    Like ``cached_read`` for a method, with ``self`` left out of the
    key: the instances of one repository class share the entries, so
    every instance answers a repeated call with the same arguments
    from the first call's result, a ``copy.deepcopy`` of it, while a
    distinct method of another class keeps its own entries.  A call
    with any argument that is not hashable calls through, uncached,
    and a call that raises stores nothing, so the next call calls
    through again.  Outside any ``read_once()`` block the decorated
    method calls through, exactly as without this decorator.
    """

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        return _cached_call(method, self, args, kwargs)

    return wrapper
