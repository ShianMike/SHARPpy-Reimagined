"""Requested-versus-loaded ensemble accounting contracts."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sharpmod.ensemble_members import EnsembleAcquisition, MemberFailure


def _spec(member):
    return SimpleNamespace(
        request_id=f"gefs-{member}",
        model="gefs",
        label="GEFS",
        run_time="2026-09-13T00:00:00+00:00",
        fxx=6,
        member=member,
    )


def test_batch_ledger_distinguishes_loaded_failed_cancelled_and_unknown():
    specs = tuple(_spec(member) for member in ("c00", "p01", "p02", "p03"))
    result = SimpleNamespace(
        items=(
            SimpleNamespace(id="gefs-c00", status="completed", error=None),
            SimpleNamespace(
                id="gefs-p01",
                status="failed",
                error={"message": "HTTP 503"},
            ),
            SimpleNamespace(id="gefs-p02", status="cancelled", error=None),
        )
    )

    ledger = EnsembleAcquisition.from_batch(specs, result)

    assert ledger.requested_count == 4
    assert ledger.loaded_members == ("c00",)
    assert ledger.failed_members == ("p01",)
    assert ledger.cancelled_members == ("p02",)
    assert ledger.unknown_members == ("p03",)
    assert [item["member"] for item in ledger.specs_for_retry()] == [
        "p01",
        "p02",
        "p03",
    ]
    assert ledger.failure_for("p01").reason == "HTTP 503"
    assert [item["member"] for item in ledger.specs_for_members(("p02", "c00"))] == [
        "c00",
        "p02",
    ]


def test_ledger_rejects_loaded_member_outside_original_request():
    with pytest.raises(ValueError, match="original request"):
        EnsembleAcquisition(("c00",), ("p01",))


def test_member_cannot_be_loaded_and_failed():
    with pytest.raises(ValueError, match="both loaded and unavailable"):
        EnsembleAcquisition(
            ("c00",),
            ("c00",),
            (MemberFailure("c00", "failed"),),
        )


def test_upstream_unnamed_ordinary_profile_is_not_an_ensemble_request():
    collection = SimpleNamespace(_profs={"": [object()]}, _meta={})
    ledger = EnsembleAcquisition.from_collection(collection)

    assert ledger.requested_count == ledger.loaded_count == 0
    assert not ledger.failures
    assert collection._meta == {}

    collection._meta["ensemble"] = True
    with pytest.raises(ValueError, match="non-empty"):
        EnsembleAcquisition.from_collection(collection)
