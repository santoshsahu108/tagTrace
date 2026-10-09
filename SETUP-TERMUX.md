# Standalone setup (phone only)

Everything runs on the phone: the app, plus a small Python helper in Termux
that fetches the tag and serves it to the app over localhost
(`http://127.0.0.1:8020`). No PC stays involved after the one-time login.

## Why one step still needs a computer, once

Signing in to Google the first time uses a Chrome window, which Termux can't
run. So you do the sign-in **once on any computer with Chrome**, which creates a
small `Auth/secrets.json` token file, and copy that file to the phone. After
that the phone runs on its own.

## 1. One-time login (on a computer with Chrome)

```
git clone https://github.com/leonboe1/GoogleFindMyTools
cd GoogleFindMyTools
pip install -r requirements.txt
python main.py        # a Chrome window opens — sign in to your Google account
```

This writes `Auth/secrets.json`. In Find Hub, set the network to
**"With network in all areas"** so the tag updates even in quiet places.
Copy `Auth/secrets.json` somewhere you can get it onto the phone (cloud drive,
cable, etc.).

## 2. On the phone (Termux)

Install **Termux from F-Droid** (the Play Store build is too old), then:

```
pkg update && pkg install python git rust binutils
git clone https://github.com/leonboe1/GoogleFindMyTools
cd GoogleFindMyTools
pip install -r ~/storage/shared/.../task3/termux/requirements-termux.txt
```

(`rust`/`binutils` are there because a couple of packages compile from source
on Android. If `pip` struggles with one, install it alone and retry.)

Then put two files into this `GoogleFindMyTools` folder:
- `collector.py` (from this project's `collector/` folder)
- `Auth/secrets.json` (the token from step 1)

Run it:

```
cp /path/to/collector.py .
python collector.py --tag "JioTag" --port 8020
```

Leave that running. `termux/run.sh` in this project does the same and also takes
a wake-lock so it keeps going with the screen off. To start it automatically on
boot, install the Termux:Boot add-on and put `run.sh` in `~/.termux/boot/`.

## 3. In the app

Open **Settings → Over Wi-Fi**, enter `http://127.0.0.1:8020`, tap **Test**,
then **Save**. Tap **Sync now** on the home screen. That's it — tap any day for
its trace.

## If Termux fights the native builds

`http_ece`, `cryptography` and `pyscrypt` compile from source on Android and can
be stubborn. If you can't get them to build after a couple of tries, the same
`collector.py` runs cleanly on any always-on computer or Raspberry Pi instead
(see `README.md`), and the app reads it over your home Wi-Fi — same app, no code
change.
