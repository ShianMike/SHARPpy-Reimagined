"""The automatic, site-specific radar tilt check."""

from sharpmod.bridge import params as p
from sharpmod.bridge.errors import BridgeError


def tilts(params):
    from sharpmod.providers import radar_inventory, radar_site

    p.keys(params, ("site_id", "product"), ("valid_time",))
    site_id = p.text(params, "site_id", 1, 16)
    if radar_site.site_by_id(site_id) is None:
        raise p.invalid("unknown radar site")
    product = p.choice(params, "product", radar_site.TILTABLE_PRODUCTS, "reflectivity")
    when = p.time(params, "valid_time") if params.get("valid_time") is not None else None
    try:
        return radar_inventory.availability(site_id, product, when)
    except radar_site.RadarSiteError as exc:
        raise BridgeError("OVERLAY_FETCH_FAILED", f"Radar tilts could not be checked: {exc}", True) from None


HANDLERS = {"radar.tilts": tilts}
