"""Resolver plugins.

Importing this package side-effect-registers every shipped resolver via
``base.register``.  Adding a new host = drop a new module in here and decorate
the class with ``@register``.
"""
from . import base  # noqa: F401
# Order doesn't matter for correctness, but earlier entries win on ties.
from . import (  # noqa: F401
    direct,
    bunkr,
    cyberdrop,
    jpgsu,
    pixl,
    pixhost,
    pixeldrain,
    imgbox,
    ibb,
    redgifs,
    gofile,
    saint2,
    turbo,
    cyberfile,
    filester,
    anonfiles,
    stubs,
)
