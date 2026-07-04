"""SHARPpy Reimagined input decoders and the decoder registry.

The registry (:mod:`sharpmod.io.decoder`) discovers built-in decoders and any
user-supplied custom decoders, and also provides the ``.npz`` point-sounding
loader that preserves the OMEGA vertical-velocity column.
"""

__all__ = ["decoder", "uwyo_decoder", "uwyo_catalog"]
