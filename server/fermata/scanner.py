import hashlib
import logging
import threading
import time

from .config import FILE_TYPES, LIBRARY_DIR
from .db import connect
from .metadata import musicxml_info, parse_path
from .thumbs import generate_pdf_thumb, pdf_info

# The scan is the one thing in this application that runs with nobody watching -
# the lifespan hook starts one on every boot, unprompted - so what it decides
# has to end up somewhere a person can read afterwards. scan_status() carries it
# for the interface; this carries it for the log, which is the only record that
# survives a container going away.
log = logging.getLogger("fermata.scanner")

_state = {
    "scanning": False,
    "total": 0,
    "processed": 0,
    "added": 0,
    "updated": 0,
    # Rows whose file was not found this pass, and which this pass therefore
    # marked missing. This key used to be called `removed` and used to mean
    # rows deleted; nothing deletes a score row on the strength of a
    # filesystem walk any more, so the old name would now be a count of
    # something that never happens. See _reconcile.
    "missing": 0,
    # Rows that were marked missing and whose file came back. Counted and
    # reported because it is the good half of the same story, and because it is
    # the evidence that a remount really did recover.
    "restored": 0,
    # Set when this scan declined to mark anything missing because the evidence
    # was not believable. `refused` is the flag to branch on; `refused_reason`
    # is written to be shown to a person as it is.
    "refused": False,
    "refused_reason": None,
    "errors": 0,
    "last_error": None,
    "started_at": None,
    "finished_at": None,
}
_state_lock = threading.Lock()

# WHEN A SCAN REFUSES TO BELIEVE ITSELF.
#
# A scan's removal pass acts on an absence, and an absence is exactly the
# evidence a broken mount fabricates. Two thresholds, and the reasoning for
# both, because a number like this is worthless without it.
#
# ZERO FILES, WITH SCORES ON RECORD, IS CATEGORICAL. There is no library that
# both used to have scores in it and now genuinely contains not one readable
# file - not one PDF, not one MusicXML - arrived at between two startups. A
# person emptying their library deliberately does it a folder at a time and
# would not be surprised by a message saying so. A mount that is not there
# produces precisely this reading, every time. No fraction is involved and none
# should be: it is a different claim from "most of the library went".
#
# A HALF IS THE PROPORTIONAL LINE. Below it, the loss is consistent with
# ordinary use - somebody deleted a collection, moved a series out to another
# drive, reorganised in bulk. Above it, "I removed more of my library than I
# kept, between two startups, without mentioning it" is a much rarer event than
# "part of the tree was not readable", which is what a partly-present mount
# looks like: one subdirectory back, the rest gone. Half is also where the two
# mistakes stop being comparable in cost. Refusing wrongly costs a message and
# a rescan once the mount is fixed, and the rows it declined to touch are
# exactly right in the meantime. Accepting wrongly marks most of the library
# missing, which makes the index untrustworthy at a glance and hands a future
# "forget the missing ones" action a loaded gun.
#
# AND A FLOOR, because a proportion of a small number says nothing. Losing one
# of two scores is fifty per cent and completely unremarkable; a library of
# three that becomes a library of one is a Tuesday. Below the floor the
# proportional test is switched off and only the categorical zero-files test
# applies. Ten is chosen as the point where "half of them" starts describing a
# pattern rather than a coincidence, and the exposure below it is small and, in
# any case, not a deletion.
#
# WHAT THE FLOOR COSTS, stated rather than discovered later. A library under it
# can be drained entirely, a little at a time, without the proportional test
# ever firing - the last file is caught by the zero-files test and nothing
# before it is. Accepted, because every step of that road is a mark: nothing is
# deleted, the practice, tags and transcriptions stay attached to their rows,
# and one good scan restores the lot. The proportion is compared against the
# rows believed PRESENT rather than every row on record, which is what stops
# this from becoming a way to launder a large loss through several passes on a
# library big enough for the test to apply.
LOSS_FRACTION = 0.5
LOSS_FLOOR = 10


def scan_status() -> dict:
    with _state_lock:
        return dict(_state)


def _hash_file(path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _refuse(reason: str) -> None:
    """Record that this scan declined to act on what it saw, and say why.

    Written into the state the interface polls AND to the log, because these are
    two different audiences with two different lifetimes: the person watching a
    scan they started, and the person a week later working out what happened
    while nobody was watching.
    """
    with _state_lock:
        _state["refused"] = True
        _state["refused_reason"] = reason
    log.error("scan did not reconcile the library: %s", reason)


def _implausible(found: int, on_record: int, unseen: int) -> str | None:
    """Is this walk of the library believable as a description of the library?

    Returns the reason it is not, phrased for a person, or None if it is. See
    LOSS_FRACTION and LOSS_FLOOR above for why these two tests and not others.
    """
    if on_record == 0:
        # Nothing to lose. A genuinely empty library on a first run lands here,
        # and must not be treated as a fault.
        return None
    if found == 0:
        return (
            f"the library folder {LIBRARY_DIR} contains no readable score files at all, "
            f"but Fermata has {on_record} score(s) on record there. That is what an "
            "unmounted drive or a missing bind mount looks like, not what an emptied "
            "library looks like, so nothing has been changed. Your practice history, "
            "tags and transcriptions are untouched. Check the folder is mounted and "
            "readable, then scan again - if you really did empty the library, the next "
            "scan will say the same thing until at least one score is back."
        )
    if on_record >= LOSS_FLOOR and unseen >= on_record * LOSS_FRACTION:
        return (
            f"{unseen} of the {on_record} score(s) on record were not found in "
            f"{LIBRARY_DIR} this time - half or more of the library in a single pass. "
            "That is far more likely to be part of the folder not being readable than a "
            "library that shed most of itself between two startups, so nothing has been "
            "changed. Your practice history, tags and transcriptions are untouched. If "
            "you did mean to remove that much, scanning again after the next change will "
            "go through."
        )
    return None


def _scan() -> None:
    conn = connect()
    with _state_lock:
        _state.update(
            total=0,
            processed=0,
            added=0,
            updated=0,
            missing=0,
            restored=0,
            refused=False,
            refused_reason=None,
            errors=0,
            last_error=None,
        )

    # Checked here as well as at startup, and not only for tidiness: the library
    # can go away while the application is running (an unmount, a drive
    # sleeping, a network share dropping), and a scan can be triggered by hand
    # or by an upload long after boot. rglob over a path that is not there
    # returns nothing at all rather than raising, so without this the walk would
    # come back empty and look exactly like an empty library.
    if not LIBRARY_DIR.is_dir():
        _refuse(
            f"the library folder {LIBRARY_DIR} is not there, or is not a folder. Nothing "
            "has been changed. This is a mount or configuration problem rather than "
            "anything about your scores - see docs/deployment.md."
        )
        return

    files = [
        p
        for p in LIBRARY_DIR.rglob("*")
        if p.is_file() and p.suffix.lower() in FILE_TYPES
    ]
    # Fixed for the whole scan: lets _scan_file tell a genuinely-removed row
    # (safe to re-link on a hash match) from one whose file just hasn't been
    # visited yet in this pass.
    disk_paths = {p.relative_to(LIBRARY_DIR).as_posix() for p in files}
    with _state_lock:
        _state["total"] = len(files)

    seen_paths = set()
    for path in files:
        rel = path.relative_to(LIBRARY_DIR).as_posix()
        try:
            _scan_file(conn, path, rel, seen_paths, disk_paths)
        except OSError as exc:
            # A file can vanish or lock between listing and reading it; skipping
            # keeps the rest of the scan (and the reconciliation below) running.
            with _state_lock:
                _state["errors"] += 1
                _state["last_error"] = f"{rel}: {exc}"
        with _state_lock:
            _state["processed"] += 1

    _reconcile(conn, len(files), seen_paths)
    conn.commit()


def _reconcile(conn, found: int, seen_paths: set) -> None:
    """Bring the rows for files this scan did not see into line with that fact.

    THIS USED TO BE A DELETE, and that delete is #95: three tables cascade from
    scores, so a walk that came back short took practice history, tags and
    hand-corrected transcriptions with it - the only things here that cannot be
    regenerated from the files on disk. The scores themselves always came back
    on the next good scan, because they are files. Nothing else did.

    It is now a mark. A file's absence is a fact about the filesystem, and the
    right record of it is a fact about the filesystem: scores.missing_since. The
    row, and everything hanging off it, stays exactly where it was. That is what
    makes a remount recover by itself, and it is also the only version of this
    that survives the two triggers no threshold can catch - a file edited AND
    moved in the same window (the content hash changed, so the rename relink has
    no candidate to match) and duplicate content with one copy renamed (more
    than one candidate, so the relink rightly declines to guess). In both, the
    file really is there and simply cannot be matched to its row. Marking the
    old row missing and inserting the new one loses nothing and states the
    truth: Fermata does not know these are the same piece. Deleting the old row
    lost a year of practice to a rename.

    The scan can still refuse to do even this much - see _implausible. A partly
    readable library would otherwise mark hundreds of rows missing, which is not
    destructive but is a false statement about somebody's library, and false at
    exactly the moment they are trying to work out what went wrong.
    """
    # Only rows currently believed present are candidates, and only they count
    # towards the proportion. Rows already marked missing are neither news nor
    # evidence: counting them would let a long-broken mount drift the
    # denominator until the proportional test could not fire.
    on_record = conn.execute(
        "SELECT id, path FROM scores WHERE missing_since IS NULL"
    ).fetchall()
    unseen = [row for row in on_record if row["path"] not in seen_paths]

    reason = _implausible(found, len(on_record), len(unseen))
    if reason is not None:
        _refuse(reason)
        return

    for row in unseen:
        # datetime('now') for the same reason every other timestamp in this
        # schema uses it: one clock, UTC, and no dependence on the caller.
        conn.execute(
            "UPDATE scores SET missing_since = datetime('now') WHERE id = ?", (row["id"],)
        )
    with _state_lock:
        _state["missing"] = len(unseen)

    if unseen:
        # A scan that quietly took away a large part of the library was the
        # other half of #95: the count went into the state dict and nothing
        # ever presented it as the alarming thing it is. Said at warning level,
        # with enough of the paths to recognise which part of the library it
        # was, because "297 scores" and "297 scores, all under Patreon/" are
        # very different messages to wake up to.
        sample = ", ".join(row["path"] for row in unseen[:5])
        log.warning(
            "scan marked %s of %s score(s) missing: their files are no longer in %s. "
            "Nothing has been deleted - practice history, tags and transcriptions are "
            "still attached, and a scan that finds the files again will restore them. "
            "First few: %s%s",
            len(unseen),
            len(on_record),
            LIBRARY_DIR,
            sample,
            " ..." if len(unseen) > 5 else "",
        )


def _scan_file(conn, path, rel: str, seen_paths: set, disk_paths: set) -> None:
    seen_paths.add(rel)
    stat = path.stat()
    row = conn.execute(
        "SELECT id, size, mtime, missing_since FROM scores WHERE path = ?", (rel,)
    ).fetchone()
    if row and row["missing_since"] is not None:
        # The file came back at the path it left from - a remount, or a restore.
        # Cleared BEFORE the unchanged-file shortcut below, not after: a
        # remounted file has the same size and mtime it always had, so the
        # shortcut would return with the row still marked missing and the mark
        # would then be permanent, surviving every subsequent scan. This is the
        # one line that makes "a remount recovers by itself" true rather than
        # nearly true.
        conn.execute(
            "UPDATE scores SET missing_since = NULL WHERE id = ?", (row["id"],)
        )
        conn.commit()
        with _state_lock:
            _state["restored"] += 1
    if row and row["size"] == stat.st_size and row["mtime"] == stat.st_mtime:
        return

    file_type = FILE_TYPES[path.suffix.lower()]
    file_hash = _hash_file(path)
    pages, pdf_title, pdf_creator = (None, None, None)
    if file_type == "pdf":
        pages, pdf_title, pdf_creator = pdf_info(path)
        generate_pdf_thumb(path, file_hash)
    meta = parse_path(rel, pdf_title, pdf_creator)
    if file_type == "musicxml":
        xml_title, xml_composer = musicxml_info(path)
        if xml_title:
            meta.title = xml_title
        if xml_composer:
            meta.composer = xml_composer
        # Semantic files always support both display modes.
        meta.content_kind = "both"

    if row:
        conn.execute(
            """UPDATE scores SET hash=?, size=?, mtime=?, pages=? WHERE id=?""",
            (file_hash, stat.st_size, stat.st_mtime, pages, row["id"]),
        )
        with _state_lock:
            _state["updated"] += 1
    else:
        # No row at this path. Before treating it as a brand-new file, check
        # whether it's actually a rename/move of an existing row: same content
        # hash, and the row's old path is nowhere on disk this scan. Re-linking
        # by id (instead of insert-and-leave-the-old-row-behind) keeps the tags,
        # the transcription and the practice attached to the piece a person
        # would say it is still about. Only do this when exactly one such
        # candidate exists - with two or more (genuine duplicate content, one
        # copy renamed) there's no way to know which one moved, so fall back to
        # an insert and let the reconciliation below mark whichever one truly
        # vanished. That fallback is no longer lossy: see _reconcile.
        candidates = [
            r
            for r in conn.execute(
                "SELECT id, path, missing_since FROM scores WHERE hash = ?", (file_hash,)
            ).fetchall()
            if r["path"] not in disk_paths
        ]
        if len(candidates) == 1:
            old_id = candidates[0]["id"]
            if candidates[0]["missing_since"] is not None:
                # A previously-missing row reached by relink rather than by
                # path, which is what a drive coming back with things moved
                # around looks like. It counts as restored for the same reason
                # the by-path case does: the file is back and the row is whole.
                with _state_lock:
                    _state["restored"] += 1
            # missing_since is cleared here too. A relink can land on a row that
            # a previous scan marked missing - a drive that came back with the
            # files under new names is the ordinary case - and a row whose file
            # this scan is looking at is, by definition, not missing.
            conn.execute(
                """UPDATE scores SET title=?, composer=?, collection=?, series=?, source=?,
                   path=?, file_type=?, content_kind=?, pages=?, hash=?, size=?, mtime=?,
                   missing_since=NULL
                   WHERE id=?""",
                (
                    meta.title,
                    meta.composer,
                    meta.collection,
                    meta.series,
                    meta.source,
                    rel,
                    file_type,
                    meta.content_kind,
                    pages,
                    file_hash,
                    stat.st_size,
                    stat.st_mtime,
                    old_id,
                ),
            )
            with _state_lock:
                _state["updated"] += 1
        else:
            conn.execute(
                """INSERT INTO scores
                   (title, composer, collection, series, source, path,
                    file_type, content_kind, pages, hash, size, mtime)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    meta.title,
                    meta.composer,
                    meta.collection,
                    meta.series,
                    meta.source,
                    rel,
                    file_type,
                    meta.content_kind,
                    pages,
                    file_hash,
                    stat.st_size,
                    stat.st_mtime,
                ),
            )
            with _state_lock:
                _state["added"] += 1
    conn.commit()


def start_scan() -> bool:
    with _state_lock:
        if _state["scanning"]:
            return False
        _state["scanning"] = True
        _state["started_at"] = time.time()
        _state["finished_at"] = None

    def run():
        try:
            _scan()
        except Exception as exc:
            with _state_lock:
                _state["errors"] += 1
                _state["last_error"] = str(exc)
        finally:
            with _state_lock:
                _state["scanning"] = False
                _state["finished_at"] = time.time()

    threading.Thread(target=run, name="fermata-scan", daemon=True).start()
    return True
