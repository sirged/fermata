# Security

## Reporting a vulnerability

Please report security problems privately, not as a public issue.

Use GitHub's private vulnerability reporting: go to the **Security** tab of this
repository and choose **Report a vulnerability**. That opens a private thread
visible only to the maintainer.

Please include what you did, what happened, and what an attacker could get out
of it. A working proof of concept is welcome but not required.

You'll get a first response within a week. If a fix is needed, you'll be told
what it is and when it ships, and you'll be credited unless you'd rather not be.

## What's in scope

Fermata is a self-hosted server that reads files you give it and serves them to
your own browser. The interesting attack surface is roughly:

- Parsing untrusted score files. Import reads PDFs and other formats with
  third-party libraries; a malicious file causing something worse than a failed
  import is worth reporting.
- Path handling on upload and on serving library files, including anything that
  escapes the configured library directory.
- Anything that lets a request read or write outside the library and config
  volumes.

## What isn't

- Fermata ships with no authentication and assumes it sits on a network you
  control, or behind a reverse proxy that handles access. "Anyone who can reach
  the port can use it" is the documented design, not a vulnerability.
- Denial of service through deliberately enormous or pathological files. Worth
  an ordinary issue, not a security report.
- Vulnerabilities in a dependency with no demonstrated path through Fermata.
  Those are better reported upstream, though a note here is welcome.

## Supported versions

This is a young project with no release branches yet. Fixes go to `main`.
