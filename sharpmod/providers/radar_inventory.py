"""Check Level II reflectivity cuts and published Level III velocity cuts."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from sharpmod.providers import radar_level3, radar_site

_WORKERS = ThreadPoolExecutor(max_workers=3, thread_name_prefix="radar-inventory")
# The two lowest cut groups were deliberately removed from the UI.
TILTS = tuple(cut for cut in radar_level3.TILTS if cut not in {"x", "y"})


def availability(site_id, product, valid_time=None, *, now=None, opener=None):
    site = radar_site.site_by_id(site_id)
    if site is None or product not in radar_site.TILTABLE_PRODUCTS:
        raise radar_site.RadarSiteError("unknown radar site or tiltable product")
    if product in radar_site.LEVEL2_PRODUCTS:
        from sharpmod.providers import radar_level2
        return radar_level2.availability(site.id, valid_time, product=product, now=now, opener=opener)
    reference = now or datetime.now(timezone.utc)
    reference = reference.replace(tzinfo=timezone.utc) if reference.tzinfo is None else reference.astimezone(timezone.utc)
    target = valid_time or reference
    target = target.replace(tzinfo=timezone.utc) if target.tzinfo is None else target.astimezone(timezone.utc)
    if not reference - timedelta(minutes=70) <= target <= reference + timedelta(minutes=1):
        raise radar_site.RadarSiteError("radar history is available only for the past 60 minutes")
    timeout, _ = radar_site._remote_limits()
    timeout = min(timeout, 8.0)
    call = opener or radar_site._default_opener
    family = "p99v"

    def check(cut):
        directory = f"{radar_level3.BASE}/DS.{family}{cut}/SI.{site.id.lower()}/"
        try:
            scans = radar_level3.directory_scans(directory, call, timeout)
        except radar_site.RadarSiteError:
            return {"tilt": cut, "status": "unknown"}
        recent = [date for date, _ in scans if reference - timedelta(minutes=65) <= date <= reference]
        available = any(target >= reference - timedelta(minutes=2) or abs(date - target) <= timedelta(minutes=10) for date in recent)
        return {"tilt": cut, "status": "available" if available else "unavailable"}

    results = list(_WORKERS.map(check, TILTS))
    return {"site_id": site.id, "product": product, "checked_at": reference.isoformat(timespec="seconds").replace("+00:00", "Z"), "tilts": results}
