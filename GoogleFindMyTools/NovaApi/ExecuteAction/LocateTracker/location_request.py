#
#  GoogleFindMyTools - A set of tools to interact with the Google Find My API
#  Copyright © 2024 Leon Böttger. All rights reserved.
#

import asyncio
import os
import time

from Auth.fcm_receiver import FcmReceiver
from NovaApi.ExecuteAction.LocateTracker.decrypt_locations import decrypt_location_response_locations
from NovaApi.ExecuteAction.nbe_execute_action import create_action_request, serialize_action_request
from NovaApi.nova_request import nova_request
from NovaApi.scopes import NOVA_ACTION_API_SCOPE
from NovaApi.util import generate_random_uuid
from ProtoDecoders import DeviceUpdate_pb2
from ProtoDecoders.decoder import parse_device_update_protobuf
from example_data_provider import get_example_data

def create_location_request(canonic_device_id, fcm_registration_id, request_uuid):

    action_request = create_action_request(canonic_device_id, fcm_registration_id, request_uuid=request_uuid)

    # Random values, can be arbitrary
    action_request.action.locateTracker.lastHighTrafficEnablingTime.seconds = 1732120060
    action_request.action.locateTracker.contributorType = DeviceUpdate_pb2.SpotContributorType.FMDN_ALL_LOCATIONS

    # Convert to hex string
    hex_payload = serialize_action_request(action_request)

    return hex_payload


def get_location_data_for_device(canonic_device_id, name):

    print(f"[LocationRequest] Requesting location data for {name}...")

    result = None
    request_uuid = generate_random_uuid()

    def handle_location_response(response):
        nonlocal result
        device_update = parse_device_update_protobuf(response)

        if device_update.fcmMetadata.requestUuid == request_uuid:
            print("[LocationRequest] Location request successful. Decrypting locations...")
            result = parse_device_update_protobuf(response)
            #print_device_update_protobuf(response)

    receiver = FcmReceiver()
    fcm_token = receiver.register_for_location_updates(handle_location_response)

    try:
        hex_payload = create_location_request(canonic_device_id, fcm_token, request_uuid)
        nova_request(NOVA_ACTION_API_SCOPE, hex_payload)

        # The answer arrives as an FCM push. Don't wait forever if it never
        # comes (e.g. another client is using the same FCM credentials).
        timeout = float(os.environ.get("LOCATION_TIMEOUT", "120") or 120)
        deadline = time.monotonic() + timeout
        while result is None:
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"no location response via FCM within {timeout:.0f}s"
                )
            time.sleep(0.1)
    finally:
        # One callback per request; don't let them pile up across polls.
        if handle_location_response in receiver.location_update_callbacks:
            receiver.location_update_callbacks.remove(handle_location_response)

    decrypt_location_response_locations(result)

if __name__ == '__main__':
    get_location_data_for_device(get_example_data("sample_canonic_device_id"), "Test")