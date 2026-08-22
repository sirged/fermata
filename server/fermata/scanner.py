import hashlib
import logging
import threading
import time

from .config import FILE_TYPES, LIBRARY_DIR
from .db import DEFAULT_OWNER, connect
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
    # rows deleted; nothing deletes a score row on the strength of a filesystem
    # walk any more, so the old name would now be a count of something that
    # never happens.
    "missing": 0,
    # Rows that were marked missing and whose file turned up again AT THE PATH
    # IT LEFT FROM. Deliberately not counting a content-hash relink - see
    # _scan_file for why that would be a claim this cannot support.
    "restored": 0,
    # A pass that both marked rows missing and added new ones: files that moved
    # in a way the relink could not match. Not an error, but not a clean pass
    # either, and it used to read as one.
    "unmatched_moves": 0,
    # Set when this scan declined to change anything because the evidence was
    # not believable. `refused` is the flag to branch on; `refused_reason` is
    # written to be shown to a person as it is; `unmatched_paths` is what it saw
    # (capped, with `unmatched_count` giving the real total); and
    # `acknowledge_token` is what POST /scan/acknowledge takes to say "I meant
    # it". A refusal with no way past it is its own defect.
    "refused": False,
    "refused_reason": None,
    "unmatched_paths": [],
    "unmatched_count": 0,
    "acknowledge_token": None,
    "errors": 0,
    "last_error": None,
    "started_at": None,
    "finished_at": None,
}
_state_lock = threading.Lock()

# WHEN A SCAN REFUSES TO BELIEVE ITSELF.
#
# A scan's reconciliation acts on an absence, and an absence is exactly the
# evidence a broken mount fabricates. Three tests, and the reasoning for each,
# because a number like this is worthless without it.
#
# ZERO FILES, WITH SCORES ON RECORD, IS CATEGORICAL. There is no library that
# both used to have scores in it and now genuinely contains not one readable
# file - not one PDF, not one MusicXML - arrived at between two startups. A
# mount that is not there produces precisely this reading, every time. No
# fraction is involved and none should be: it is a different claim from "most of
# the library went".
#
# A HALF IS THE PROPORTIONAL LINE, and there is exactly ONE proportional test:
# how much of the library would still be accounted for after this pass, against
# the HIGH-WATER MARK - the most scores this library has ever had present at
# once.
#
#   WHY THE HIGH-WATER MARK AND NOT THE CURRENT COUNT. Measured against what is
#   currently present, this guard can be walked past a step at a time, because
#   every permitted pass shrinks the next pass's denominator. 297 scores can go
#   148, 74, 37, 18, 9, 5, 5 without one of those passes ever losing half of
#   what it started with: seven passes, 296 rows marked, no refusal, and a
#   flapping mount that exposes a shrinking subset walks that ladder unaided.
#   Against the high-water mark the same sequence is refused on the second rung,
#   because three quarters of the library is unaccounted for however it got that
#   way. The proportion has to be measured against the library, not against
#   what is left of it.
#
#   WHY "WHAT REMAINS" AND NOT "WHAT IS ABSENT", which are not the same test
#   once a mark has been accepted. Counting everything absent means an
#   acknowledged pruning goes on counting for ever: archive 150 of 200, confirm
#   it, and 150 rows stay absent - so a test on absence fires on every later
#   scan, against a library that no longer exists, and the refusal is permanent
#   again. Counting what remains asks the question that actually matters, "is
#   most of this library reachable", and an acknowledgement answers it by moving
#   the mark down to the new size.
#
#   AND WHY THERE IS NO SEPARATE SINGLE-PASS TEST. There was one, against the
#   rows believed present, and it was removed as provably redundant: the
#   high-water mark is never smaller than the current count, so this test fires
#   whenever a single-pass one would have, and fires in cases it would have
#   missed. Two overlapping tests is also how the first version of this hid a
#   bug - a mutation that loosened the single-pass boundary changed no outcome,
#   because the other test was quietly covering for it.
#
# AND A FLOOR, because a proportion of a small number says nothing. Losing one
# of two scores is fifty per cent and completely unremarkable; a library of
# three that becomes a library of one is a Tuesday. Below the floor the
# proportional test is switched off and only the categorical zero-files test
# applies. Ten is where "half of them" starts describing a pattern rather than a
# coincidence. A library under the floor can therefore still be drained a little
# at a time - accepted, because every step of that road is a MARK: nothing is
# deleted, the practice, tags and transcriptions stay attached, and one good
# scan restores the lot.
#
# NONE OF THESE IS A DEAD END. Every refusal publishes a token, and
# acknowledging it runs the scan again with the guard stood down for exactly the
# evidence that was shown. Without that, a person who genuinely did prune their
# library would be refused for ever: the same paths are unmatched on every
# subsequent pass, so the same test fires on every subsequent pass, and with no
# way to delete a score row by hand there would be no way out at all.
LOSS_FRACTION = 0.5
LOSS_FLOOR = 10

# The most scores this library has ever had present at once, kept in the
# settings table because it has to outlive the process - the ladder above is
# walked by a sequence of STARTUPS, so a high-water mark held in memory would be
# reset by the very restarts it exists to watch. Not a user setting: settings
# keys are filtered against SETTINGS_DEFAULTS on the way out and rejected
# against it on the way in (see api.get_settings / api.put_settings), so this is
# invisible to and unwritable by a client.
HIGH_WATER_KEY = "library_high_water"

# How many unmatched paths a refusal actually lists. The whole point is to let
# somebody recognise WHICH part of their library it is, and twenty names does
# that as well as three hundred would while keeping the payload and the log line
# a sane size. `unmatched_count` always carries the real total.
UNMATCHED_SAMPLE = 20


def scan_status() -> dict:
    with _state_lock:
        return dict(_state)


def _hash_file(path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _read_high_water(conn) -> int:
    row = conn.execute(
        "SELECT value FROM settings WHERE owner = ? AND key = ?",
        (DEFAULT_OWNER, HIGH_WATER_KEY),
    ).fetchone()
    if row is None:
        return 0
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        # Stored as text like every other setting. A value that is not a number
        # cannot be produced by this code, and treating it as "no mark yet" is
        # the reading that fails open rather than taking startup down over a
        # counter.
        return 0


def _write_high_water(conn, value: int) -> None:
    conn.execute(
        """INSERT INTO settings(owner, key, value) VALUES (?, ?, ?)
           ON CONFLICT(owner, key) DO UPDATE SET value = excluded.value""",
        (DEFAULT_OWNER, HIGH_WATER_KEY, str(value)),
    )


def _acknowledge_token(unmatched_paths) -> str:
    """A name for exactly this set of unmatched paths.

    Consent has to be about something. This is what makes "I meant it" mean "I
    meant THAT" - a token computed from the paths themselves, so an
    acknowledgement cannot be replayed against a different, larger loss that
    happened to arrive between the person reading the message and pressing the
    button. It is stable across restarts by construction, which matters because
    the refusal a person is looking at was very likely produced by a startup
    scan and will be produced again by the next one.
    """
    joined = "\n".join(sorted(unmatched_paths))
    return hashlib.sha1(joined.encode("utf-8", "surrogatepass")).hexdigest()


def _implausible(found: int, believed_present: int, unmatched: int, high_water: int):
    """Is this walk of the library believable as a description of the library?

    Returns the reason it is not, phrased for a person, or None if it is. See the
    block comment on LOSS_FRACTION for the reasoning behind the two tests.

    `unmatched` is stored paths, believed present, that are not on disk now.
    """
    if unmatched == 0:
        # Nothing would be marked, so there is nothing to disbelieve. A guard has
        # to be quiet when it has nothing to say: an error logged on every
        # startup for ever is how somebody learns to stop reading the log.
        #
        # DELIBERATELY REDUNDANT TODAY, and said so rather than left to be
        # rediscovered. The test below refuses any pass that would take the
        # library under half of its high-water mark, and an acknowledgement moves
        # that mark down to the new size - so a database cannot actually reach
        # "well under the mark with nothing to change", and removing this line
        # would change no observable behaviour. It stays because that argument is
        # a chain of three invariants, and this is one line. It is covered at the
        # function boundary in test_scanner.py, where the state IS constructible,
        # precisely because a system-level test of it could not fail.
        return None
    if found == 0 and believed_present > 0:
        return (
            f"the library folder {LIBRARY_DIR} contains no readable score files at all, "
            f"but Fermata has {believed_present} score(s) on record there. That is what an "
            "unmounted drive or a missing bind mount looks like, not what an emptied "
            "library looks like, so NOTHING HAS BEEN CHANGED - not one row was added, "
            "updated or marked. Your practice history, tags and transcriptions are "
            "untouched. Check the folder is mounted and readable, then scan again. If you "
            "really did empty your library on purpose, confirm this message and Fermata "
            "will accept it."
        )
    remaining = believed_present - unmatched
    if high_water >= LOSS_FLOOR and remaining <= high_water * LOSS_FRACTION:
        return (
            f"this scan can account for {remaining} of the {high_water} score(s) this "
            f"library held when it was last complete, and {unmatched} more would be marked "
            f"missing in this pass alone. Half or more of {LIBRARY_DIR} is unaccounted for, "
            "which is more often part of a folder not being readable than a library that "
            "shed most of itself. So NOTHING HAS BEEN CHANGED - not one row was added, "
            "updated or marked. Your practice history, tags and transcriptions are "
            "untouched.\n"
            "\n"
            "Some of those files may simply have MOVED, which Fermata can match up again by "
            "content - but it will not do that, or anything else, until you say so, because "
            "this many paths changing at once is also exactly what a mount problem looks "
            "like. If you reorganised or pruned your library on purpose, confirm this "
            "message: Fermata will then match every file it can back to its own score, mark "
            "the rest as missing rather than deleting anything, and take the smaller library "
            "as the new normal so it stops asking."
        )
    return None


def _refuse(reason: str, unmatched_paths=(), token: str | None = None) -> None:
    """Record that this scan declined to act on what it saw, and say why.

    Written into the state the interface polls AND to the log, because these are
    two different audiences with two different lifetimes: the person watching a
    scan they started, and the person a week later working out what happened
    while nobody was watching.
    """
    paths = sorted(unmatched_paths)
    with _state_lock:
        _state["refused"] = True
        _state["refused_reason"] = reason
        _state["unmatched_paths"] = paths[:UNMATCHED_SAMPLE]
        _state["unmatched_count"] = len(paths)
        _state["acknowledge_token"] = token
    log.error(
        "scan did not reconcile the library and changed nothing: %s%s",
        reason,
        (" First few not found: " + ", ".join(paths[:5])) if paths else "",
    )


def _scan(acknowledge: str | None = None) -> None:
    """One pass. Decide first, write second - in that order, deliberately.

    THE ORDER IS THE FIX. This used to walk the library, insert and update and
    relink a row per file (committing as it went), and only then ask whether the
    walk was believable. So a refusal was never the no-op its own message
    claimed: `refused: true` arrived alongside `added: 1, updated: 1` in the same
    dictionary. The composite case was worse - a library re-exported and
    reorganised in one go gave `refused: true, added: 12` against twelve files,
    twenty-four rows for twelve pieces, permanently doubled.

    Everything the guard needs is knowable before any row is touched: the
    directory listing, and the paths already on record. So it is asked first,
    and when it refuses, this returns having written precisely nothing.
    """
    conn = connect()
    with _state_lock:
        _state.update(
            total=0,
            processed=0,
            added=0,
            updated=0,
            missing=0,
            restored=0,
            unmatched_moves=0,
            refused=False,
            refused_reason=None,
            unmatched_paths=[],
            unmatched_count=0,
            acknowledge_token=None,
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

    # --- Decide. Reads only. ---
    rows = conn.execute("SELECT id, path, missing_since FROM scores").fetchall()
    believed_present = [r for r in rows if r["missing_since"] is None]
    unmatched = [r for r in believed_present if r["path"] not in disk_paths]
    high_water = _read_high_water(conn)

    unmatched_paths = [r["path"] for r in unmatched]
    token = _acknowledge_token(unmatched_paths)
    reason = _implausible(
        len(files), len(believed_present), len(unmatched), high_water
    )
    acknowledged = reason is not None and acknowledge is not None and acknowledge == token
    if reason is not None and not acknowledged:
        _refuse(reason, unmatched_paths, token)
        return
    if acknowledged:
        log.warning(
            "scan proceeding on an acknowledged reconciliation: %s path(s) were not found "
            "at their previous locations and this was confirmed. Files that only moved will "
            "be matched back to their own score by content; the rest will be marked "
            "missing, not deleted.",
            len(unmatched_paths),
        )

    # --- Write. ---
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

    _mark_absent(conn, seen_paths, len(believed_present))
    _record_high_water(conn, high_water, reset=acknowledged)
    conn.commit()


def _mark_absent(conn, seen_paths: set, were_present: int) -> None:
    """Record that these files are not there, which is not the same as deleting them.

    THIS USED TO BE A DELETE, and that delete is #95: three tables cascade from
    scores, so a walk that came back short took practice history, tags and
    hand-corrected transcriptions with it - the only things here that cannot be
    regenerated from the files on disk. The scores always came back on the next
    good scan, because they are files. Nothing else did.

    It is now a mark. A file's absence is a fact about the filesystem, and the
    right record of it is a fact about the filesystem: scores.missing_since. The
    row, and everything hanging off it, stays exactly where it was. That is what
    makes a remount recover by itself, and it is the only version of this that
    survives the two triggers no threshold can catch - a file edited AND moved in
    the same window (the content hash changed, so the relink has no candidate)
    and duplicate content whose copies move together (more than one candidate,
    so the relink rightly declines to guess). In both, the file really is there
    and simply cannot be matched to its row. Marking the old row and inserting
    the new one loses nothing and states the truth: Fermata does not know these
    are the same piece.

    Which rows to mark is decided from `seen_paths` - what the loop actually
    visited - and NOT from the set the guard measured before the loop ran. Those
    two differ by exactly the rows the relink moved to a new path, and marking
    one of those would mark a row whose file this very scan just found.
    """
    unseen = [
        row
        for row in conn.execute(
            "SELECT id, path FROM scores WHERE missing_since IS NULL"
        ).fetchall()
        if row["path"] not in seen_paths
    ]
    for row in unseen:
        # datetime('now') for the same reason every other timestamp in this
        # schema uses it: one clock, UTC, and no dependence on the caller. Only
        # rows not already marked are touched, so an existing mark keeps the
        # date it was made - "missing since March" is a different fact from
        # "missing since this morning", and re-stamping would erase it.
        conn.execute(
            "UPDATE scores SET missing_since = datetime('now') WHERE id = ?", (row["id"],)
        )
    if not unseen:
        return

    with _state_lock:
        _state["missing"] = len(unseen)
        added = _state["added"]
        _state["unmatched_moves"] = min(len(unseen), added)

    # A scan that quietly took away a large part of the library was the other
    # half of #95: the count went into the state dict and nothing ever presented
    # it as the alarming thing it is. Said at warning level, with enough of the
    # paths to recognise WHICH part of the library it was, because "297 scores"
    # and "297 scores, all under Patreon/" are very different messages to wake
    # up to.
    sample = ", ".join(row["path"] for row in unseen[:5])
    log.warning(
        "scan marked %s of %s score(s) missing: their files are no longer where they were "
        "in %s. Nothing has been deleted - practice history, tags and transcriptions are "
        "still attached, and a scan that finds the files again will restore them. First "
        "few: %s%s",
        len(unseen),
        were_present,
        LIBRARY_DIR,
        sample,
        " ..." if len(unseen) > 5 else "",
    )
    if added:
        # Marks and inserts in the same pass means files moved in a way the
        # content-hash relink could not match: a changed mount prefix over
        # duplicated content, or an edit and a move together. Nothing is lost,
        # but the library now holds two rows for one piece and the history is on
        # the one nobody will open. That is strictly better than the delete it
        # replaced, and it must not read as a clean pass.
        log.warning(
            "%s of those went missing in the same pass that added %s new score(s), so some "
            "files have most likely moved in a way Fermata could not match to their "
            "existing scores - the practice history and tags are on the score marked "
            "missing, not on the new one. The Duplicates view is the place to see which.",
            min(len(unseen), added),
            added,
        )


def _record_high_water(conn, previous: int, reset: bool) -> None:
    """Remember how big this library is when it is whole.

    `reset` is what an acknowledgement does: the person has said their library
    really is this size now, so the mark comes DOWN to match. Without that, an
    acknowledged pruning would leave the high-water test firing on every
    subsequent scan for ever, against a library that no longer exists.
    """
    present = conn.execute(
        "SELECT COUNT(*) FROM scores WHERE missing_since IS NULL"
    ).fetchone()[0]
    _write_high_water(conn, present if reset else max(previous, present))


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
        # by id (instead of inserting and leaving the old row behind) keeps the
        # tags, the transcription and the practice attached to the piece a
        # person would say it is still about. Only do this when exactly one such
        # candidate exists - with two or more (genuine duplicate content, copies
        # moving together) there's no way to know which one moved, so fall back
        # to an insert and let _mark_absent record whichever one truly vanished.
        # That fallback is no longer lossy.
        candidates = [
            r
            for r in conn.execute(
                "SELECT id, path, missing_since FROM scores WHERE hash = ?", (file_hash,)
            ).fetchall()
            if r["path"] not in disk_paths
        ]
        if len(candidates) == 1:
            old_id = candidates[0]["id"]
            # A RELINK IS NOT COUNTED AS A RESTORE, and that is a correction
            # rather than an omission. `restored` is presented as evidence that
            # a remount really did recover, and only the by-path case above can
            # support that claim: the same file reappeared where it was. This
            # branch matches on content alone, and a row marked missing stays a
            # relink candidate indefinitely - so ANY later file with the same
            # bytes lands here and inherits that score's practice, tags and
            # transcription. For identical content that is usually the right
            # answer and it is the behaviour Fermata already had, but it is a
            # guess about identity, and a guess must not be reported as proof
            # that a drive came back. It is counted as an update, which is what
            # it is. (Narrowing it further - a time bound, or matching on more
            # than the bytes - is worth doing and is not this change: see the
            # note on the PR.)
            #
            # missing_since is cleared here regardless: a row whose file this
            # scan is looking at is not missing, however it was found.
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


def start_scan(acknowledge: str | None = None) -> bool:
    """Begin a scan in the background. `acknowledge` is a token from a refusal.

    An acknowledgement stands down the guard for exactly the evidence the token
    names and nothing else - see _acknowledge_token. A token that no longer
    matches (the library changed again while somebody was reading the message)
    simply does not apply, and the scan refuses again with the new figures,
    which is the safe way for stale consent to fail.
    """
    with _state_lock:
        if _state["scanning"]:
            return False
        _state["scanning"] = True
        _state["started_at"] = time.time()
        _state["finished_at"] = None

    def run():
        try:
            _scan(acknowledge)
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
