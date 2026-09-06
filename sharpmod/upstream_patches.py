"""Local repairs to pinned upstream code, applied when that code is loaded.

A sibling of :mod:`sharpmod.upstream_warnings`, and for the same reason: a
dependency has a defect we have reported and fixed upstream, but waiting for the
fix to reach a release would leave the symptom in front of our users for months.

Every patch here has to satisfy three things, because reaching into someone
else's library is a debt and not a solution:

1. It names the upstream report, so the reason is discoverable.
2. It stands down on its own once the installed version carries the real fix,
   so upgrading the dependency is what retires it rather than us remembering to.
3. It is applied at one chokepoint, never at import of this module, so nothing
   is altered for code that does not go through us.
"""

from __future__ import annotations

import functools
import logging

_LOGGER = logging.getLogger(__name__)

#: Where the real fix for the Herbie source search lives.
HERBIE_SOURCE_FALLBACK_PR = "https://github.com/blaylockbk/Herbie/pull/554"
HERBIE_SOURCE_FALLBACK_ISSUE = "https://github.com/blaylockbk/Herbie/issues/246"

#: Attribute stamped on a patched class, so applying twice is a no-op.
_STAMP = "_sharpmod_source_fallback"

#: Present on a Herbie that already isolates each source itself. When the fix
#: lands upstream this attribute appears and the patch below declines to apply.
_UPSTREAM_MARKER = "_sign_azure_url"


def _priority_ordered_sources(herbie) -> dict:
    """The sources Herbie will actually search, in the order it will search them.

    Mirrors the filter at the top of ``find_grib``: a source the caller excluded
    through ``priority`` must not be reintroduced by us trying it separately.
    """
    sources = dict(getattr(herbie, "SOURCES", None) or {})
    priority = getattr(herbie, "priority", None)
    if priority is None:
        return sources
    if isinstance(priority, str):
        priority = [priority]
    return {name: sources[name] for name in priority if name in sources}


def _isolate_per_source(original, what: str):
    """Wrap a Herbie search so one failing mirror does not end the search.

    Herbie's own loop shares a single ``try`` with every source, so an exception
    from one of them -- a TLS failure behind a proxy, a refused connection, or
    Azure declining to issue a SAS token, which raises ``KeyError`` because the
    rejection still arrives as HTTP 200 -- leaves the loop and abandons the
    mirrors behind it. The file is then reported missing when it was there all
    along, and for several models Azure is *first* in the list, so it takes the
    working mirrors down with it.

    Rather than reimplement the search, this calls Herbie's own method once per
    source with ``SOURCES`` narrowed to that one. The per-source logic stays
    upstream's, which is what keeps this from rotting against their changes; all
    this adds is that a raise ends one attempt instead of all of them.
    """

    @functools.wraps(original)
    def search(self, *args, **kwargs):
        sources = _priority_ordered_sources(self)
        if len(sources) < 2:
            # Nothing to fall back to, so add no behaviour: a single-source
            # search should raise exactly what it raises today.
            return original(self, *args, **kwargs)

        skipped = []
        for name, url in sources.items():
            self.SOURCES = {name: url}
            try:
                found, source = original(self, *args, **kwargs)
            except Exception as error:
                skipped.append(name)
                _LOGGER.warning(
                    "herbie.source_failed source=%s what=%s error=%r "
                    "(trying the next source; see %s)",
                    name, what, error, HERBIE_SOURCE_FALLBACK_PR,
                )
                continue
            finally:
                # Restored every time: Herbie narrows SOURCES itself, and the
                # object outlives this call.
                self.SOURCES = sources
            if found is not None:
                return (found, source)

        if skipped:
            _LOGGER.warning(
                "herbie.all_sources_failed what=%s skipped=%s",
                what, ",".join(skipped),
            )
        # Same empty answer Herbie gives for a file that genuinely is not there.
        return (None, None)

    return search


def apply_herbie_source_fallback(herbie_class) -> bool:
    """Let a Herbie search survive one unreachable mirror. Returns whether applied.

    Declines when the installed Herbie already isolates its sources, and when the
    class has been patched already, so this is safe to call from every entry
    point that loads Herbie.

    See :data:`HERBIE_SOURCE_FALLBACK_ISSUE` for the report and
    :data:`HERBIE_SOURCE_FALLBACK_PR` for the fix this stands in for. Note that
    it covers the giving-up-early failures only: Herbie's SAS token request
    carries no timeout, and a token service that accepts the connection and then
    never answers still stalls until the caller cancels. That one cannot be
    reached from out here without replacing the method body.
    """
    if herbie_class is None:
        return False
    if getattr(herbie_class, _STAMP, False):
        return False
    if hasattr(herbie_class, _UPSTREAM_MARKER):
        _LOGGER.debug(
            "herbie.source_fallback_not_needed reason=fix_present_upstream")
        setattr(herbie_class, _STAMP, True)
        return False

    patched = []
    for name, what in (("find_grib", "grib"), ("find_idx", "index")):
        original = getattr(herbie_class, name, None)
        if not callable(original):
            continue
        setattr(herbie_class, name, _isolate_per_source(original, what))
        patched.append(name)

    if not patched:
        # An unrecognised Herbie. Leaving it alone is the safe answer; the
        # symptom is a failed download, not a wrong sounding.
        _LOGGER.warning(
            "herbie.source_fallback_skipped reason=no_search_methods_found")
        return False

    setattr(herbie_class, _STAMP, True)
    _LOGGER.debug("herbie.source_fallback_applied methods=%s",
                  ",".join(patched))
    return True
