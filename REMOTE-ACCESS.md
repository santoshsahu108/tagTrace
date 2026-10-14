# Remote access to the Termux server (Tailscale + SSH key + tmux)

- **Phone A**: the phone running Tag Trace (collector + web page).
- **Phone B**: any other device with Termux you connect from.

Tailscale gives both phones a private address (100.x.x.x) that works on any
network (Wi-Fi or mobile data). SSH with a key logs you in without a password.
tmux keeps the collector running so you can watch its live screen from Phone B.
Nothing is opened to the public internet.

---

## Step 1 — Tailscale on both phones

1. Install the **Tailscale** app from the Play Store on Phone A and Phone B.
2. Open it and sign in with the **same account** on both. Tap **Connect**.
3. On Phone A, in the Tailscale app, note its address, e.g. `100.101.102.103`.
4. Phone A only: Android Settings → Apps → Tailscale → Battery → **Unrestricted**
   (do the same for Termux), so neither is killed in the background.

Check from Phone B later (step 4) — no extra setup needed in Termux.

## Step 2 — SSH server on Phone A (Termux)

```
pkg update && pkg install -y openssh tmux
passwd            # set a Termux password (used only once, then disabled)
whoami            # note your user name, e.g. u0_a123
sshd              # starts SSH on port 8022
```

## Step 3 — SSH key on Phone B (Termux)

```
pkg update && pkg install -y openssh
ssh-keygen -t ed25519 -f ~/.ssh/id_tagtrace -N ""
```

Send the public key to Phone A (asks Phone A's Termux password once):

```
ssh-copy-id -p 8022 -i ~/.ssh/id_tagtrace.pub u0_a123@100.101.102.103
```

(If `ssh-copy-id` is missing: run `cat ~/.ssh/id_tagtrace.pub`, and on Phone A
paste that one line into `~/.ssh/authorized_keys`, then
`chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys`.)

To save typing, on Phone B create `~/.ssh/config`:

```
nano ~/.ssh/config
```

```
Host tagtrace
    HostName 100.101.102.103
    Port 8022
    User u0_a123
    IdentityFile ~/.ssh/id_tagtrace
```

## Step 4 — Test the login from Phone B

```
ssh tagtrace
```

You should land in Phone A's Termux **without a password**. Type `exit` to leave.

## Step 5 — Turn off password logins (Phone A)

Only after step 4 works:

```
echo "PasswordAuthentication no" >> $PREFIX/etc/ssh/sshd_config
pkill sshd; sshd
```

Now only devices holding your key can log in.

## Step 6 — Run Tag Trace inside tmux (Phone A)

```
tmux new -s tagtrace
cd ~/tagTrace/GoogleFindMyTools && python collector.py
```

Open a second tab for the web page: press **Ctrl+B**, then **C**, and run:

```
cd ~/tagTrace/web && npm run dev
```

Leave tmux without stopping anything: **Ctrl+B**, then **D**.

## Step 7 — Watch the live session from Phone B

```
ssh tagtrace
tmux attach -t tagtrace
```

- Switch tabs: **Ctrl+B**, then **N** (next) / **P** (previous).
- Leave (keeps running): **Ctrl+B**, then **D**.
- **Ctrl+C** stops the program in the current tab — only use it on purpose.

## Step 8 — Open the web page on Phone B

Option A, over Tailscale directly: open `http://100.101.102.103:5173` in
Phone B's browser. Add the Tailscale address to `web/.env` on Phone A first:

```
VITE_API_URL=/api
VITE_ALLOWED_HOSTS=yourdomain.com,100.101.102.103
```

then restart `npm run dev`.

Option B, through SSH (no change needed):

```
ssh -N -L 5173:localhost:5173 tagtrace
```

keep it running, and open `http://localhost:5173` on Phone B.

## Step 9 — Start everything on boot (Phone A)

Install **Termux:Boot** from F-Droid and open it once. Then:

```
mkdir -p ~/.termux/boot
nano ~/.termux/boot/tagtrace.sh
```

```
#!/data/data/com.termux/files/usr/bin/bash
termux-wake-lock
sshd
tmux new -d -s tagtrace -n collector "cd ~/tagTrace/GoogleFindMyTools && python collector.py"
tmux new-window -t tagtrace -n web "cd ~/tagTrace/web && npm run dev"
```

```
chmod +x ~/.termux/boot/tagtrace.sh
```

After a reboot, `ssh tagtrace` then `tmux attach -t tagtrace` shows both running.

## Troubleshooting

- `Connection refused`: `sshd` isn't running on Phone A — run `sshd` there.
- `Connection timed out`: Tailscale is off on one phone, or the IP is wrong.
- `Permission denied (publickey)`: the key isn't in Phone A's
  `~/.ssh/authorized_keys`, or its permissions aren't 700/600.
- `no sessions` on `tmux attach`: the collector isn't in tmux yet — do step 6.

## Security notes

- Keep `~/.ssh/id_tagtrace` (Phone B) private; it is the key to Phone A.
- Lost Phone B? On Phone A, delete its line from `~/.ssh/authorized_keys`,
  and remove the device in the Tailscale admin console.
