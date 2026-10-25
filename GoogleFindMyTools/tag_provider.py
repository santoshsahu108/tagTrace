"""Isolated access to Find Hub with one user's credentials.

GoogleFindMyTools keeps its credentials and FCM connection in process-global
state (``Auth.token_cache``, the ``FcmReceiver`` singleton). To guarantee one
user's credentials are never reused for another, every Google-facing operation
for a user runs in its *own* process:

* ``connect_google`` — child that opens Chrome on this computer for the user
  to sign in to Google, collects the keys, and returns the user's trackers.
* ``list_devices`` — short-lived child that validates existing credentials
  (used by the legacy secrets.json import).
* ``worker.run_worker`` — long-lived child that polls one user's tag.

``prepare_process`` is what both call first inside the child: it loads the
credentials into GFMT's in-memory store and disables the interactive (Chrome)
login, which can't run on a server.
"""

from __future__ import annotations

import multiprocessing as mp
import traceback

# Keys a working GoogleFindMyTools login produces. fcm_credentials and
# owner_key are re-created automatically, so they are optional.
REQUIRED_KEYS = ("username", "aas_token", "shared_key")


class SetupError(Exception):
    """A setup/validation failure with a stable machine-readable ``code``.

    Codes: missing_fields, invalid_credentials, network, timeout, provider,
    no_trackers, unknown_tag, already_configured, setup_in_progress, no_setup,
    no_browser, cancelled.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class CredentialsExpired(Exception):
    """Google rejected the stored credentials; the user must reconnect."""


def check_required_keys(credentials: dict) -> None:
    """Raise SetupError if the uploaded secrets miss a key we can't recreate."""
    if not isinstance(credentials, dict):
        raise SetupError("missing_fields", "credentials must be a JSON object")
    missing = [k for k in REQUIRED_KEYS if not credentials.get(k)]
    if missing:
        raise SetupError(
            "missing_fields",
            "secrets.json is missing: " + ", ".join(missing)
            + ". Run the Google login (python main.py) on a computer with Chrome "
            "and upload the new file.",
        )


def _no_interactive_login(*_args, **_kwargs):
    raise CredentialsExpired(
        "Google login needs to be redone in Chrome; reconnect the tag"
    )


def prepare_process(credentials: dict, on_change=None) -> None:
    """Load ``credentials`` into GFMT for this process; never open Chrome."""
    from Auth import token_cache

    token_cache.use_memory_store(credentials, on_change)

    # Missing/expired tokens would normally open a Chrome window. Replace those
    # entry points so the failure surfaces as CredentialsExpired instead.
    import Auth.aas_token_retrieval as aas
    import KeyBackup.shared_key_retrieval as shared

    aas.request_oauth_account_token_flow = _no_interactive_login
    shared.request_shared_key_flow = _no_interactive_login


def classify_error(exc: BaseException) -> tuple[str, str]:
    """Map a provider exception to ``(code, user-facing message)``."""
    import requests

    if isinstance(exc, CredentialsExpired):
        return "invalid_credentials", str(exc)
    # gpsoauth returns {"Error": "BadAuthentication"} without an "Auth" key.
    if isinstance(exc, KeyError) and exc.args and exc.args[0] == "Auth":
        return "invalid_credentials", (
            "Google rejected these credentials (expired or revoked). "
            "Redo the Google login and upload a new secrets.json."
        )
    if isinstance(exc, (requests.ConnectionError, requests.Timeout, OSError)):
        return "network", "Couldn't reach Google. Check the internet connection and retry."
    return "provider", f"Find Hub request failed: {type(exc).__name__}"


def _list_devices_child(credentials: dict, conn) -> None:
    updated: dict = {}

    def remember(store: dict) -> None:
        updated.clear()
        updated.update(store)

    try:
        prepare_process(credentials, remember)
        from NovaApi.ListDevices.nbe_list_devices import request_device_list
        from ProtoDecoders.decoder import get_canonic_ids, parse_device_list_protobuf

        raw = request_device_list()
        if not raw:
            raise RuntimeError("empty device list response")
        devices = [
            {"id": canonic_id, "name": name}
            for name, canonic_id in get_canonic_ids(parse_device_list_protobuf(raw))
        ]
        conn.send({"ok": True, "devices": devices, "credentials": updated or credentials})
    except BaseException as exc:  # report every failure to the parent
        code, message = classify_error(exc)
        conn.send({"ok": False, "code": code, "message": message,
                   "detail": traceback.format_exception_only(type(exc), exc)[-1].strip()})
    finally:
        conn.close()


def list_devices(credentials: dict, timeout: float) -> tuple[list[dict], dict]:
    """Validate credentials against Find Hub in a child process.

    Returns ``(devices, credentials)`` where ``devices`` is
    ``[{"id", "name"}, ...]`` and ``credentials`` may include refreshed values
    (e.g. new FCM credentials). Raises ``SetupError`` on any failure.
    """
    check_required_keys(credentials)
    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_list_devices_child, args=(credentials, child), daemon=True)
    proc.start()
    child.close()
    try:
        if not parent.poll(timeout):
            raise SetupError("timeout", "Google didn't answer in time. Retry setup.")
        result = parent.recv()
    except EOFError:
        raise SetupError("provider", "validation process exited unexpectedly")
    finally:
        if proc.is_alive():
            proc.kill()
        proc.join(5)
        parent.close()
    if not result["ok"]:
        raise SetupError(result["code"], result["message"])
    if not result["devices"]:
        raise SetupError("no_trackers", "No trackers found on this Google account.")
    return result["devices"], result["credentials"]


# --------------------------------------------------------------------------- #
# "Connect with Google": interactive sign-in in Chrome on this computer
# --------------------------------------------------------------------------- #
def _oauth_token_via_chrome(progress) -> str:
    """GFMT's EmbeddedSetup sign-in, minus its console prompt."""
    from selenium.webdriver.support.ui import WebDriverWait

    from chrome_driver import create_driver

    progress("google_login", "Sign in to your Google account in the Chrome window that opened.")
    driver = create_driver()
    try:
        driver.get("https://accounts.google.com/EmbeddedSetup")
        WebDriverWait(driver, 600).until(lambda d: d.get_cookie("oauth_token") is not None)
        return driver.get_cookie("oauth_token")["value"]
    finally:
        driver.quit()


def _connect_child(conn) -> None:
    import signal
    import sys

    # Turn SIGTERM into SystemExit so `finally: driver.quit()` closes Chrome
    # when the parent gives up (timeout, collector shutdown).
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(1))

    def progress(step: str, message: str) -> None:
        conn.send({"type": "progress", "step": step, "message": message})

    store: dict = {}
    try:
        from Auth import token_cache

        token_cache.use_memory_store(store)

        import Auth.aas_token_retrieval as aas
        import KeyBackup.shared_key_retrieval as shared
        from KeyBackup.shared_key_flow import request_shared_key_flow

        aas.request_oauth_account_token_flow = lambda: _oauth_token_via_chrome(progress)

        def shared_key_via_chrome():
            progress("key_backup", "Sign in again in Chrome and unlock your encrypted "
                     "data with your phone's screen lock (PIN, pattern or password).")
            key = request_shared_key_flow()
            if not key:
                raise SetupError("cancelled", "The key backup step was closed before finishing.")
            return key

        shared._retrieve_shared_key = shared_key_via_chrome

        progress("starting", "Opening Chrome…")
        from Auth.aas_token_retrieval import get_aas_token
        from KeyBackup.shared_key_retrieval import get_shared_key
        from SpotApi.GetEidInfoForE2eeDevices.get_owner_key import get_owner_key

        get_aas_token()
        get_shared_key()
        progress("checking", "Signed in. Reading your trackers…")
        get_owner_key()

        from NovaApi.ListDevices.nbe_list_devices import request_device_list
        from ProtoDecoders.decoder import get_canonic_ids, parse_device_list_protobuf

        raw = request_device_list()
        if not raw:
            raise RuntimeError("empty device list response")
        devices = [
            {"id": canonic_id, "name": name}
            for name, canonic_id in get_canonic_ids(parse_device_list_protobuf(raw))
        ]
        conn.send({"type": "result", "ok": True, "devices": devices, "credentials": dict(store)})
    except BaseException as exc:
        if isinstance(exc, SetupError):
            code, message = exc.code, exc.message
        elif "chrome" in str(exc).lower() or "webdriver" in type(exc).__module__:
            code, message = "no_browser", (
                "Couldn't open Chrome on the computer running the collector. "
                "Run the collector on a computer with Chrome and a screen."
            )
        elif type(exc).__name__ == "TimeoutException":
            code, message = "timeout", "Sign-in took too long. Start again."
        else:
            code, message = classify_error(exc)
        conn.send({"type": "result", "ok": False, "code": code, "message": message})
    finally:
        conn.close()


def connect_google(progress, timeout: float = 1800) -> tuple[list[dict], dict]:
    """Run the interactive Google sign-in in a child process.

    ``progress(step, message)`` is called as the user moves through Chrome.
    Returns ``(devices, credentials)``; raises SetupError on failure.
    """
    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_connect_child, args=(child,), daemon=True)
    proc.start()
    child.close()
    try:
        while True:
            if not parent.poll(timeout):
                raise SetupError("timeout", "Sign-in took too long. Start again.")
            msg = parent.recv()
            if msg["type"] == "progress":
                progress(msg["step"], msg["message"])
                continue
            if not msg["ok"]:
                raise SetupError(msg["code"], msg["message"])
            if not msg["devices"]:
                raise SetupError("no_trackers", "No trackers found on this Google account.")
            return msg["devices"], msg["credentials"]
    except EOFError:
        raise SetupError("provider", "The sign-in process exited unexpectedly.")
    finally:
        if proc.is_alive():
            proc.terminate()  # lets the child close its Chrome window
            proc.join(15)
        if proc.is_alive():
            proc.kill()
        proc.join(5)
        parent.close()
