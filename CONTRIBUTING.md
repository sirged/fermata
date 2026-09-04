# Contributing

Thanks for looking. Fermata is developed in the open, and issues are genuinely
useful even when they don't come with a patch.

## Issues are welcome

Bug reports, feature ideas, and "this didn't work with my library" reports are
all wanted. For a bug, the most useful thing you can include is what kind of
PDF or score file was involved — how it was produced matters more than almost
anything else, because import behaviour depends heavily on how a file was
engraved.

Please don't attach copyrighted sheet music to an issue. Describe the file
instead: what program exported it, whether it's a scan or vector output,
whether it has a tab staff.

## Pull requests

Changes land through pull requests, which are reviewed before merging. `main` is
protected, so this applies to everyone including the maintainer.

Before opening one:

- Open or find an issue first for anything beyond a small fix, so the approach
  can be agreed before you spend time on it.
- Keep the change focused. A pull request that does one thing gets reviewed;
  one that does five things stalls.
- Run the checks locally (below). CI runs the same ones.
- Explain how you verified the change, not just what you changed. For anything
  touching score import, "the tests pass" is weaker evidence than "here is what
  it produced for this kind of file, and here is why that's correct."

## Running the checks

Backend:

```bash
cd server
pip install -e ".[dev]"
python -m pytest -q
```

Some tests need a folder of sheet music and skip without one. To include them,
point `FERMATA_TEST_LIBRARY` at a directory of your own files.

Frontend:

```bash
cd web
npm ci
npm run build
```

Frontend tests — unit specs and the browser suite, which drives a real backend
serving the build, so `npm run build` has to have run first:

```bash
cd web
npx playwright install --with-deps chromium
npm run test:browser
```

Whole thing, as it actually ships:

```bash
docker compose up --build
```

## A note on accuracy

A lot of this project reads music out of files that were never meant to be
read programmatically, so it is frequently uncertain. The rule throughout is
that uncertainty gets reported rather than hidden: it is better for the app to
say a rhythm may be wrong than to present a confident guess. If you add
something that infers, please also add the honest caveat that goes with it.
