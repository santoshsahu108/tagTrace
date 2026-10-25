"""Tests for per-user tag configuration, setup flow and user isolation.

Run against a disposable local Postgres database (never production):

    TEST_DATABASE_URL=postgresql+psycopg2://$USER@127.0.0.1:5432/tagtrace_test \\
        .venv/bin/python -m unittest discover -s tests -v

Google / Find Hub is never contacted: ``tag_provider.connect_google``,
``tag_provider.list_devices`` and the worker's fetch are mocked.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from cryptography.fernet import Fernet  # noqa: E402

# Configure BEFORE importing app modules (config is read once at import).
_DEFAULT_TEST_DB = (
    f"postgresql+psycopg2://{os.environ.get('USER', 'postgres')}"
    "@127.0.0.1:5432/tagtrace_test"
)
TEST_DB = os.environ.get("TEST_DATABASE_URL", _DEFAULT_TEST_DB)
if "neon.tech" in TEST_DB:
    raise SystemExit("Refusing to run tests against a Neon (production) database.")
_LEGACY_DIR = tempfile.mkdtemp()
os.environ.update(
    {
        "DATABASE_URL": TEST_DB,
        "TAG_CREDENTIALS_KEY": Fernet.generate_key().decode(),
        "LEGACY_TAG_OWNER": "",
        "SETUP_TIMEOUT": "5",
        "GFMT_SECRETS_FILE": os.path.join(_LEGACY_DIR, "secrets.json"),
    }
)

import auth  # noqa: E402
import repository  # noqa: E402
import shares  # noqa: E402
import tag_configs  # noqa: E402
import tag_provider  # noqa: E402
from config import get_settings  # noqa: E402
from crypto import decrypt_credentials  # noqa: E402
from db import engine, init_database, session_scope  # noqa: E402
from models import Location, TagConfig  # noqa: E402
from sqlalchemy import func, select, text  # noqa: E402
from tag_provider import SetupError  # noqa: E402

SECRETS = {
    "username": "someone@gmail.com",
    "aas_token": "aas_et/FAKE-TOKEN-FOR-TESTS",
    "shared_key": "00" * 32,
    "owner_key": "11" * 32,
}
DEVICES = [{"id": "canonic-tag-1", "name": "Tag"}, {"id": "canonic-phone", "name": "Pixel"}]


def fake_list_devices(credentials, timeout):
    tag_provider.check_required_keys(credentials)
    return list(DEVICES), dict(credentials, fcm_credentials={"refreshed": True})


def fake_connect(progress, timeout=1800):
    progress("google_login", "Sign in to your Google account in Chrome.")
    progress("key_backup", "Unlock your encrypted data.")
    return list(DEVICES), dict(SECRETS, fcm_credentials={"new": True})


def _wait_connect(user_id: int, seconds: float = 5) -> dict:
    deadline = time.time() + seconds
    while time.time() < deadline:
        status = tag_configs.get_status(user_id)
        if not status.get("connect", {}).get("running"):
            return status
        time.sleep(0.05)
    raise AssertionError("connect job didn't finish")


def _reset_tables():
    tag_configs._jobs.clear()
    with engine.begin() as conn:
        conn.execute(text(
            "TRUNCATE locations, shares, sessions, tags, tag_configs, users "
            "RESTART IDENTITY CASCADE"
        ))


def _make_user(email: str, password: str = "pw-123456") -> int:
    auth.add_user(email, password)
    with session_scope() as s:
        from models import User
        return s.scalar(select(User.id).where(User.email == email))


def _active_count(user_id: int) -> int:
    with session_scope() as s:
        return s.scalar(
            select(func.count()).select_from(TagConfig).where(
                TagConfig.user_id == user_id, TagConfig.is_active.is_(True)
            )
        )


def _complete_setup(user_id: int) -> None:
    with mock.patch("tag_provider.list_devices", side_effect=fake_list_devices):
        tag_configs.begin_setup(user_id, SECRETS)
    tag_configs.select_tag(user_id, "canonic-tag-1")


def setUpModule():
    try:
        init_database()
    except SystemExit as exc:
        raise unittest.SkipTest(f"test database unavailable: {exc}")


class Base(unittest.TestCase):
    def setUp(self):
        _reset_tables()


# --------------------------------------------------------------------------- #
# Unit tests: tag_configs
# --------------------------------------------------------------------------- #
class SetupFlowTests(Base):
    def test_no_configuration_requires_setup(self):  # (2)
        uid = _make_user("a@example.com")
        status = tag_configs.get_status(uid)
        self.assertEqual(status["status"], "none")
        self.assertTrue(status["setup_required"])

    def test_successful_setup_persists_encrypted_and_completes(self):  # (3)
        uid = _make_user("a@example.com")
        with mock.patch("tag_provider.list_devices", side_effect=fake_list_devices):
            pending = tag_configs.begin_setup(uid, json.dumps(SECRETS))
        self.assertEqual(pending["status"], "pending")
        self.assertCountEqual(pending["devices"], DEVICES)
        self.assertNotIn("credentials", pending)

        done = tag_configs.select_tag(uid, "canonic-tag-1")
        self.assertEqual(done["status"], "completed")
        self.assertFalse(done["setup_required"])
        self.assertEqual(done["tag_name"], "Tag")

        with session_scope() as s:
            cfg = s.scalar(select(TagConfig).where(TagConfig.user_id == uid))
            raw = cfg.credentials
        self.assertNotIn("FAKE-TOKEN", raw)  # never plaintext at rest
        stored = decrypt_credentials(raw)
        self.assertEqual(stored["aas_token"], SECRETS["aas_token"])
        self.assertEqual(stored["fcm_credentials"], {"refreshed": True})
        with session_scope() as s:
            from models import Tag
            rows = s.execute(
                select(Tag.device_id, Tag.is_selected).where(Tag.user_id == uid)
                .order_by(Tag.device_id)
            ).all()
            email = s.scalar(select(TagConfig.google_email).where(TagConfig.user_id == uid))
        self.assertEqual(rows, [("canonic-phone", False), ("canonic-tag-1", True)])
        self.assertEqual(email, "someone@gmail.com")

    def test_connect_with_google_job(self):
        uid = _make_user("a@example.com")
        with mock.patch("tag_provider.connect_google", side_effect=fake_connect):
            started = tag_configs.start_connect(uid)
            self.assertTrue(started["connect"]["running"])
            status = _wait_connect(uid)
        self.assertEqual(status["status"], "pending")
        self.assertEqual(status["google_email"], "someone@gmail.com")
        self.assertEqual(status["devices"], sorted(DEVICES, key=lambda d: d["name"]))
        self.assertEqual(status["connect"]["step"], "done")
        done = tag_configs.select_tag(uid, "canonic-tag-1")
        self.assertEqual(done["status"], "completed")
        self.assertNotIn("connect", tag_configs.get_status(uid))

    def test_connect_failure_and_double_start(self):
        uid = _make_user("a@example.com")
        gate = threading.Event()

        def slow_fail(progress, timeout=1800):
            gate.wait(5)
            raise SetupError("no_browser", "Couldn't open Chrome")

        with mock.patch("tag_provider.connect_google", side_effect=slow_fail):
            tag_configs.start_connect(uid)
            with self.assertRaises(SetupError) as ctx:
                tag_configs.start_connect(uid)  # already open in Chrome
            self.assertEqual(ctx.exception.code, "setup_in_progress")
            gate.set()
            status = _wait_connect(uid)
        self.assertEqual(status["status"], "failed")
        self.assertEqual(status["connect"]["code"], "no_browser")
        self.assertEqual(_active_count(uid), 1)

    def test_connect_refused_when_already_completed(self):
        uid = _make_user("a@example.com")
        _complete_setup(uid)
        with mock.patch("tag_provider.connect_google") as m:
            with self.assertRaises(SetupError) as ctx:
                tag_configs.start_connect(uid)
            m.assert_not_called()
        self.assertEqual(ctx.exception.code, "already_configured")

    def test_failed_setup_is_not_completed_and_keeps_no_secrets(self):  # (4)
        uid = _make_user("a@example.com")
        err = SetupError("network", "Couldn't reach Google.")
        with mock.patch("tag_provider.list_devices", side_effect=err):
            with self.assertRaises(SetupError):
                tag_configs.begin_setup(uid, SECRETS)
        status = tag_configs.get_status(uid)
        self.assertEqual(status["status"], "failed")
        self.assertTrue(status["setup_required"])
        self.assertIn("reach Google", status["last_error"])
        with session_scope() as s:
            self.assertIsNone(s.scalar(select(TagConfig.credentials)))

        # Retry resumes the same row and succeeds.
        _complete_setup(uid)
        self.assertEqual(tag_configs.get_status(uid)["status"], "completed")
        self.assertEqual(_active_count(uid), 1)

    def test_invalid_and_incomplete_credentials(self):  # (5)
        uid = _make_user("a@example.com")
        with self.assertRaises(SetupError) as ctx:
            tag_configs.begin_setup(uid, {"username": "x"})
        self.assertEqual(ctx.exception.code, "missing_fields")
        with self.assertRaises(SetupError) as ctx:
            tag_configs.begin_setup(uid, "not json")
        self.assertEqual(ctx.exception.code, "missing_fields")

        bad = SetupError("invalid_credentials", "Google rejected these credentials")
        with mock.patch("tag_provider.list_devices", side_effect=bad):
            with self.assertRaises(SetupError) as ctx:
                tag_configs.begin_setup(uid, SECRETS)
        self.assertEqual(ctx.exception.code, "invalid_credentials")
        self.assertEqual(tag_configs.get_status(uid)["status"], "failed")

    def test_unknown_tag_rejected(self):
        uid = _make_user("a@example.com")
        with mock.patch("tag_provider.list_devices", side_effect=fake_list_devices):
            tag_configs.begin_setup(uid, SECRETS)
        with self.assertRaises(SetupError) as ctx:
            tag_configs.select_tag(uid, "someone-elses-tag")
        self.assertEqual(ctx.exception.code, "unknown_tag")

    def test_completed_setup_is_not_restarted_without_replace(self):  # (7)
        uid = _make_user("a@example.com")
        _complete_setup(uid)
        with mock.patch("tag_provider.list_devices", side_effect=fake_list_devices) as m:
            with self.assertRaises(SetupError) as ctx:
                tag_configs.begin_setup(uid, SECRETS)
            m.assert_not_called()
        self.assertEqual(ctx.exception.code, "already_configured")
        self.assertEqual(_active_count(uid), 1)

    def test_replace_and_reset(self):
        uid = _make_user("a@example.com")
        _complete_setup(uid)
        # A failed replacement keeps the working tag.
        with mock.patch("tag_provider.list_devices",
                        side_effect=SetupError("network", "down")):
            with self.assertRaises(SetupError):
                tag_configs.begin_setup(uid, SECRETS, replace=True)
        self.assertEqual(tag_configs.get_status(uid)["status"], "completed")

        with mock.patch("tag_provider.list_devices", side_effect=fake_list_devices):
            self.assertEqual(
                tag_configs.begin_setup(uid, SECRETS, replace=True)["status"], "pending"
            )
        self.assertEqual(_active_count(uid), 1)

        self.assertEqual(tag_configs.reset(uid)["status"], "none")
        self.assertEqual(_active_count(uid), 0)

    def test_concurrent_setup_creates_one_configuration(self):  # (8)
        uid = _make_user("a@example.com")
        started = threading.Event()

        def slow(credentials, timeout):
            started.set()
            time.sleep(1.0)
            return fake_list_devices(credentials, timeout)

        results: list = []

        def attempt():
            try:
                results.append(tag_configs.begin_setup(uid, SECRETS)["status"])
            except SetupError as exc:
                results.append(exc.code)

        with mock.patch("tag_provider.list_devices", side_effect=slow):
            t1 = threading.Thread(target=attempt)
            t1.start()
            started.wait(5)
            t2 = threading.Thread(target=attempt)
            t2.start()
            t1.join()
            t2.join()
        self.assertCountEqual(results, ["pending", "setup_in_progress"])
        self.assertEqual(_active_count(uid), 1)

    def test_worker_marks_expired_credentials_failed(self):
        uid = _make_user("a@example.com")
        _complete_setup(uid)
        config_id = tag_configs.completed_configs()[0]["id"]

        fake_findhub = types.ModuleType("findhub")
        fake_findhub.collect_points = mock.Mock(side_effect=KeyError("Auth"))
        import worker
        with mock.patch.dict(sys.modules, {"findhub": fake_findhub}), \
                mock.patch("tag_provider.prepare_process"):
            worker.run_worker(config_id, interval=0)  # returns on auth failure
        status = tag_configs.get_status(uid)
        self.assertEqual(status["status"], "failed")
        self.assertIn("rejected", status["last_error"])
        self.assertEqual(tag_configs.completed_configs(), [])

    def test_worker_stores_points_for_its_own_user_only(self):  # (9)
        a = _make_user("a@example.com")
        b = _make_user("b@example.com")
        _complete_setup(a)
        config_id = tag_configs.completed_configs()[0]["id"]
        points = [{"t": 1_800_000_000, "lat": 19.1, "lng": 72.9, "acc": 10.0, "src": "own"}]

        fake_findhub = types.ModuleType("findhub")
        calls = {"n": 0}

        def collect(tag_id, name):
            calls["n"] += 1
            self.assertEqual(tag_id, "canonic-tag-1")
            if calls["n"] > 1:
                raise KeyError("Auth")  # stop the loop after one good poll
            return points

        fake_findhub.collect_points = collect
        import worker
        with mock.patch.dict(sys.modules, {"findhub": fake_findhub}), \
                mock.patch("tag_provider.prepare_process"):
            worker.run_worker(config_id, interval=0)
        self.assertEqual(repository.total_points(a), 1)
        self.assertEqual(repository.total_points(b), 0)


class RepositoryTests(Base):  # (9) existing behaviour, now per user
    def test_dedup_days_and_ranges(self):
        uid = _make_user("a@example.com")
        p = [
            {"t": 1_800_000_000, "lat": 19.1, "lng": 72.9, "acc": 5.0, "src": "own"},
            {"t": 1_800_000_300, "lat": 19.2, "lng": 72.8, "acc": 8.0, "src": "crowdsourced"},
        ]
        self.assertEqual(repository.insert_points(p, "Tag", uid), 2)
        self.assertEqual(repository.insert_points(p, "Tag", uid), 0)  # dedup
        day = repository.days_with_data(uid)[0]
        self.assertEqual(day["count"], 2)
        self.assertEqual(len(repository.points_for_day(uid, day["date"])), 2)
        self.assertEqual(len(repository.points_since(uid, 1_800_000_000)), 1)
        self.assertEqual(
            len(repository.points_in_range(uid, day["date"], day["date"])), 2
        )

    def test_same_fix_for_two_users_is_kept_for_both(self):
        a, b = _make_user("a@example.com"), _make_user("b@example.com")
        p = [{"t": 1_800_000_000, "lat": 19.1, "lng": 72.9, "acc": 5.0, "src": "own"}]
        repository.insert_points(p, "Tag", a)
        repository.insert_points(p, "Tag", b)
        self.assertEqual(repository.total_points(a), 1)
        self.assertEqual(repository.total_points(b), 1)


class LegacyImportTests(Base):  # (10)
    def setUp(self):
        super().setUp()
        self.path = os.environ["GFMT_SECRETS_FILE"]
        if os.path.exists(self.path):
            os.remove(self.path)
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO locations (t, lat, lng, acc, src, tag) "
                "VALUES (1800000000, 19.1, 72.9, 5, 'own', 'Tag')"
            ))
        get_settings.cache_clear()

    def tearDown(self):
        os.environ["LEGACY_TAG_OWNER"] = ""
        get_settings.cache_clear()

    def test_without_owner_nothing_changes(self):
        uid = _make_user("a@example.com")
        tag_configs.import_legacy("Tag")
        self.assertEqual(repository.total_points(uid), 0)
        self.assertEqual(tag_configs.get_status(uid)["status"], "none")

    def test_owner_gets_points_even_without_secrets_file(self):
        uid = _make_user("a@example.com")
        os.environ["LEGACY_TAG_OWNER"] = "a@example.com"
        get_settings.cache_clear()
        tag_configs.import_legacy("Tag")
        self.assertEqual(repository.total_points(uid), 1)
        self.assertEqual(tag_configs.get_status(uid)["status"], "none")

    def test_secrets_file_imported_as_completed_config(self):
        uid = _make_user("a@example.com")
        other = _make_user("b@example.com")
        with open(self.path, "w") as fh:
            json.dump(SECRETS, fh)
        os.environ["LEGACY_TAG_OWNER"] = "a@example.com"
        get_settings.cache_clear()
        with mock.patch("tag_provider.list_devices", side_effect=fake_list_devices):
            tag_configs.import_legacy("Tag")
            tag_configs.import_legacy("Tag")  # idempotent
        status = tag_configs.get_status(uid)
        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["tag_name"], "Tag")
        self.assertEqual(_active_count(uid), 1)
        self.assertEqual(tag_configs.get_status(other)["status"], "none")


# --------------------------------------------------------------------------- #
# Integration tests: HTTP API
# --------------------------------------------------------------------------- #
class ApiTests(Base):
    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer

        from api import make_handler

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler())
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def call(self, method, path, body=None, token=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.loads(exc.read())

    def login(self, email):
        status, body = self.call("POST", "/login", {"email": email, "password": "pw-123456"})
        self.assertEqual(status, 200)
        return body

    def test_login_without_tag_directs_to_setup_then_completes(self):  # (2)(3)
        _make_user("a@example.com")
        body = self.login("a@example.com")
        self.assertTrue(body["setup_required"])
        self.assertEqual(body["tag_setup"]["status"], "none")
        token = body["token"]

        uid = auth.session_user(token)[0]
        with mock.patch("tag_provider.connect_google", side_effect=fake_connect):
            status, started = self.call("POST", "/tag/connect", {}, token)
            self.assertEqual(status, 200)
            self.assertTrue(started["connect"]["running"])
            _wait_connect(uid)
        status, pending = self.call("GET", "/tag", token=token)
        self.assertEqual((status, pending["status"]), (200, "pending"))
        self.assertEqual(len(pending["devices"]), 2)
        self.assertNotIn("aas_token", json.dumps(pending))

        status, done = self.call("POST", "/tag/select", {"tag_id": "canonic-tag-1"}, token)
        self.assertEqual((status, done["status"]), (200, "completed"))

        body = self.login("a@example.com")  # (1)(7) next login: no setup again
        self.assertFalse(body["setup_required"])
        self.assertEqual(body["tag_setup"]["tag_name"], "Tag")
        self.assertEqual(_active_count(uid), 1)
        status, body = self.call("POST", "/tag/connect", {}, token)
        self.assertEqual((status, body["code"]), (409, "already_configured"))

    def test_setup_errors_map_to_http_codes(self):  # (5)
        _make_user("a@example.com")
        token = self.login("a@example.com")["token"]
        uid = auth.session_user(token)[0]
        status, body = self.call("POST", "/tag/select", {"tag_id": "x"}, token)
        self.assertEqual((status, body["code"]), (409, "no_setup"))
        bad = SetupError("invalid_credentials", "rejected")
        with mock.patch("tag_provider.connect_google", side_effect=bad):
            self.assertEqual(self.call("POST", "/tag/connect", {}, token)[0], 200)
            _wait_connect(uid)
        _, tag = self.call("GET", "/tag", token=token)
        self.assertEqual((tag["status"], tag["connect"]["code"]), ("failed", "invalid_credentials"))
        self.assertEqual(self.call("POST", "/tag/connect", {})[0], 401)
        self.assertEqual(self.call("POST", "/tag/setup", {}, token)[0], 404)  # upload removed

    def test_users_only_see_their_own_data(self):  # (6)
        a, b = _make_user("a@example.com"), _make_user("b@example.com")
        _complete_setup(a)
        repository.insert_points(
            [{"t": 1_800_000_000, "lat": 19.1, "lng": 72.9, "acc": 5.0, "src": "own"}],
            "Tag", a,
        )
        ta = self.login("a@example.com")["token"]
        tb = self.login("b@example.com")["token"]

        self.assertEqual(len(self.call("GET", "/locations?since=0", token=ta)[1]["points"]), 1)
        self.assertEqual(self.call("GET", "/locations?since=0", token=tb)[1]["points"], [])
        self.assertEqual(self.call("GET", "/days", token=tb)[1]["days"], [])
        self.assertEqual(self.call("GET", "/tag", token=tb)[1]["status"], "none")
        # A client-supplied user_id is ignored.
        status, body = self.call("GET", "/locations?since=0&user_id=%d" % a, token=tb)
        self.assertEqual(body["points"], [])
        # B can't finish or reset A's setup; it only affects B.
        self.assertEqual(self.call("POST", "/tag/reset", {}, tb)[0], 200)
        self.assertEqual(tag_configs.get_status(a)["status"], "completed")

        # Shares expose only their creator's trace.
        day = repository.days_with_data(a)[0]["date"]
        _, share_b = self.call(
            "POST", "/shares", {"from": day, "to": day, "ttl_seconds": 3600}, tb
        )
        _, shared = self.call("GET", f"/shared?token={share_b['token']}")
        self.assertEqual(shared["points"], [])
        _, share_a = self.call(
            "POST", "/shares", {"from": day, "to": day, "ttl_seconds": 3600}, ta
        )
        _, shared = self.call("GET", f"/shared?token={share_a['token']}")
        self.assertEqual(len(shared["points"]), 1)


if __name__ == "__main__":
    unittest.main()
