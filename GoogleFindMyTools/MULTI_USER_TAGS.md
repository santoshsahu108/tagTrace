# Per-user tag configuration

Each signed-in user now connects **their own** Find Hub tag with a
**Connect with Google** button. The collector opens Chrome on the computer it
runs on, the user signs in, and the Google keys plus the account's trackers
are saved straight into the database (keys encrypted). No `secrets.json`
file is involved for new users.

## 1. How it worked before

- **Login** (`auth.py`, `api.py`): `POST /login` checks a PBKDF2 password and
  returns a bearer token stored in `sessions`. The token → user lookup existed,
  but nothing downstream used the user.
- **Tag credentials** (`Auth/token_cache.py`): every GoogleFindMyTools module
  read and wrote one global `Auth/secrets.json` (`username`, `aas_token`,
  `shared_key`, `owner_key`, `fcm_credentials`).
- **Workflow** (`collector.py` → `findhub.py` → GFMT): at start the collector
  found the tracker whose name contains `TAG_NAME`, then every
  `POLL_INTERVAL` asked Find Hub for its location (`nbe_execute_action`),
  received the encrypted answer as an FCM push, decrypted it and inserted into
  `locations`. All users saw all points.
- **Setup**: run `python main.py` on a computer with Chrome; GFMT opens a
  Google sign-in and writes `secrets.json`. No in-app setup existed.

GFMT keeps credentials and its FCM connection in **process-global state**
(`token_cache`, the `FcmReceiver` singleton), so two users can't safely share
one process. That drives the design below.

## 2. Schema and migration

New table `tag_configs` (`models.TagConfig`):

| column | type | notes |
|---|---|---|
| `id` | bigint identity PK | |
| `user_id` | bigint FK → users, ON DELETE CASCADE | from the session only |
| `google_email` | text | connected Google account (plain) |
| `tag_id` | text | Find Hub canonic device id of the chosen tracker |
| `tag_name` | text | tracker name shown in UI/logs |
| `credentials` | text | **Fernet ciphertext** of the secret keys (`aas_token`, `shared_key`, `owner_key`, `fcm_credentials`) |
| `configuration` | jsonb | reserved for non-secret extras |
| `setup_status` | text | `pending` / `completed` / `failed` |
| `last_error` | text | user-facing reason for `failed` |
| `is_active` | bool | old configs kept inactive (history), secrets wiped |
| `created_at`, `updated_at` | timestamptz | |

New table `tags` (`models.Tag`): every tracker on the connected Google
account — `id`, `user_id`, `tag_config_id` (FK, cascade), `device_id`
(canonic id), `name`, `is_selected`, `created_at`, `updated_at`; unique
`(tag_config_id, device_id)`. Refreshed on each connect; the chosen one is
`is_selected` and copied to `tag_configs.tag_id`.

- **One active config per user**: partial unique index
  `tag_configs_one_active_per_user (user_id) WHERE is_active`. Multiple tags
  per user later = relax that index; no reshaping.
- `locations` gains `user_id` (FK, cascade) and `tag_config_id` (FK, set
  null). The global dedup constraint `(t, lat, lng)` becomes the per-user
  unique index `(user_id, t, lat, lng) NULLS NOT DISTINCT`, plus an index on
  `(user_id, t)`.
- `shares` gains `user_id`: a share link shows only its creator's trace.

Migration follows the project's existing convention (no Alembic):
`db.init_database()` runs `create_all` and then the idempotent
`_migrate_user_scoping()` (`ALTER TABLE … ADD COLUMN IF NOT EXISTS`, index
swap) on every start. Requires PostgreSQL 15+ (Neon is 18).

## 3. Files changed

| file | change |
|---|---|
| `models.py` | `TagConfig`; `user_id`/`tag_config_id` on `Location`, `user_id` on `Share` |
| `db.py` | `_migrate_user_scoping()`; DB name taken from `DATABASE_URL` |
| `config.py` | `TAG_CREDENTIALS_KEY`, `LEGACY_TAG_OWNER`, `SETUP_TIMEOUT` |
| `crypto.py` (new) | Fernet encrypt/decrypt of credentials, key generation |
| `Auth/token_cache.py` | optional in-memory credential store (`use_memory_store`) |
| `tag_provider.py` (new) | per-process isolation, Chrome sign-in (`connect_google`), validation, error classes |
| `chrome_driver.py` | matches chromedriver to the installed Chrome; no longer kills the user's Chrome |
| `tag_configs.py` (new) | lookup, connect job, tags table, reset, worker helpers, legacy import |
| `worker.py` (new) | one poller process per completed config |
| `collector.py` | supervisor that starts/stops workers; `--generate-key` |
| `repository.py`, `shares.py` | every query scoped by `user_id` |
| `auth.py` | `session_user()` → `(user_id, email)` |
| `api.py` | `/tag*` endpoints; login and `/me` return setup status |
| `web/src/App.jsx`, `index.css` | setup screen, "Reconnect tag" |
| `tests/test_tag_setup.py` (new) | 21 unit + integration tests |

## 4. Configuration lookup

- **Requests**: `auth.session_user(token)` → `user_id`; then
  `tag_configs.get_status(user_id)`, `repository.*(user_id, …)`. Any
  `user_id` the client sends is ignored.
- **Background polling**: `collector._supervise` reads
  `tag_configs.completed_configs()` every 15 s and keeps one spawned
  `worker.run_worker(config_id)` process per completed config. The worker
  loads and decrypts **only its own** config, puts it in GFMT's in-memory
  store (`tag_provider.prepare_process`), and writes every point with that
  config's `user_id`. Refreshed credentials (e.g. new FCM registration) are
  re-encrypted back to the row. Nothing is written to disk.
- Reset/replace/failure removes the config from the completed set, so the
  supervisor stops that worker; a crashed worker restarts after 60 s.

## 5. Setup flow and login

```
POST /login ─▶ {token, email, tag_setup, setup_required}
                         │
     status none/failed ─┤  web shows "Connect your tag"
                         ▼
POST /tag/connect  ─▶ background job; collector opens Chrome on its computer
   GET /tag  ◀── web polls every 2 s: connect.{running, step, message}
                         │  1. Google sign-in (accounts.google.com/EmbeddedSetup)
                         │  2. sign in again + unlock key backup with screen lock
                         │  3. owner key + tracker list read automatically
                         ├─ error ─▶ status failed + last_error, no secrets kept
                         ▼
     status pending + devices[] (from `tags`)   resumable after reload/login
                         ▼
POST /tag/select {tag_id}  ─▶ status completed ─▶ worker starts polling
POST /tag/reset            ─▶ status none (Reconnect tag)
```

- `tag_provider.connect_google` runs GoogleFindMyTools' own Chrome flows in a
  separate process with an empty in-memory credential store, without their
  console prompts, and reports progress steps (`google_login`, `key_backup`,
  `checking`). Cancelling or timing out closes the Chrome window.
- A completed config is never set up again on login; replacing it requires
  `replace: true` (sent only from "Reconnect tag"). A second click while
  Chrome is open returns `setup_in_progress`.
- **Requirement**: the collector must run on a computer with Chrome and a
  screen (the Mac). On Termux/headless servers the job fails with
  `no_browser`; set up on the Mac against the same Neon database, then run
  the collector anywhere.
- Errors are `{error, code}` (or `connect.code` for the job):
  `no_browser`, `timeout`, `cancelled`, `invalid_credentials`, `no_trackers`,
  `network`, `provider`; and for requests `unknown_tag` 400, `no_setup` 409,
  `already_configured` 409, `setup_in_progress` 409.

## 6. Security

- Credentials encrypted at rest with Fernet (AES-128-CBC + HMAC) using
  `TAG_CREDENTIALS_KEY`; ciphertext only in the DB. The API never returns
  credentials. Retired or failed configs have their credentials wiped.
- `user_id` always comes from the session token; ownership is implicit in
  every query (`WHERE user_id = :session_user`).
- Each user's Google session runs in its own OS process, so GFMT's global
  state can't mix users.
- Concurrent setups for the same user are serialised with a Postgres advisory
  lock; the loser gets `setup_in_progress`. The partial unique index is the
  final guard against duplicates.
- Missing/expired Google tokens never open Chrome on the server: the
  interactive entry points raise, the worker marks the config `failed` with
  "reconnect" and stops; other users are unaffected.
- Nothing logs credentials; worker logs show only user id and points.
- The sign-in Chrome uses a throwaway profile; GoogleFindMyTools' habit of
  killing all Chrome processes first is now off unless `CHROME_KILL_EXISTING=1`.
- Request bodies are capped at 64 KB.

## 7. Tests

`tests/test_tag_setup.py` (stdlib `unittest`, Google mocked) against a local
throwaway database — refuses to run against Neon:

```
TEST_DATABASE_URL=postgresql+psycopg2://$USER@127.0.0.1:5432/tagtrace_test \
  .venv/bin/python -m unittest discover -s tests -v
```

Covers: completed user logs in and continues (no re-setup); no config →
setup required; Connect-with-Google job (progress, saved trackers in `tags`,
Google email, double start refused, Chrome failure → failed); successful setup
persists encrypted creds; failed setup isn't
completed and keeps no secrets, retry resumes; invalid/incomplete
credentials; cross-user isolation (locations, days, tag status, shares,
ignored client `user_id`); repeated login/setup creates no duplicates;
concurrent setup → exactly one config; worker stores points only for its
user and marks expired logins failed; dedup/day/range queries; legacy import
with and without `secrets.json`.

Also verified by hand: on the Mac, Connect with Google opens a Chrome sign-in
window and closes it again on cancel; the web screen shows live progress and
then the tracker list; real credentials list trackers; an invalid token is
reported as `invalid_credentials`; the supervisor spawns
a worker, which marks a rejected login failed and is stopped; the migration
on a copy of the old local database assigns all 1063 points; the web setup
screen (error, resume after reload, pick tracker, dashboard).

## 8. Environment variables

| var | required | purpose |
|---|---|---|
| `TAG_CREDENTIALS_KEY` | yes | Fernet key; `python3 collector.py --generate-key`. Back it up — losing it forces every user to reconnect. |
| `LEGACY_TAG_OWNER` | for migration | email that inherits `secrets.json` + existing points/shares |
| `SETUP_TIMEOUT` | no (90) | seconds to wait for Google when validating legacy credentials |
| `CHROME_VERSION_MAIN` | no | force chromedriver's Chrome major version (default: detected) |
| `CHROME_KILL_EXISTING` | no | `1` restores GFMT's "close all Chrome first" behaviour |
| `GFMT_SECRETS_FILE` | no | legacy secrets.json path (import only) |
| `TAG_NAME` | no | legacy import: which tracker to pick |

Web UI: no new variables.

## 9. Migration and deployment

1. Stop the running collector (only one may run).
2. `python3 collector.py --generate-key` → put it in `.env` as
   `TAG_CREDENTIALS_KEY` (and in `/etc/tagtrace/tagtrace.env` on servers).
3. Set `LEGACY_TAG_OWNER=<your login email>` in `.env`.
4. Start the collector. On first start it migrates the schema, imports your
   `secrets.json` as your tag (tracker matching `TAG_NAME`), assigns all
   existing points and shares to you, and starts your worker. Expect:
   `[legacy] assigned N points …`, `[legacy] imported … as …'s tag 'Tag'`,
   `[supervisor] started worker for user 1 ('Tag')`.
5. Rebuild/restart the web UI. Other users log in and click **Connect with
   Google** (while the collector runs on a computer with Chrome).
6. After confirming, you may delete `Auth/secrets.json` (it's no longer read).

Rollback: the new columns are additive, but the old code's inserts rely on
the dropped `(t, lat, lng)` constraint. To roll back, recreate it
(`ALTER TABLE locations ADD CONSTRAINT locations_t_lat_lng_key UNIQUE (t, lat, lng)`,
after removing cross-user duplicates) — the old code would then show every
user's points to everyone.

## 10. Assumptions, limitations, open questions

- **Chrome opens on the collector's computer**, not on the user's own device.
  That's fine when you run the collector on your Mac; users elsewhere need
  someone at that computer, or a remote-browser setup (not built).
- Two Google sign-ins are needed (account, then key backup); that's how
  Google's encrypted-key backup works and can't be skipped.
- **One tag per user** for now (schema ready for more).
- **Android app**: unchanged and still works for users with a completed
  setup (login stays compatible). It has no setup screen; users connect
  their tag in the web UI first.
- **Resources**: one process (≈60–80 MB) and one FCM connection per user.
  Fine for a handful of users; many users would need pooling.
- **Same Google account for two users** would make their workers compete for
  one FCM channel. Not prevented yet; open question whether to block it.
- **Key rotation** for `TAG_CREDENTIALS_KEY` isn't automated (would need
  MultiFernet re-encryption).
- Legacy `secrets.json` support remains until all deployments are migrated;
  remove `tag_configs.import_legacy` and `GFMT_SECRETS_FILE` after that.
