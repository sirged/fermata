# Deploying Fermata

This is for running Fermata on a home server, a NAS, or a spare machine — not
for a professional server setup. If you've used Docker once or twice before,
that's enough. Every command below is meant to be copied and pasted as-is.

## Contents

- [Getting it running](#getting-it-running)
- [Reaching it from another device](#reaching-it-from-another-device)
- [Backups](#backups)
- [Upgrading](#upgrading)
- [Current limitations](#current-limitations)
- [Troubleshooting](#troubleshooting)

## Getting it running

You need [Docker](https://docs.docker.com/get-docker/) installed — Docker
Desktop on Windows or Mac, or `docker` and the `docker compose` plugin on
Linux. Nothing else.

Fermata is one container. It has no database server, no separate cache, no
second process to configure — everything it needs lives inside two folders you
choose.

Clone the repository and set up those two folders next to it:

```bash
git clone https://github.com/sirged/fermata.git
cd fermata
mkdir -p library config
```

- `library/` is where your sheet music goes — PDFs, MusicXML, Guitar Pro
  files. Fermata only reads from and writes into this folder; it never touches
  anything outside it.
- `config/` is where Fermata keeps its own state: the database (titles, tags,
  practice history, transcriptions) and a thumbnail cache. You will not need
  to open this folder yourself, but you will back it up — see
  [Backups](#backups).

Now copy some sheet music into `library/` — a PDF is enough to start. If you
don't have anything handy yet, that's fine too; you can add files later and
Fermata will pick them up.

Build and start the container:

```bash
docker compose up --build -d
```

The first run builds the image, which takes a minute or two — you'll see
`npm` and `pip` installing packages. Once it settles, check that the container
is actually healthy rather than just started:

```bash
docker compose ps
```

Look at the `STATUS` column in the output — it should read something like
`Up 30 seconds (healthy)`. `(healthy)` is the important part: it means the
container's own internal check succeeded, not just that it launched. If it
still says `(health: starting)`, wait a few seconds and run the command
again. If it never turns healthy, see [Troubleshooting](#troubleshooting).

Open **http://localhost:8080** in a browser on the same machine. Fermata scans
the `library/` folder automatically every time the container starts, so
anything you put there before starting should already show up. If you add
files while it's running, click **Scan library** in the sidebar rather than
restarting the container — it re-scans in the background and the page updates
as it finds things.

An empty library is not a failure — see
[Empty library after a scan](#empty-library-after-a-scan) in troubleshooting
if you expected files to show up and don't see them.

## Reaching it from another device

Fermata is meant to be read from whatever you're standing at — a tablet on a
music stand, a phone, a laptop — not just the machine running the container.
`docker compose up` already publishes port 8080 to your whole local network,
not just to `localhost`, so nothing further needs enabling.

Find the LAN address of the machine running Fermata:

- **Linux**: `hostname -I`
- **macOS**: `ipconfig getifaddr en0` (or `en1` if you're on Wi-Fi and that's
  not it)
- **Windows**: `ipconfig` and look for "IPv4 Address" under your active
  network adapter

Then, from another device on the same network, open
`http://<that address>:8080` — for example `http://192.168.1.42:8080`. If it
doesn't load, check that both devices are actually on the same network (a
guest Wi-Fi network is often isolated from the main one on purpose) and that
nothing on the host machine is blocking incoming connections on port 8080 —
Windows will sometimes prompt for a firewall rule the first time; allow it for
private networks.

This also means anyone else on that network can reach it, with no login of
any kind — see [Current limitations](#current-limitations) before deciding
whether that network is trusted.

## Backups

This is the section to actually act on, not just read. Everything in
`library/` is your own files — if you lost them, you'd still have the
originals somewhere. Everything in `config/` is not recoverable any other
way: your practice history, your tags, and any hand-corrected tab
transcriptions live only in the database in that folder. Losing `config/`
without a backup means losing that work, even though every PDF is still
sitting untouched in `library/`.

### Taking a backup

Stop the container first, so the database isn't being written to mid-copy:

```bash
docker compose stop
```

Copy the whole `config/` folder somewhere safe — another disk, a NAS share, a
cloud-synced folder, whatever you'd trust with anything else you don't want to
lose:

```bash
cp -r config config-backup-2024-01-15
```

(On Windows PowerShell: `Copy-Item -Recurse config config-backup-2024-01-15`.
Use today's date, not literally this one — the name only needs to be one you
recognize later.)

Then start Fermata again:

```bash
docker compose up -d
```

That's the whole backup: one folder, copied while the container isn't
writing to it. Do this on whatever schedule matches how much practice history
you'd be upset to lose — weekly is reasonable for casual use.

### Restoring a backup

Stop the container, replace the live `config/` folder with the backed-up one,
and start it again:

```bash
docker compose stop
rm -rf config
cp -r config-backup-2024-01-15 config
docker compose up -d
```

Your library files in `library/` are untouched by any of this — restoring
`config/` brings back the database as it was at backup time, and the next
startup scan reconciles it against whatever is currently in `library/`.

## Upgrading

To upgrade, pull the new version and rebuild:

```bash
git pull
docker compose up --build -d
```

Fermata's database applies its own schema changes automatically on startup —
new tables and columns an upgrade needs are created the first time the new
version starts, and a change too large for that (rebuilding a table to carry
its rows into a new shape) runs then too, once, inside a single transaction. It
has never required a manual step, and you do not need to run a migration
command.

That said, take a backup first anyway, the same way you would before any
software upgrade you can't easily undo:

```bash
docker compose stop
cp -r config config-backup-before-upgrade
git pull
docker compose up --build -d
```

If something looks wrong after an upgrade, restoring that backup and going
back to the previous `git` commit gets you back to where you started.

Going back without restoring the backup is a different matter, and this is why
the backup is worth taking. Once a version has started, its schema changes have
already been applied to `config/fermata.db`, and the database records which
version wrote it. An older Fermata will run quite happily against a database it
recognises — but if the newer version changed the schema, the older one refuses
to start rather than write to a database it does not understand, and says so:

```
RuntimeError: this database is at schema version 4, but this version of
Fermata understands 3. It was written by a newer release - upgrade, or
restore a backup taken before it.
```

That is the message telling you a `git` rollback alone is not enough. Either go
forward again, or restore the `config/` backup you took before upgrading.

## Current limitations

This section is here so you can decide whether Fermata is ready for what you
want from it, not to talk you out of using it. All of the following are
current, verified behavior — not modesty.

**Tab extraction needs a digitally engraved PDF.** Fermata reads tablature out
of a PDF by reading the actual text and music-font glyphs the score was
typeset with — it does not look at the page as an image and does not do any
optical recognition. A scanned PDF (a photograph or scan of a paper score)
carries none of that; there is nothing to read. You can still open a scanned
PDF and use it in the practice reader exactly like any other PDF — turning
pages, keeping your place — it just never becomes an interactive, playable
staff.

**Extracted rhythm is not always complete, and Fermata says so rather than
hiding it.** Rhythm is decoded from the engraving itself, and separating two
independent musical voices sharing one bar is the hardest part of that
process. Where the separation is imperfect, a bar's notes can add up to more
or less than the time signature says it should hold. Fermata does not silently
pad or trim a bar to make the arithmetic work — it reports exactly how many
bars in a transcription don't add up, alongside the transcription itself, so
you can see which passages to check by ear rather than trusting an unstated
guess.

**Harmonics are not carried into a transcription.** Natural and artificial
harmonics on a fretted instrument are dropped entirely when Fermata converts
a PDF into an editable score — the note simply isn't written into the result.
This is a known, current gap in what gets extracted, not a display setting.

**There is one user, and no login.** Fermata does not have accounts, a
sign-in screen, or any way to separate what a teacher sees from what a
student sees. Anyone who can open the address in a browser has the same
access as you do — they can browse and download everything in your library,
upload new files, and edit tags, metadata, and practice records.

Because of that, Fermata belongs on a home network you trust, not on the open
internet. If you forward its port to the internet or otherwise expose it
publicly, anyone who finds it — and on the open internet, something usually
does find an open port before long — gets that same unrestricted access with
no username or password standing in the way. There is nothing in Fermata
today that mitigates this; it simply is not built for that exposure yet.

## Troubleshooting

Each of these is meant to stand alone — find the one that matches what you're
seeing.

### Port already in use

If `docker compose up` fails with something like:

```
Error response from daemon: failed to set up container networking: driver
failed programming external connectivity on endpoint fermata: Bind for
0.0.0.0:8080 failed: port is already allocated
```

something else on the machine — often another container, sometimes another
application — is already using port 8080. Change the host side of the port
mapping in `docker-compose.yml`:

```yaml
ports:
  - "8081:8080"
```

Then run `docker compose up -d` again and open `http://localhost:8081`
instead. Only the number before the colon needs to change — the container
still listens on 8080 internally.

### Container won't start, or keeps restarting

Check what it's actually saying:

```bash
docker compose logs fermata
```

If the log ends with something like:

```
sqlite3.OperationalError: unable to open database file
ERROR:    Application startup failed. Exiting.
```

Fermata can't write to the `config/` folder — the most common cause is that
folder (or a file inside it, e.g. `fermata.db`) is not writable by the
container, for instance because it's mounted read-only or owned by a user
that doesn't grant write access. Confirm the mount isn't read-only in
`docker-compose.yml` (it should be `./config:/data/config`, not
`./config:/data/config:ro`), and on Linux hosts, that the `config` folder's
permissions allow writing:

```bash
chmod -R u+rwX config
```

Then restart:

```bash
docker compose up -d
```

There are two other reasons Fermata will stop on purpose, and because
`docker-compose.yml` sets `restart: unless-stopped`, either one shows up as a
container restarting over and over. Both print a plain explanation, so it is
worth reading to the end of the log rather than only the last line.

**"this database is at schema version N"** — the database was written by a
newer Fermata than the one you are running, so this one will not touch it. This
is the message you get from rolling a version back without restoring the
matching backup; see [Upgrading](#upgrading) for what to do.

**"missing the link that keeps it pointing at real rows"** — something is wrong
with the structure of the database itself. This cannot happen from normal use or
from any upgrade, so unless you have edited `fermata.db` by hand it is a bug we
would like to hear about: please open an issue with the log text. Your sheet
music in `library/` is not involved either way — only the database in `config/`.
If you have a backup of that folder, restoring it (see
[Restoring a backup](#restoring-a-backup)) is the safe way back. The message itself ends with a
repair note for anyone comfortable with SQLite, including which option
permanently discards data and which does not.

In both cases, stop the restart loop before working on it, so the container is
not fighting you:

```bash
docker compose stop
```

### Empty library after a scan

If you've put files in `library/` but nothing shows up after starting the
container or clicking **Scan library**, first confirm the container is
actually seeing the files you think it is:

```bash
docker exec fermata ls -la /data/library
```

If that comes back empty, the files aren't where the container is looking —
double check they're inside the `library/` folder next to your
`docker-compose.yml`, not a `library/` folder somewhere else, and that
`docker-compose.yml` still has `./library:/data/library` under `volumes:`.

If the files do show up in that listing but not in Fermata, the extension may
not be one Fermata recognizes. It picks up `.pdf`, `.musicxml`, `.mxl`,
`.gp`, `.gp3`, `.gp4`, `.gp5` and `.gpx` — anything else (a `.doc`, a `.zip`
of a whole folder, a bare image) is skipped silently by the scanner rather
than shown as an error. Renaming or converting to one of those extensions and
re-scanning resolves it.

### General "something's wrong, where do I even look"

```bash
docker compose ps
docker compose logs fermata --tail 50
```

The first tells you whether the container is running and, importantly,
whether it reports `(healthy)`. The second shows recent log output, which for
almost every failure ends with a Python traceback that names the actual
problem — worth reading even if it looks intimidating, since the last few
lines are usually plain English.
