# Run Tag Trace on the phone (Termux)

The collector runs in Termux on the phone, saves points to your Neon cloud
database, and serves the app on `http://127.0.0.1:8020`. The Google sign-in
needs Chrome, so it happens once on the Mac; the phone just reuses the token.

You need from the Mac (both are secret, never commit or share them):
- `GoogleFindMyTools/Auth/secrets.json` (Google tokens)
- `GoogleFindMyTools/.env` (Neon `DATABASE_URL` and settings)

## 1. Install Termux

Install **Termux** and **Termux:API** from **F-Droid** (the Play Store build is
outdated). Optional: **Termux:Boot** to start on boot.

## 2. Install packages (in Termux)

```
pkg update && pkg upgrade -y
pkg install -y python git python-cryptography postgresql rust binutils termux-api
termux-setup-storage            # allow access to Downloads; tap Allow
```

`python-cryptography` is prebuilt; `postgresql` provides libpq for psycopg2;
`rust`/`binutils` are for the few packages that build from source.

## 3. Get the code

```
cd ~
git clone https://github.com/santoshsahu108/tagTrace.git
cd tagTrace
pip install -r termux/requirements-termux.txt
```

If one package fails to build, install it alone (`pip install <name>`) and
re-run the line above.

## 4. Copy the two secret files from the Mac

Move `secrets.json` and `.env` to the phone's **Downloads** folder (USB cable,
or a private cloud drive), then in Termux:

```
cp ~/storage/downloads/secrets.json ~/tagTrace/GoogleFindMyTools/Auth/secrets.json
cp ~/storage/downloads/.env        ~/tagTrace/GoogleFindMyTools/.env
chmod 600 ~/tagTrace/GoogleFindMyTools/Auth/secrets.json ~/tagTrace/GoogleFindMyTools/.env
rm ~/storage/downloads/secrets.json ~/storage/downloads/.env    # other apps can read Downloads
```

On a phone the collector only needs to listen locally. Edit `.env`
(`nano ~/tagTrace/GoogleFindMyTools/.env`) and set:

```
HTTP_HOST=127.0.0.1
GFMT_DIR=
```

(`GFMT_DIR` must be empty here; the Mac path does not exist on the phone.)

## 5. First run

```
cd ~/tagTrace/GoogleFindMyTools
python collector.py
```

Expect `[db] connected, N points stored` and `Tracking 'Tag' every 300s`.
Your login user already lives in Neon, so no `--add-user` is needed.

**Stop the collector on the Mac** so two copies don't poll the same account.

## 6. Keep it running

```
bash ~/tagTrace/termux/run.sh          # takes a wake-lock, runs the collector
```

In Android settings, set **Battery → Unrestricted** for Termux so it isn't
killed with the screen off.

Start on boot (needs Termux:Boot, opened once):

```
mkdir -p ~/.termux/boot
cp ~/tagTrace/termux/run.sh ~/.termux/boot/tagtrace.sh
chmod +x ~/.termux/boot/tagtrace.sh
```

## 7. In the app

**Settings → Over Wi-Fi**: enter `http://127.0.0.1:8020`, log in with your
user, tap **Test**, then **Save**, then **Sync now**.

## Update later

```
cd ~/tagTrace && git pull && pip install -r termux/requirements-termux.txt
```

then restart the collector. `git pull` never touches your `.env` or
`secrets.json` (both are gitignored).

## Troubleshooting

- `No module named ...`: run the `pip install -r` line from step 3 again.
- `psycopg2` build fails: make sure `pkg install postgresql` ran, then
  `pip install psycopg2`.
- `Can't reach Postgres`: check the phone has internet and `DATABASE_URL` in
  `.env` is the Neon one.
- Google token errors: redo the login on the Mac (`python main.py`) and copy
  the new `secrets.json` again.
