"""Tab parsing and timeline module.

The LOADER is imported lazily, and that is a fix rather than a tidy-up.
`loader` needs `guitarpro`, so an eager import here made every
`pickhero.tabs.*` submodule need it too -- including `downloader` and
`songsterr`, which touch no GP file at all. A player running
`tools/songsterr_fields.py` to find out why a SEARCH came back empty got
`ModuleNotFoundError: No module named 'guitarpro'` instead: a network
diagnostic refusing to run for want of a tab parser, in exactly the case
where somebody reaches for it.

`timeline` stays eager -- it is pure arithmetic with no dependency of its
own, and the dataclasses are what everything else is typed against.
"""

from pickhero.tabs.timeline import NoteEvent, SongMetadata, Timeline

__all__ = [
    "NoteEvent",
    "SongMetadata",
    "Timeline",
    "load_gp_file",
    "list_tracks",
]


def __getattr__(name: str):
    """`pickhero.tabs.load_gp_file` still works, and still needs guitarpro.

    PEP 562: this runs only for a name the module does not already have, so
    the two loader functions cost their import when somebody asks for one
    and never when a sibling module is imported.
    """
    if name in ("load_gp_file", "list_tracks"):
        from pickhero.tabs import loader
        return getattr(loader, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
