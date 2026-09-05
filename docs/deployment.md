# Deploying Fermata

This is for running Fermata on a home server, a NAS, or a spare machine — not
for a professional server setup. If you've used Docker once or twice before,
that's enough. Every command below is meant to be copied and pasted as-is.

## Contents

- [Getting it running](#getting-it-running)
- [Reaching it from another device](#reaching-it-from-another-device)
- [Reverse proxy authentication](#reverse-proxy-authentication)
- [The Model Context Protocol server](#the-model-context-protocol-server)
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
  anything outside it. It also will not create it: if this folder is missing,
  Fermata stops and says so, because a missing library folder is far more often
  a drive that did not mount than a folder nobody made yet.
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
whether that network is trusted, and see the next section if you want to put
a login in front of it.

## Reverse proxy authentication

Fermata still has no accounts or login screen of its own — see
[Current limitations](#current-limitations) — but it can trust one that a
reverse proxy in front of it already did. This is the standard pattern
self-hosted services use to add a login without building one: an
authenticating proxy (Caddy, Authelia, authentik, and others all support it)
sits in front of the app and, once a visitor has signed in, sets an HTTP
header naming them on every request it forwards. Fermata can be told to
trust that header.

**This is off by default, and stays off after an upgrade unless you turn it
on.** Nothing below does anything unless you set both environment variables
it describes — an existing `docker-compose.yml` that predates this feature
keeps behaving exactly as it always did.

### Turning it on

Two environment variables, both required together:

- `FERMATA_AUTH_HEADER` — the header your proxy sets with the logged-in
  username. **Use `X-Remote-User`, the name every example in this section
  uses.** Fermata will accept any header name here, but naming a DIFFERENT
  one than your proxy config actually protects is a real way to defeat this
  feature by accident — see the warning box below before you pick anything
  else.
- `FERMATA_TRUSTED_PROXIES` — a comma-separated list of IP addresses and/or
  CIDR ranges Fermata will accept that header from. **This is the setting
  that matters for security.** Fermata trusts `FERMATA_AUTH_HEADER` only on
  a request whose direct connection comes from an address on this list — a
  request from anywhere else has the header ignored entirely, even if it
  sends one, because nothing stops a client on your network from setting
  that header itself otherwise. Set this to your proxy container's address,
  not a broad range, if you can — see the worked example below for how to
  give it a fixed one.

> **Your proxy MUST REPLACE this header, never append to it, and must strip
> any copy the client itself sent.** Fermata trusts the header by name
> alone, not by which layer set it, and reads only the FIRST occurrence when
> a header is sent more than once — so a proxy configured to *add* the
> header onto whatever the request already carried can leave a client's own
> forged value sitting in front of the proxy's real one, and Fermata would
> read the forged one. (As of this feature, Fermata also refuses outright
> — `401` — any request where the configured header arrives more than
> once, specifically because that shape is never produced by a proxy
> correctly configured to replace it; treat seeing this rejection in your
> logs as a sign your proxy is appending, not replacing.) Every example
> below sets, not adds, the header — that is what `header_up` does in Caddy
> and what `copy_headers` combined with `header_up` does in the Authelia
> example — but if you write your own, confirm your proxy's documentation
> says "set" or "replace" and not "add".

Setting `FERMATA_AUTH_HEADER` without also setting `FERMATA_TRUSTED_PROXIES`
does not accidentally trust everyone — it does the opposite. An empty trusted
list trusts no address at all, so every request is refused once the header
name is set, including ones from your actual proxy. This is deliberate: the
one way to misconfigure this fails as "nobody can get in, and the log says
why" rather than "anyone can set the header themselves and get in as
anyone." Look at `docker compose logs fermata` if turning this on locks you
out unexpectedly — a rejected request logs why, including the address it
came from.

The reverse is also checked, rather than left to fail silently, in two
different ways depending on how bad the mistake is:

- **`FERMATA_TRUSTED_PROXIES` set while `FERMATA_AUTH_HEADER` is not** logs
  an error at startup but still starts — this is almost always a typo in
  the header variable's name, and with the header unset, auth is entirely
  OFF: every request is served unauthenticated no matter what the
  trusted-proxy list says. A warning, not a refusal, because nothing here
  is actively pretending to be secure while it isn't — it is visibly,
  checkably off.
- **`FERMATA_TRUSTED_PROXIES` including `0.0.0.0/0` or `::/0` refuses to
  start outright.** No real proxy's own address is ever "the entire
  internet" — this is always a mistake, never an intentional choice, unlike
  a genuinely broad-but-real subnet (`10.0.0.0/8`, say, which stays a
  warning if you actually mean it). With auth ON, this combination is
  strictly worse than auth being off: the running server would authenticate
  *any* direct request as whatever username it claims, and serve that
  identity at `/api/me`, while `docker compose ps` reports the container
  perfectly healthy the entire time. A log line an operator has to go
  looking for is not enough for a failure mode that looks, from the
  outside, like everything is working correctly - so Fermata does not
  start at all, the same as it would for uvicorn's own proxy-header trust
  being left on.

Watch `docker compose logs fermata` after changing either setting — both
print a plain explanation.

In `docker-compose.yml`, add both under the `fermata` service's `environment:`
(or `environment:` doesn't exist yet — add it):

```yaml
services:
  fermata:
    environment:
      FERMATA_AUTH_HEADER: X-Remote-User
      FERMATA_TRUSTED_PROXIES: 172.28.1.10/32
```

### Why uvicorn's own proxy-header trust must stay off

This one is not optional, and Fermata now refuses to start with reverse-proxy
auth turned on unless it can confirm you have it right: the container's own
`uvicorn` server must be run with **`--no-proxy-headers`**. Fermata's shipped
`Dockerfile` and the plain `uvicorn` command in this project's own README
already pass it — this section exists so that if you run Fermata a different
way (your own image, a bare `uvicorn` invocation, a process manager), you
know not to drop it.

Here is the failure this closes, plainly: `uvicorn` can be told to trust an
`X-Forwarded-For` header from a nearby peer and use it to decide what
address a request "really" came from — a legitimate feature for a proxy
uvicorn itself sits behind. **By default, this is ON**, and it runs
*outside* Fermata's own code, before `FERMATA_TRUSTED_PROXIES` is ever
consulted. If it is on, a request straight to Fermata carrying a forged
`X-Remote-User` header AND a forged `X-Forwarded-For` naming an address on
your trusted-proxy list gets treated as if it genuinely came from that
address — a complete authentication bypass, reachable by anyone who can
reach Fermata at all, regardless of how carefully `FERMATA_TRUSTED_PROXIES`
is set. `--no-proxy-headers` turns this off entirely, so the address Fermata
checks is always the real TCP connection, never something a header can
rewrite.

Fermata cannot see how you actually launched `uvicorn`, so as a backstop it
refuses to start (a clear, readable error, not a silent gap) whenever
`FERMATA_AUTH_HEADER` is set and it cannot confirm `--no-proxy-headers` was
passed, or when the `FORWARDED_ALLOW_IPS` environment variable is set at
all — that variable is the other way to widen exactly the trust that must
stay off. This check is best-effort (it reads this process's own command
line — including which of `--proxy-headers` / `--no-proxy-headers` appears
LAST, since that is the one a real launch actually obeys, not merely
whether `--no-proxy-headers` appears anywhere) and is not a substitute for
actually passing the flag; treat it as a safety net catching the mistake,
not the fix itself. Simply don't pass both.

### The full configuration state space

Every combination of the two settings, and what a request from an address
NOT on `FERMATA_TRUSTED_PROXIES` — carrying a forged header — gets back,
assuming `--no-proxy-headers` is correctly in place:

| `FERMATA_AUTH_HEADER` | `FERMATA_TRUSTED_PROXIES` | Forged request from an untrusted address | Notes |
| --- | --- | --- | --- |
| unset | unset | `200`, unauthenticated — same as before this feature existed | The default. Not a bypass: there is no auth to bypass. |
| unset | set | `200`, unauthenticated | **Logged as an error at startup**, still starts — almost always a typo in `FERMATA_AUTH_HEADER`'s name; the trusted-proxy list is doing nothing. |
| set | unset (empty) | `401` | Fail closed — the documented behavior of forgetting the second variable. |
| set | a real address/subnet not matching the request | `401` | The ordinary, intended-secure state — this is the one every example above configures. |
| set | `0.0.0.0/0` or `::/0` | *(no request ever answers)* | **Refuses to start.** No real proxy's own address is ever "the entire internet" — this is always a mistake, and worse than auth being off, so it is fatal rather than logged. |

`check_auth_configuration_sanity` (in `fermata/authproxy.py`) is what logs
the one row above that still starts. `check_trusted_proxies_are_not_everyone`
is what refuses to start for the `0.0.0.0/0` / `::/0` row — the same fatal
bucket `check_proxy_header_safety` is in for the proxy-headers guard, not a
warning. Every `401` row, and the `0.0.0.0/0` refusal, are exercised
directly in `server/tests/test_authproxy.py`.

### What is and isn't covered

Once both variables are set, **every route requires the header** — the API
and the browser app it serves both — with exactly one exception:
`GET /api/health` stays open with no header at all, because that is what
Docker's own `HEALTHCHECK` (see the `Dockerfile`) polls from inside the
container, and it would be a strange kind of security to let a
misconfiguration here turn a healthy container "unhealthy" and drop it into
Compose's restart loop. `/docs` and `/openapi.json` are **not** exempt —
turning this on locks those down along with everything else, so a request
without a trusted header sees a plain `401` with a short JSON message, never
a stack trace or a half-loaded page.

A request from a trusted proxy carrying no header, or an empty one, is
refused the same way — a proxy that is supposed to authenticate visitors but
was itself misconfigured (auth disabled on its side, a typo in the header
name it forwards) fails closed here too, rather than Fermata quietly letting
the request through unauthenticated. So is a request where the header
arrives more than once — see the warning box above on why a proxy that
appends rather than replaces can produce that shape, and why Fermata will
not guess which of two values is the real one.

The username Fermata reads is logged (at request time, alongside what was
rejected and why when auth fails) and available at `GET /api/me`:

```json
{"enabled": true, "username": "alice"}
```

`enabled` reports whether reverse-proxy auth is turned on at all, independent
of whether this particular request carried an identity — with it off, or on
but reaching this endpoint through a different path than your proxy, that is
`{"enabled": false, "username": null}` rather than an error. **Nothing in
Fermata acts on this identity today** — there are no per-user permissions,
no filtering of what a given username can see, and this does not turn
Fermata into a multi-user application. It is read, logged, and left there
for a consumer that might one day act on it — a sharing layer, say. The
[Model Context Protocol server](#the-model-context-protocol-server) is not
one: it leaves this identity inert, and Fermata refuses to run the two
features together in any case. (A few database tables
already carry an `owner` column reserved for that future, every row
currently written as the single placeholder owner `local` — wiring a real
username into it today would only orphan your own data from the very
queries that read it back, which is a bigger feature than reverse-proxy
authentication is meant to be.)

### Example: Caddy

This is the config actually used to verify this feature — run for real
against a built image (with the Dockerfile's `--no-proxy-headers` CMD
included), a Caddy container in front of it on its own Docker network, and
curled from the host: a request straight to Fermata's own port with a
forged header came back `401`; the SAME request with a forged
`X-Forwarded-For` header naming Caddy's own trusted address ALSO came back
`401` (the specific bypass a security review found and this section's
"proxy-header trust must stay off" note above exists because of); the
request actually routed through Caddy with a valid login came back `200`
carrying the right username; and the container's own `HEALTHCHECK` stayed
`healthy` throughout.

`Caddyfile`, exactly as tested (`:8080` rather than a domain name, matching
the rest of this guide's LAN-only, no-TLS setup — replace `:8080` with your
own domain if you have one and want Caddy's automatic HTTPS on top of this):

```
:8080 {
	basic_auth {
		alice JDJhJDE0JEV4YW1wbGVIYXNoR29lc0hlcmUuLi4
	}
	reverse_proxy fermata:8080 {
		header_up X-Remote-User {http.auth.user.id}
	}
}
```

Generate a real password hash for that `basic_auth` line rather than typing a
password in plainly:

```bash
docker run --rm caddy:2-alpine caddy hash-password --plaintext 'your password here'
```

(Caddy's `basic_auth` is a minimal example that needs no separate service —
swap it for `forward_auth` pointed at Authelia or authentik if you want a
real login page, shared sessions, and more than one user; the `header_up`
line stays the same either way, since it is Caddy that sets the header for
Fermata, not whatever authenticated the visitor.)

Add Caddy as a second service in `docker-compose.yml`, and — this matters —
**stop publishing Fermata's own port to the host once Caddy is in front of
it**, so the only way in is through the proxy that is actually checking
logins:

```yaml
services:
  fermata:
    # no "ports:" here anymore - only reachable from other containers on
    # this compose network, which is what makes FERMATA_TRUSTED_PROXIES
    # below meaningful rather than cosmetic.
    environment:
      FERMATA_AUTH_HEADER: X-Remote-User
      FERMATA_TRUSTED_PROXIES: 172.28.1.10/32
    volumes:
      - ./library:/data/library
      - ./config:/data/config
    restart: unless-stopped

  caddy:
    image: caddy:2-alpine
    ports:
      - "8080:8080"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy-data:/data
    restart: unless-stopped
    networks:
      default:
        ipv4_address: 172.28.1.10

networks:
  default:
    ipam:
      config:
        - subnet: 172.28.1.0/24

volumes:
  caddy-data:
```

Caddy is given a fixed address (`172.28.1.10`) on the compose network
specifically so `FERMATA_TRUSTED_PROXIES` can name that one address rather
than the whole network range — a request that reaches Fermata directly
(bypassing Caddy some other way) does not arrive from `172.28.1.10` even if
it is on the same network, and is refused exactly as a request from the
open internet would be. This is the same reason Fermata's own `ports:` is
removed above: with it still published, a request to that port would look,
from inside the container, like it came from the Docker network's gateway
address rather than from Caddy — which a broader trusted range would wrongly
accept. Naming Caddy's own address, and taking Fermata off the host network
entirely, closes both gaps at once.

### Example: Authelia

Authelia and authentik both work the same way — they sit in front of
Fermata behind a reverse proxy (commonly Caddy, nginx, or Traefik) and set a
`Remote-User` header once a visitor authenticates. This example is
`docker-compose.yml` and Caddyfile syntax; it has been checked for correct
syntax but **not run end-to-end** the way the plain-Caddy example above was
— standing up Authelia's own session store and configuration is
disproportionate to verify in this repository, so treat this as a starting
point to adapt rather than a copy-paste guarantee.

`Caddyfile`, with Authelia running as a third container reachable at
`authelia:9091`:

```
fermata.example.com {
	forward_auth authelia:9091 {
		uri /api/verify?rd=https://auth.example.com
		copy_headers Remote-User
	}
	reverse_proxy fermata:8080 {
		header_up X-Remote-User {http.request.header.Remote-User}
	}
}
```

`docker-compose.yml` gains an `authelia` service (see
[Authelia's own deployment docs](https://www.authelia.com/integration/deployment/docker/)
for its configuration file, which is more involved than Caddy's and out of
scope here) alongside the same `fermata` and `caddy` services as the plain
example — Fermata's own environment and missing `ports:` are unchanged:

```yaml
services:
  fermata:
    environment:
      FERMATA_AUTH_HEADER: X-Remote-User
      FERMATA_TRUSTED_PROXIES: 172.28.1.10/32
    # ... volumes, restart, networks as above - no "ports:"

  caddy:
    # ... as above, with the Caddyfile shown here instead

  authelia:
    image: authelia/authelia:latest
    volumes:
      - ./authelia:/config
    networks:
      default:
        ipv4_address: 172.28.1.11
```

## The Model Context Protocol server

Fermata can offer its library and practice history as a set of **read-only
tools** over the Model Context Protocol — an open standard for describing
tools to a program that reads them. It is a way to let an external tool ask
"what's in the library" or "how much did I practise last week" without
anyone writing HTTP requests by hand.

**This is off by default, and stays off after an upgrade unless you turn it
on.** Nothing below runs, and nothing listens, until you set `FERMATA_MCP`.

### What it does and does not do

- **Read only.** The tools list and search scores, read a score's metadata
  and its transcription status, read practice history and summaries, read
  goals, and read trainer attempts. There is no tool that changes anything —
  not "log a session", not "rename a score", not "delete". That is not a
  setting; there is no code path in it that can send anything but a `GET`.
- **It wraps the REST API, it does not replace it.** Every tool is one
  documented route from [the REST API](api.md), called over ordinary HTTP,
  and every answer is that route's own JSON handed back unchanged. The tool
  descriptions and their input schemas are generated from the same
  `/openapi.json` the API serves, at startup, so a tool cannot describe a
  route that no longer looks like that.
- **It has no login of its own.** Anything that can reach the port can read
  your whole library and practice history. That is the same trust model as
  Fermata's own web interface (see [Current
  limitations](#current-limitations)), and it is why the listener stays on
  `127.0.0.1` — inside the container only — unless you deliberately move it.

### It does not work with reverse-proxy authentication

**Fermata refuses to start if you set both `FERMATA_MCP` and
`FERMATA_AUTH_HEADER`**, and will tell you so by name in the log. The two
are not supported together in this release.

The reason is the first bullet above, from the other side. The tools read
the REST API as an ordinary anonymous client over loopback — that is what
"wraps the documented routes" means, and it is what keeps this layer from
being a second copy of the API. With
[reverse-proxy authentication](#reverse-proxy-authentication) turned on,
every request that did not come from your trusted proxy carrying the
identity header is refused, and the tools' requests are exactly that. So
every tool would answer `401` while the tool list went on advertising
thirteen working tools — a failure with no symptom except an emptiness that
looks like an empty library.

The two obvious workarounds are both worse than the fault, which is why
neither is offered. Adding `127.0.0.1` to `FERMATA_TRUSTED_PROXIES` does not
fix it (the internal client still sends no identity header, so the request
is refused a second time) and it does mean anything else running on that
machine can now set the identity header itself and be believed. Having
Fermata's own internal client send an identity header is worse still: it
would turn the trusted header into something a process on the box can mint,
which is the exact forgery reverse-proxy authentication exists to prevent.

So: pick one. Keep your login and leave `FERMATA_MCP` unset, or use the
tools and leave `FERMATA_AUTH_HEADER` unset with the port reachable only
from somewhere you trust.

### Turning it on

Add the environment variable to the `fermata` service in
`docker-compose.yml` and publish a second port:

```yaml
services:
  fermata:
    # ... build, volumes, restart as in "Getting it running"
    ports:
      - "8080:8080"
      - "127.0.0.1:8765:8765"
    environment:
      FERMATA_MCP: "1"
      FERMATA_MCP_HOST: 0.0.0.0
```

Note the `127.0.0.1:` on the second port — without it Docker publishes that
port to your whole local network, the way port 8080 above is published, and
this one has no login in front of it at all. Start with it reachable only
from the machine running the container, and widen it deliberately (drop the
prefix) once you have decided who should be able to read your library.

Then rebuild and restart:

```bash
docker compose up -d
```

The tools are then reachable at **http://127.0.0.1:8765/mcp**, over the
protocol's Streamable HTTP transport. Point a client that speaks the Model
Context Protocol at that URL; it will list thirteen tools, each named after
what it reads (`list_scores`, `get_practice_summary`, and so on).

The four settings, only the first of which turns anything on:

| Variable | Default | What it does |
| --- | --- | --- |
| `FERMATA_MCP` | unset (off) | Set to `1` to run the server at all. Every other setting here is inert while this is unset. |
| `FERMATA_MCP_HOST` | `127.0.0.1` | What the listener binds. Inside a container the default means "this container only" — set it to `0.0.0.0` if you are publishing the port, as above. |
| `FERMATA_MCP_PORT` | `8765` | The port it listens on. |
| `FERMATA_MCP_API_URL` | `http://127.0.0.1:8080` | Where it finds Fermata's own REST API. The default is right for the container; change it only if you are running from source and told `uvicorn` to use a different port. |

`FERMATA_MCP_HOST` defaulting to loopback is deliberate: publishing this is
two decisions (set the host, map the port), not one, so it is hard to expose
by accident. Do not publish that port to the open internet — put it behind
the same reverse proxy and login as everything else, or leave it on your own
network.

### Checking it, and what failure looks like

You can see exactly which tools it would offer without connecting anything:

```bash
docker compose exec fermata python -m fermata.mcp_server
```

That prints each tool with the route it reads and the arguments it takes.

If the port you chose is already taken, **the container will not start**,
and `docker compose logs fermata` will say so in a sentence naming the port.
That is on purpose: a container that came up healthy while the feature you
just turned on silently did nothing is the worse outcome. Change
`FERMATA_MCP_PORT`, or unset `FERMATA_MCP`, and it starts again — nothing in
your library or your practice history is touched either way.

### Running it from source

The protocol library is an optional extra, so a source install needs it
asked for by name:

```bash
pip install -e "server[mcp]"
FERMATA_MCP=1 FERMATA_MCP_API_URL=http://127.0.0.1:8000 \
  uvicorn fermata.main:app --port 8000 --no-proxy-headers
```

The container image already includes the extra, which is why turning the
feature on there is an environment variable rather than a rebuild.

## Backups

This is the section to actually act on, not just read. Everything in
`library/` is your own files — if you lost them, you'd still have the
originals somewhere. Everything in `config/` is not recoverable any other
way: your practice history, your tags, and any hand-corrected tab
transcriptions live only in the database in that folder. Losing `config/`
without a backup means losing that work, even though every PDF is still
sitting untouched in `library/`.

### The one folder in `library/` that is Fermata's

Deleting a score in Fermata does not delete the file. It moves it to
`library/.fermata-trash/`, and the score — with its practice history, tags,
goals and transcription — stays in the database, listed under **Trash**, until
you either put it back or destroy it deliberately. Three things follow:

- **Scans ignore that folder entirely**, so nothing in it is ever taken for a
  score in your library, and nothing you deleted quietly comes back.
- **It counts toward your disk usage** until you empty it. Nothing empties it
  on a timer; a deleted score stays deleted-not-destroyed for as long as you
  leave it.
- **If you back up `library/` too, back up that folder with it** — or, if you
  would rather not carry deleted files around, empty the Trash view first.
  Deleting the folder by hand is safe for your database: nothing is lost from
  it, and the scores can still be put back. What comes back in that case is the
  score — its practice history, tags, goals and transcription — flagged as
  **file missing**, which is the same state any score whose file has gone shows.
  Put the file back in your library, scan, and it recovers by itself. The bytes
  you deleted by hand are gone, though; Fermata cannot get those back.

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

**`GET /api/export`** is the other way to get a backup, and it does not need
the container stopped: it is a live endpoint, called over the network, that
answers with one zip holding every row Fermata keeps — scores, transcriptions,
practice sessions, goals, setlists, tags, instruments, settings and drill
history — plus the score files themselves: a portable archive rather than a
copy of the database file. It
is the better choice for scripting a backup onto another machine, or for
taking one without touching the host filesystem at all; copying `config/` is
the better choice for a quick local snapshot before an upgrade. See [the API
guide](api.md#getting-everything-in-and-out-issue-58) for the archive's shape
and what restoring it (`POST /api/import`) does and does not do.

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
startup scan reconciles it against whatever is currently in `library/`. That
reconciliation cannot cost you anything: a score whose file is not found is
marked as missing rather than removed, so its practice history, tags and
transcription stay attached and come back with the file.

## Upgrading

To upgrade, pull the new version and rebuild:

```bash
git pull
docker compose up --build -d
```

Check the build tag in the sidebar (or `GET /api/version`) before assuming an upgrade didn't take or reporting a bug for a feature you expect to see — a container that is still on the old image looks exactly like a missing feature, and this is the fastest way to tell the two apart.

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

**The version that introduced missing-file tracking is one of those**, and it is
worth knowing why rather than only that. Older versions deleted a score row
whenever a scan did not find its file — taking the practice history, tags and
transcriptions attached to it. Newer ones mark the row instead. An older version
run against a newer database would not fail on the column it does not know
about: it would ignore it, read every marked score as though its file should be
there, not find it, and delete the lot. So the refusal above is not bureaucracy,
it is the thing standing between a rollback and your practice history. If you
need to go back, restore the `config/` backup as well.

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
guess. Where one voice of a bar does have to be filled out with silence — a
voice that enters halfway through the bar has to start halfway through the bar,
or its notes play early — the transcription names the bars that happened in,
counts that silence as missing rather than as read, and marks it in the
MusicXML so another program can tell it from a rest that was printed.

**A harmonic is marked but not sounded as one.** A note the engraving marks
as a harmonic — a diamond notehead, or a fret number in guillemets — is
written into the transcription and carries `<harmonic>` in the MusicXML. What
does not follow it is the sound: the built-in player discards that element,
so it plays the fretted pitch rather than the harmonic, and a harmonic
engraved as a half note is still read at a quarter's length. This is a known,
current gap in what gets extracted and rendered, not a display setting.

**There is one user, and no login.** Fermata does not have accounts, a
sign-in screen, or any way to separate what a teacher sees from what a
student sees. Anyone who can open the address in a browser has the same
access as you do — they can browse and download everything in your library,
upload new files, and edit tags, metadata, and practice records.

Because of that, Fermata belongs on a home network you trust, not on the open
internet. If you forward its port to the internet or otherwise expose it
publicly, anyone who finds it — and on the open internet, something usually
does find an open port before long — gets that same unrestricted access with
no username or password standing in the way, UNLESS you have set up
[reverse proxy authentication](#reverse-proxy-authentication): it puts a real
login in front of Fermata via a proxy like Caddy, Authelia, or authentik, but
it is off by default and does nothing until you configure it. It also stays
what its name says — everyone who logs in still sees the same one library,
the same tags, the same practice history, all of it fully shared. There is no
per-user privacy or permissions inside Fermata itself, and no plan to build
that; a login only decides who is allowed in at all, not what any of them
can see once they are.

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

There are three other reasons Fermata will stop on purpose, and because
`docker-compose.yml` sets `restart: unless-stopped`, any one of them shows up as
a container restarting over and over. All print a plain explanation, so it is
worth reading to the end of the log rather than only the last line.

**"its library folder … is not there"** — the `library/` folder Fermata was
told to use does not exist. This one is worth understanding rather than just
fixing, because Fermata refuses it deliberately and used to do something much
worse. A missing library folder is almost always a mount that did not appear:
the host folder was renamed or moved, an external drive did not come back, or
the container started before its volume was ready. Fermata will not create the
folder for you — an empty library folder looks exactly like a library with
nothing in it, and Fermata would have no way to tell the difference.

Check that the folder is there, next to your `docker-compose.yml`, and that
`docker-compose.yml` still has `./library:/data/library` under `volumes:`:

```bash
ls -la library
```

If the folder genuinely should exist and be empty — a first run before you have
copied any music in — create it and start again:

```bash
mkdir -p library
docker compose up -d
```

A restart loop here is harmless and self-healing: nothing has been changed, and
the moment the mount appears the next restart attempt succeeds by itself. This
is on purpose. Your practice history, tags and transcriptions in `config/` are
not touched by any of it.

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

If the listing comes back empty **and** Fermata was already showing your
scores before, look in the log instead:

```bash
docker compose logs fermata --tail 50
```

You should find a line saying the scan "did not reconcile the library", with a
reason — and the library page will be showing you the same reason in a panel at
the top, headed **Fermata did not update your library**. Fermata refuses to act
on a walk of the library it cannot believe: a folder that suddenly holds no
readable score files at all, one that has lost half or more of what it had in a
single pass, or one that has lost half or more of what it held when it was last
whole. All three are far more often a mount problem than news about your music,
so nothing at all is changed — not one score added, updated or marked — and the
panel lists the files it could not find so you can see which part of your library
it means.

Fix the mount and scan again; everything comes back on its own.

**If you did mean it**, press **Yes, I meant to do that** in that panel. Pruning
your library, moving a collection to another drive, or re-exporting everything
under new names all look exactly like a mount problem from the inside, and
Fermata cannot tell the difference — so it asks once rather than guessing. There
has to be a way to say yes: the same files are absent on every later scan, so
without it the refusal would repeat for ever and nothing you could do would clear
it. Confirming still deletes nothing. Files that have moved are matched back to
their own score by content, and the rest are marked as missing.

### A score marked "file missing"

A score card showing **file missing** means Fermata cannot find the file, and
nothing more than that. The score is still there and still opens; its tags, its
practice history and any hand-corrected transcription are all still attached to
it, and the sidebar shows how many of each collection's files are in this state.

Put the file back — under the old name or a new one, Fermata matches it by
content — and the next scan clears the mark by itself. Nothing is ever deleted
because a file went away.

Two things worth knowing about it:

- If a file was **edited and moved** at the same time, Fermata cannot tell it is
  the same piece: the content it would match on has changed. You get the old
  score marked missing, holding your practice history and tags, and a new score
  for the new file with none. Nothing is lost, but they are two scores until a
  future release lets you merge them.
- A score whose file is missing is left out of **Needs attention**, because it
  cannot be practised. It stays in the main library view and in **Recently
  practiced** — practice that happened does not stop having happened.

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
