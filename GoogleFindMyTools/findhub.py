"""Find Hub integration — the only module that depends on GoogleFindMyTools.

Wraps GoogleFindMyTools' sign-in and decryption to turn one tag's latest
reports into plain ``{t, lat, lng, acc, src}`` points. Keeping every GFMT
import in this one module lets the rest of the package run (and be tested)
without the GFMT checkout on the path.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys

from config import get_settings

# Python 3.14's stricter asyncio SSL raises noisy but harmless errors when the
# FCM push connection is torn down between fetches (APPLICATION_DATA_AFTER_
# CLOSE_NOTIFY). The client reconnects on its own, so quiet the teardown traces.
for _name in ("Auth.firebase_messaging", "Auth.firebase_messaging.fcmpushclient"):
    logging.getLogger(_name).setLevel(logging.CRITICAL)

# Make the GoogleFindMyTools checkout importable before pulling its modules in.
sys.path.insert(0, get_settings().gfmt_dir)

from NovaApi.ListDevices.nbe_list_devices import request_device_list  # noqa: E402
from NovaApi.ExecuteAction.LocateTracker.location_request import (  # noqa: E402
    get_location_data_for_device,
)
from ProtoDecoders.decoder import (  # noqa: E402
    parse_device_list_protobuf,
    get_canonic_ids,
)
import NovaApi.ExecuteAction.LocateTracker.decrypt_locations as gfmt_decrypt  # noqa: E402
import NovaApi.ExecuteAction.LocateTracker.location_request as gfmt_locreq  # noqa: E402
from ProtoDecoders import DeviceUpdate_pb2, Common_pb2  # noqa: E402

_STATUS_NAMES = {
    Common_pb2.Status.LAST_KNOWN: "last_known",
    Common_pb2.Status.CROWDSOURCED: "crowdsourced",
    Common_pb2.Status.AGGREGATED: "aggregated",
}


def find_canonic_id(tag_name: str) -> tuple[str, str]:
    """Return the ``(name, canonic_id)`` whose name contains ``tag_name``."""
    device_list = parse_device_list_protobuf(request_device_list())
    ids = get_canonic_ids(device_list)
    for name, canonic_id in ids:
        if tag_name.lower() in name.lower():
            return name, canonic_id
    raise SystemExit(
        "Tag %r not found. Trackers on your account:\n  %s"
        % (tag_name, "\n  ".join(n for n, _ in ids) or "(none)")
    )


def collect_points(canonic_id: str, name: str) -> list[dict]:
    """Ask Find Hub for this tag and return a list of structured points."""
    captured: dict = {}
    # Patch the symbol in location_request's namespace — that module binds
    # decrypt_location_response_locations by value at import, so patching it on
    # decrypt_locations alone would not affect the call it makes.
    original = gfmt_locreq.decrypt_location_response_locations

    def capture(device_update):
        captured["update"] = device_update
        try:
            return original(device_update)  # keep GFMT's own validation path
        except Exception:
            return None

    gfmt_locreq.decrypt_location_response_locations = capture
    try:
        get_location_data_for_device(canonic_id, name)
    finally:
        gfmt_locreq.decrypt_location_response_locations = original

    update = captured.get("update")
    return _decode_update(update) if update is not None else []


def _decode_update(update) -> list[dict]:
    registration = update.deviceMetadata.information.deviceRegistration
    identity_key = gfmt_decrypt.retrieve_identity_key(registration)
    is_mcu = gfmt_decrypt.is_mcu_tracker(registration)
    reports = (
        update.deviceMetadata.information.locationInformation.reports
        .recentLocationAndNetworkLocations
    )

    locations = list(reports.networkLocations)
    timestamps = list(reports.networkLocationTimestamps)
    if reports.HasField("recentLocation"):
        locations.append(reports.recentLocation)
        timestamps.append(reports.recentLocationTimestamp)

    points: list[dict] = []
    for loc, stamp in zip(locations, timestamps):
        if loc.status == Common_pb2.Status.SEMANTIC:
            continue  # named place, no coordinates
        enc = loc.geoLocation.encryptedReport
        try:
            if enc.publicKeyRandom == b"":  # own report
                key_hash = hashlib.sha256(identity_key).digest()
                plain = gfmt_decrypt.decrypt_aes_gcm(key_hash, enc.encryptedLocation)
            else:
                offset = 0 if is_mcu else loc.geoLocation.deviceTimeOffset
                plain = gfmt_decrypt.decrypt(
                    identity_key, enc.encryptedLocation, enc.publicKeyRandom, offset
                )
        except Exception:
            continue
        proto = DeviceUpdate_pb2.Location()
        proto.ParseFromString(plain)
        points.append(
            {
                "t": int(stamp.seconds),
                "lat": proto.latitude / 1e7,
                "lng": proto.longitude / 1e7,
                "acc": round(float(loc.geoLocation.accuracy), 1),
                "src": "own"
                if enc.isOwnReport
                else _STATUS_NAMES.get(loc.status, ""),
            }
        )
    return points
