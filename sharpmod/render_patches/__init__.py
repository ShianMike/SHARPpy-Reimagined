"""Panel-focused SHARPpy render patch implementations."""

from sharpmod.render_patches.hodograph import install_hodo_label_fit
from sharpmod.render_patches.text_panels import (
    install_fire_text_fit,
    install_winter_text_fit,
)

__all__ = [
    "install_fire_text_fit",
    "install_hodo_label_fit",
    "install_winter_text_fit",
]
