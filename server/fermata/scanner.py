import hashlib
import logging
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from . import transcribe_batch
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
    # What happened to the bulk transcription pass this scan's OWN chain
    # tried to start over the ids it added - see _finish_scan_chain. None at
    # the start of every pass (_scan() resets both keys unconditionally,
    # chained or not - see its own reset block) and set only once, by
    # _finish_scan_chain itself, when the chain's LAST pass finishes and
    # _decide_and_settle_chain has handed it that pass's ids - so a pass
    # still mid-chain, or one that added nothing at all, reads as None
    # rather than carrying over whatever an earlier, unrelated chain left
    # here (#190).
    "transcribe_batch_started": None,
    "transcribe_batch_note": None,
}
_state_lock = threading.Lock()

# Ids `scores` rows this SCAN gained, across every pass of the chain a
# refused-then-acknowledged or upload-stacked rescan builds (see
# _decide_and_settle_chain) - handed to transcribe_batch.start_batch exactly
# once, by _finish_scan_chain, when the chain's last pass finishes rather
# than once per pass (#190: a chained rescan must start ONE bulk pass over
# the union of ids the whole chain added, not one per scan). Lives outside
# `_state` because `_state` is reset at the top of every _scan() call
# (including a chained one) and this has to survive exactly that reset.
#
# Pulled out of this variable ATOMICALLY with `scanning` clearing, inside
# _decide_and_settle_chain's single lock acquisition, never in a separate
# one - see that function's own comment for the race a second lock
# acquisition used to leave open (#190 review, F2).
_chain_added_ids: list[int] = []

# Bumped exactly once per genuinely NEW chain - inside _begin_scan_locked,
# whenever it is NOT a continuation - never for a chain continuing itself.
# What this is for: _finish_scan_chain calls transcribe_batch.start_batch
# OUTSIDE any lock (deliberately - see its own docstring, and the existing
# test built to land a scan in exactly this window), so a plain start_scan()
# can be accepted, run its own _scan() pass, and reset
# transcribe_batch_started/note to None for ITSELF before this chain gets
# back around to writing ITS OWN result. Without a way to notice that,
# _finish_scan_chain's write would land last and clobber the newer chain's
# fresh None with THIS chain's now-stale numbers - measured (#190 review,
# F2-1): a chain B that added nothing had its own status read "started
# transcribing 1", chain A's own count. _decide_and_settle_chain captures
# this alongside `ids`, under the same lock; _finish_scan_chain checks it
# again immediately before writing and simply does not write if a newer
# chain has since been accepted.
#
# STATED PLAINLY, because it is a real trade rather than a clean win: if the
# newer chain adds nothing of its own, ITS _finish_scan_chain also returns
# early (see `if not ids`) and never writes anything either - so the OLDER
# chain's genuinely real, genuinely running pass simply goes unreported.
# `transcribe_batch_started`/`note` stay None, which understates what is
# actually happening (a pass IS running), rather than misattributing it to
# a scan that never asked for it. Losing that one note is the accepted cost
# of never showing a person a number that belongs to the wrong scan.
_scan_generation = 0

# Where a deleted score's file goes, as a folder name directly under the library
# root (#56). Inside the library rather than beside it, deliberately: the library
# folder is very often a mount or an external drive, and a trash kept anywhere
# else would mean every deletion copying bytes across a filesystem boundary -
# slow, and capable of filling a small system disk with somebody's PDFs. Inside,
# the move is a rename within one filesystem: instant, atomic, and impossible to
# half-do.
#
# NAMED WITH A LEADING DOT so a file manager, a sync client and a backup tool
# all treat it the way they treat every other application's private folder. The
# scan skips it entirely - see _library_files - which is what stops a deleted
# score from being re-discovered as a brand new one the moment anything scans.
TRASH_DIR_NAME = ".fermata-trash"

# True while a deliberate change to the library is being applied - a move, a
# rename, a deletion, a restore (#56).
#
# WHY THIS EXISTS AT ALL, and why it is not simply left to the database. A scan
# decides what to write from a DIRECTORY LISTING taken at its start: `files`,
# `disk_paths`, and the set of stored paths it compares them against. A move
# landing after that listing invalidates it in the one way the scan cannot
# detect - the row's path is now somewhere the listing never saw, so
# _mark_absent marks a row whose file is perfectly present, and the person's
# score reads as missing because they moved it with the button provided for
# moving it.
#
# The scanner already refuses to run two scans at once for the same reason, so
# this is that rule extended to the other kind of writer rather than a new idea:
# ONE THING THAT MOVES OR REMOVES AN EXISTING FILE RUNS AT A TIME. It is
# deliberately coarse - a whole-library exclusion for an operation on one file -
# because the alternative is a scan and a mutation agreeing about which paths
# each may touch, which is a great deal of machinery to make a scan and a click
# overlap by a few milliseconds.
#
# WHAT IS NOT HELD, stated exactly, because "one writer at a time" is easy to
# say and would be false as a flat claim:
#
#   UPLOAD IS NOT HELD, on purpose. It only ever CREATES a file at a path
#   nothing claims; it never moves or removes one, so it cannot invalidate a
#   scan's listing the way a move does - a scan either sees the new file and
#   inserts a row, or does not and the upload's own scan picks it up. Holding it
#   would also break the ordinary case rather than protect it: every upload
#   starts a scan, so uploading two files in a row would have the second refused
#   for the first one's scan - which is exactly why a decline queues
#   `_rescan_pending` rather than dropping the request: without it, "the
#   upload's own scan picks it up" was only true when that scan actually ran,
#   and a scan already in flight when the second file landed had taken its
#   listing before that file existed either (#110). The one interaction that
#   matters is an upload landing on a move's destination, and that is caught
#   where it has to be caught anyway (a person can drop a file into the
#   library folder with no endpoint involved at all): _move_file_on_disk
#   refuses a destination that exists, and its rename fails rather than
#   overwriting if the file appears in the gap after that check.
#
#   CREATING A FOLDER IS NOT HELD either, and needs no argument beyond stating
#   it: mkdir moves nothing and removes nothing, and a scan does not have an
#   opinion about an empty directory.
_mutating = False

# Set when start_scan() is declined - because a scan is already running or
# the library is mid-mutation - so the request it was declined for is not
# silently lost (#110). The paragraph above claims "a scan either sees the
# new file and inserts a row, or does not and the upload's own scan picks it
# up" - that was only true when the upload's own scan actually runs. A scan
# already in flight takes its directory listing before the new file exists,
# so it does not see it either; if the upload's own start_scan() call is
# THEN declined because that other scan is still going, nothing was left to
# revisit the file at all. It stayed off the browser and out of every future
# scan until something unrelated happened to ask again - seen as a browser
# test's "the uploaded score never appeared" under CI load, where a scan
# from a different test was still finishing when this one uploaded.
#
# The fix is not to make start_scan() block or queue every request - that
# would turn "declined" into an ever-growing backlog under real load. One
# flag is enough: the running pass (or mutation) is about to look at the
# library fresh regardless, so anything that arrived while it was busy only
# needs ONE more pass after it, not a pass of its own.
_rescan_pending = False


class LibraryBusy(RuntimeError):
    """Something else is reconciling the library right now.

    Raised rather than waited out: the caller is an HTTP request with a person
    behind it, and "a scan is running, try again in a moment" is a better answer
    than a request that hangs for the length of a scan over a library of
    thousands of files.
    """


@contextmanager
def hold_library_still():
    """Hold the library still for one deliberate change.

    Refuses if a scan is running or another change is being applied, and makes
    start_scan decline for as long as it is held. See the comment on `_mutating`
    for why a change to one file excludes a scan of the whole library.
    """
    global _mutating
    with _state_lock:
        if _state["scanning"]:
            raise LibraryBusy(
                "a library scan is running, so Fermata will not move or delete anything "
                "just now - a scan decides what to write from a listing taken when it "
                "started, and a file moving underneath it would read as a file that went "
                "missing. Wait for the scan to finish and try again."
            )
        if _mutating:
            raise LibraryBusy(
                "another change to the library is being applied right now. Wait for it to "
                "finish and try again."
            )
        _mutating = True
    try:
        yield
    finally:
        with _state_lock:
            _mutating = False
        # If a scan was declined while this held the library (an upload
        # landing mid-move, say), it does not get to fall through the gap
        # the mutation just closed - see `_rescan_pending`.
        _run_pending_rescan()


def trash_dir(root: Path | None = None) -> Path:
    """The trash folder for a library root.

    Takes the root as an argument rather than reading LIBRARY_DIR at import
    time, because api.py and the tests each hold their own binding of it - see
    test_scanner.py's `library` fixture, which repoints this module's.
    """
    return (root if root is not None else LIBRARY_DIR) / TRASH_DIR_NAME


def in_trash(rel: str) -> bool:
    """Is this library-relative path inside the trash folder?"""
    parts = Path(rel.replace("\\", "/")).parts
    return bool(parts) and parts[0] == TRASH_DIR_NAME


def _library_files(root: Path) -> list[Path]:
    """Every file in the library a scan is entitled to have an opinion about.

    The trash is excluded here rather than filtered out later, so that it is
    excluded from `disk_paths` too - a deleted file counting as "on disk" would
    make the relink treat it as a live candidate and quietly resurrect the score
    somebody deleted.
    """
    return [
        p
        for p in root.rglob("*")
        if p.is_file()
        and p.suffix.lower() in FILE_TYPES
        and not in_trash(p.relative_to(root).as_posix())
    ]


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


def hash_file(path) -> str:
    """A file's content hash - the only thing in Fermata that says two files
    are the same music.

    Public because the move and restore paths in api.py check a file against
    its score's stored hash with it (#56). That is deliberately the SAME
    identity test _scan_file's relink uses, rather than a second one: there is
    one answer to "is this still that score's file", and it is the bytes.
    """
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
            transcribe_batch_started=None,
            transcribe_batch_note=None,
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

    files = _library_files(LIBRARY_DIR)
    # Fixed for the whole scan: lets _scan_file tell a genuinely-removed row
    # (safe to re-link on a hash match) from one whose file just hasn't been
    # visited yet in this pass.
    disk_paths = {p.relative_to(LIBRARY_DIR).as_posix() for p in files}
    with _state_lock:
        _state["total"] = len(files)

    # --- Decide. Reads only. ---
    #
    # A DELETED SCORE IS NOT PART OF THIS CONVERSATION, and every query below
    # that reaches the scores table says so. Its file is in the trash, which the
    # walk above skipped, so counting it among the rows "believed present" would
    # make every deletion look to the guard like a file that vanished - delete
    # half a large library on purpose and the next scan would refuse, offering a
    # token to acknowledge something nobody did. Deleting is not something the
    # scan has to be talked into; it already happened, deliberately, and was
    # recorded (#56).
    rows = conn.execute(
        "SELECT id, path, missing_since FROM scores WHERE deleted_at IS NULL"
    ).fetchall()
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
            "SELECT id, path FROM scores WHERE missing_since IS NULL AND deleted_at IS NULL"
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


def record_deliberate_shrink(conn) -> int:
    """Take the library's current size as its new high-water mark, because
    somebody just made it smaller on purpose (#56).

    THIS IS THE SAME THING AN ACKNOWLEDGEMENT DOES, and for the same reason.
    The high-water mark is what the proportional refusal is measured against
    (see LOSS_FRACTION), and it only ever rises by itself - so without this, a
    person who deletes two thirds of their library through Fermata is left with
    a mark describing a library that no longer exists, and the very next file
    that genuinely goes missing takes the remaining count under half of it and
    refuses a scan over one file. The refusal would be about a loss they
    already told Fermata about, one score at a time, and confirmed by pressing
    delete.

    Returns the new mark, so a caller can say what it did rather than only do
    it.
    """
    present = conn.execute(
        "SELECT COUNT(*) FROM scores WHERE missing_since IS NULL AND deleted_at IS NULL"
    ).fetchone()[0]
    _write_high_water(conn, present)
    return present


def _record_high_water(conn, previous: int, reset: bool) -> None:
    """Remember how big this library is when it is whole.

    `reset` is what an acknowledgement does: the person has said their library
    really is this size now, so the mark comes DOWN to match. Without that, an
    acknowledged pruning would leave the high-water test firing on every
    subsequent scan for ever, against a library that no longer exists.
    """
    present = conn.execute(
        "SELECT COUNT(*) FROM scores WHERE missing_since IS NULL AND deleted_at IS NULL"
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
    file_hash = hash_file(path)
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
        #
        # A DELETED ROW IS NEVER A CANDIDATE (#56). Its file is in the trash by
        # somebody's decision, and its stored path points there - so without
        # this filter, dropping a fresh copy of that content anywhere in the
        # library would re-link the deleted score onto it, move its path back
        # out of the trash, and hand the person back the thing they threw away,
        # while leaving the actual trashed file behind with nothing naming it.
        # Restoring is an action with a button; it is not something a scan
        # should do on somebody's behalf.
        candidates = [
            r
            for r in conn.execute(
                "SELECT id, path, missing_since FROM scores"
                " WHERE hash = ? AND deleted_at IS NULL",
                (file_hash,),
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
            cur = conn.execute(
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
                # Only a genuinely NEW row - never the relink branch above,
                # which is counted as an update because it is one (see its
                # own comment) - is a score a bulk transcription pass has
                # never seen before (#190). `cur.lastrowid` is sqlite's id
                # for the row this exact INSERT just created.
                _chain_added_ids.append(cur.lastrowid)
    conn.commit()


def start_scan(acknowledge: str | None = None) -> bool:
    """Begin a scan in the background. `acknowledge` is a token from a refusal.

    An acknowledgement stands down the guard for exactly the evidence the token
    names and nothing else - see _acknowledge_token. A token that no longer
    matches (the library changed again while somebody was reading the message)
    simply does not apply, and the scan refuses again with the new figures,
    which is the safe way for stale consent to fail.

    Always a genuinely NEW chain - this function is never how a scan's own
    chain continues itself (#190 review, second pass): a continuation goes
    through `_begin_scan_locked` directly, from inside
    `_decide_and_settle_chain`'s own lock, which is the only place with a
    `_continuing_chain=True` case to hand it. Every caller here (POST /scan,
    POST /upload, POST /scan/acknowledge, the startup scan, and
    `_run_pending_rescan` on hold_library_still's behalf) must not carry
    forward whatever a previous, unrelated chain left in `_chain_added_ids`
    (#190) - a test that calls `_scan()` directly, bypassing this function
    entirely (the pattern this module's own tests use for determinism),
    never goes through this reset either, which is correct: those ids were
    never claimed by a chain this function tracks, so a later, real
    start_scan() must not inherit them. (An earlier version of this took a
    `_continuing_chain` parameter kept reachable "for tests"; nothing ever
    called it that way, so it was dead weight - removed rather than kept as
    an unused door back into a state this function no longer produces.)
    """
    global _rescan_pending
    with _state_lock:
        if _state["scanning"] or _mutating:
            # `_mutating` for the same reason `scanning` is here: one thing
            # reconciles the library at a time. A scan starting in the middle of
            # a move would take its directory listing while the file is at
            # neither end of the move - see the comment on `_mutating`.
            #
            # Recorded rather than just refused - see `_rescan_pending` - so
            # whatever asked for this scan is not left with no scan ever
            # having looked for it.
            _rescan_pending = True
            return False
        _begin_scan_locked(_continuing_chain=False)
    _spawn_scan_thread(acknowledge)
    return True


def _begin_scan_locked(_continuing_chain: bool) -> None:
    """The part of starting a scan that must happen while `_state_lock` is
    already held (#190 review, F2 second pass): flip `scanning` on and,
    unless this is a chain's own continuation, reset `_chain_added_ids` for
    the fresh chain that's beginning and bump `_scan_generation` (#190
    review, F2-1) - the moment a genuinely NEW chain is accepted, which
    `_finish_scan_chain` uses afterwards to notice a still-in-flight OLDER
    chain's write has gone stale. Called from start_scan() (which holds the
    lock itself) and from _decide_and_settle_chain's continuing branch
    (which holds it for exactly this reason - see that function's comment).
    """
    global _chain_added_ids, _scan_generation
    _state["scanning"] = True
    _state["started_at"] = time.time()
    _state["finished_at"] = None
    if not _continuing_chain:
        _chain_added_ids = []
        _scan_generation += 1


def _spawn_scan_thread(acknowledge: str | None) -> None:
    """The background thread every scan pass runs in, whether started by
    start_scan() or by a chain continuing itself directly. Factored out so
    _decide_and_settle_chain can spawn a continuation's thread without
    going back through start_scan()'s own lock acquisition (#190 review,
    F2 second pass)."""

    def run():
        try:
            _scan(acknowledge)
        except Exception as exc:
            with _state_lock:
                _state["errors"] += 1
                _state["last_error"] = str(exc)
        _decide_and_settle_chain()

    threading.Thread(target=run, name="fermata-scan", daemon=True).start()


def _decide_and_settle_chain() -> None:
    """Decide the chain's fate - continue with another pass, or end and hand
    off to _finish_scan_chain - and, if it continues, hand `scanning` and
    `_chain_added_ids` straight to that continuation, all in ONE lock
    acquisition (#190 review, two rounds).

    THE GAP THIS CLOSES, FIRST ROUND. `scanning` used to clear (and the lock
    release) BEFORE anything asked whether the chain continues or ends. An
    ordinary POST /api/scan or /api/upload landing in that gap reset
    `_chain_added_ids` to start ITS OWN fresh chain, taking the ids THIS
    chain was about to hand to transcribe_batch with it (see the ENDING
    branch below, which closes this by deciding under the lock).

    THE GAP THIS CLOSES, SECOND ROUND - and this is the one the first fix
    left open. The ENDING branch decided everything it needed under the
    lock, but the CONTINUING branch did not: it left `ids = None`,
    `_chain_added_ids` untouched, and called `start_scan(_continuing_chain=
    True)` - a SEPARATE function, with its OWN SEPARATE lock acquisition -
    only AFTER this function's own lock had already released `scanning`
    back to False. Anything landing in THAT gap (an ordinary start_scan(),
    or a move/delete taking `_mutating` via hold_library_still and later
    running _run_pending_rescan's plain start_scan() from its finally) saw
    a genuinely idle scanner, was entitled to proceed, and reset
    `_chain_added_ids` before the real continuation ever got there -
    measured: a chain that added {1, 2} handed transcribe_batch only [2].
    Worse than the first gap's silence: a pass still started, over a
    subset, and transcribe_batch_started/note reported that smaller count
    as though it were the whole chain's.

    Closed the same way as the first gap, extended to cover the
    continuation too: when this chain continues, `_begin_scan_locked` runs
    right here, inside this SAME lock - `scanning` never becomes visible as
    False, and `_chain_added_ids` is never reset, between one pass ending
    and the next one of the SAME chain beginning. A start_scan() or a
    mutation arriving after this function returns is starting or applying
    against a genuinely settled scanner, never stealing the tail of a
    chain still mid-flight.
    """
    global _rescan_pending, _chain_added_ids
    with _state_lock:
        _state["finished_at"] = time.time()
        if _rescan_pending:
            _rescan_pending = False
            continuing = True
            ids = None
            generation = None
            # The continuation itself, right here, under the same lock that
            # just decided to continue - see the docstring above for why a
            # separate start_scan() call, even after this lock released,
            # was still the gap.
            _begin_scan_locked(_continuing_chain=True)
        else:
            continuing = False
            ids = _chain_added_ids
            # Captured under this SAME lock, alongside `ids` - the
            # generation THIS chain belongs to, for _finish_scan_chain to
            # check again once it is about to write (#190 review, F2-1).
            # See `_scan_generation`'s own comment for the write race this
            # closes: transcribe_batch.start_batch runs outside any lock,
            # deliberately, so a newer chain can be accepted and reset
            # transcribe_batch_started/note for itself before this chain's
            # own write lands - checking the generation again right before
            # that write is what lets it notice and skip, rather than
            # clobbering the newer chain's fresh None with its own now-
            # stale numbers.
            generation = _scan_generation
            _chain_added_ids = []
            _state["scanning"] = False
    # A no-op in production - see its own docstring. The one moment a test
    # can inject something deterministically to prove nothing can land in
    # a gap that, by design, no longer exists for anything else to use.
    _after_chain_decided(continuing)
    if continuing:
        # No acknowledge token carried forward - see start_scan's own
        # docstring for why a continuation never replays one.
        _spawn_scan_thread(acknowledge=None)
    else:
        _finish_scan_chain(ids, generation)


def _after_chain_decided(continuing: bool) -> None:
    """A no-op in production, always. The fix above (#190 review, F2
    second pass) closes the continuing branch's gap by making `scanning`
    and `_chain_added_ids` never externally observable in an inconsistent
    state between one pass ending and the next one of the same chain
    beginning - which is exactly why there is no other seam left for a
    test to inject at and prove that. This one exists purely so a test can
    try landing something HERE, right after `_decide_and_settle_chain`'s
    own lock has genuinely released, and show it lands on a scanner that
    is already fully settled (an ended chain) or already fully reclaimed
    (a continuing one) either way - never on one still mid-transition."""


def _run_pending_rescan() -> bool:
    """Start exactly one follow-up scan if something was declined while a
    MUTATION held the library - see `_rescan_pending` and
    hold_library_still's own finally, the one remaining caller. A scan's
    own chain no longer uses this (see _decide_and_settle_chain) - and,
    since that function now hands a continuing chain its `scanning` state
    and `_chain_added_ids` directly, under one lock, `_mutating` and
    `_state["scanning"]` genuinely are never both true here: a mutation
    cannot begin while a chain is mid-flight (hold_library_still's own
    guard refuses it, and `scanning` stays visibly True for the chain's
    entire lifetime now, continuations included), so there is no
    accumulated-ids state for this call to disturb - mutations never touch
    `_chain_added_ids` in the first place.

    Plain start_scan(), never the acknowledge token a refused scan might have
    been holding: that token is consent to one specific, named set of missing
    files, and replaying it against whatever the library looks like now would
    apply a person's "yes" to evidence they never saw.
    """
    global _rescan_pending
    with _state_lock:
        if not _rescan_pending:
            return False
        _rescan_pending = False
    start_scan()
    return True


# Set by api.py at import time, right after it defines _batch_process_one -
# see register_transcribe_hook just below. scanner.py cannot import api.py
# directly to reach it: api.py already imports scanner.py (for start_scan,
# scan_status and LIBRARY_DIR), so the other direction would be a circular
# import - the same reasoning transcribe_batch.py's own module comment gives
# for why ITS dependency on api.py runs one direction only, carried one layer
# further down. Stays None in a test that imports scanner.py (and
# transcribe_batch.py) without ever importing api.py - a scan then has
# nothing registered to call, so it simply starts no bulk pass, which is a
# fine thing for such a test to mean.
_transcribe_process_one = None


def register_transcribe_hook(process_one) -> None:
    """Say how to transcribe one score, so a scan's own chain can start a
    bulk pass over what it added without scanner.py importing api.py to find
    out how (#190). `process_one` has the same shape transcribe_batch.
    start_batch already requires: `process_one(score_id, reconvert) -> dict`
    naming the outcome. Called exactly once, by api.py, right after
    `_batch_process_one` is defined.
    """
    global _transcribe_process_one
    _transcribe_process_one = process_one


def _finish_scan_chain(ids: list[int], generation: int) -> None:
    """Start ONE bulk transcription pass over every id any pass of this
    scan's chain added, now that the chain's last pass has finished - never
    once per pass (#190). A freshly scanned library should not sit with zero
    transcriptions until somebody clicks a bulk pass by hand, and a chain
    that stitched several rescans together (an upload landing mid-scan, a
    refusal that got acknowledged) must still only ever start one pass over
    the union of everything it added, not one pass per link in the chain.

    `ids` is handed in rather than read from `_chain_added_ids` here, and
    that is deliberate (#190 review, F2): pulling it out has to happen
    atomically with clearing `scanning`, or a scan landing in the gap
    between the two can reset the list before this function ever sees it -
    see _decide_and_settle_chain, the only caller, for the full argument.
    `generation` is the same idea applied to transcribe_batch_started/note
    (#190 review, F2-1) - see `_scan_generation`'s own comment.

    Does nothing if the chain added nothing - nothing new to transcribe - or
    if nobody has registered a hook (see register_transcribe_hook: a test
    that imports scanner.py without api.py has nothing to call this with).

    transcribe_batch.start_batch already refuses re-entrantly, returning
    False and changing nothing, if a pass is already running by somebody's
    own hand - that refusal is exactly right here too (see the No-gos on
    #190: no queue), so it is not retried or queued, only recorded into this
    scan's own status in words. A scan runs unattended on every boot, which
    is the one place nobody is watching transcribe_batch's own status for it.

    DELIBERATELY NOT HELD UNDER `_state_lock` for the `start_batch` call
    itself - the same "one thing settles the chain, everything else may
    proceed" reasoning `_decide_and_settle_chain` already applies to
    `scanning`. That is exactly what leaves a window between capturing
    `ids`/`generation` and this function's own write: a plain start_scan()
    can be accepted, and its own `_scan()` pass can reset
    transcribe_batch_started/note to None for ITSELF, before this call gets
    back around to writing. The write below checks `_scan_generation` again,
    immediately before writing, specifically to notice that and skip rather
    than clobber - see `_before_finish_writes_status`, the seam a test uses
    to land exactly there.
    """
    if not ids or _transcribe_process_one is None:
        return
    started = transcribe_batch.start_batch(_transcribe_process_one, ids)
    # A no-op in production - see the docstring above. The one moment a test
    # can inject a newer chain being accepted, to prove this chain's own
    # write below does not clobber it.
    _before_finish_writes_status(ids)
    with _state_lock:
        if _scan_generation != generation:
            # A newer chain has already been accepted since this chain's
            # ids were captured - it has its own fresh None waiting for its
            # own result, and writing this chain's numbers over it would
            # misattribute them to a scan that has not decided anything yet
            # (#190 review, F2-1). If that newer chain goes on to add
            # something of its own, IT reports its own result accurately
            # when it finishes - but if it adds nothing, its own
            # _finish_scan_chain also returns early (see `if not ids`
            # above) and writes nothing either, so THIS chain's genuinely
            # running pass simply goes unreported: transcribe_batch_started/
            # note stay None rather than say anything at all. See
            # `_scan_generation`'s own comment for why that understatement
            # is the accepted trade against the misattribution.
            return
        _state["transcribe_batch_started"] = started
        _state["transcribe_batch_note"] = (
            f"started transcribing {len(ids)} newly scanned score(s) in the background"
            if started
            else "did not start transcribing the newly scanned scores because a bulk "
            "transcription pass was already running - start one by hand from the "
            "library view to pick them up"
        )


def _before_finish_writes_status(ids: list[int]) -> None:
    """A no-op in production, always. The one seam between
    transcribe_batch.start_batch returning and _finish_scan_chain writing
    transcribe_batch_started/note that a test can inject a newer chain being
    accepted at, deterministically, to prove the generation check just below
    it actually stops the stale write (#190 review, F2-1)."""
