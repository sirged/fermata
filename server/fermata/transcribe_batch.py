import logging
import threading
import time

# Transcribing a whole library one score at a time is exactly the thing
# issue #55 exists to remove - a freshly scanned 300-score library has zero
# transcriptions until someone clicks through all of it. This module owns
# the ONE thing a bulk pass needs beyond what api.transcribe() already does:
# running many scores in the background, with progress a client can poll and
# an honest outcome recorded for every one of them.
#
# THE SAME SHAPE AS scanner.py's SCAN, DELIBERATELY - a module-level state
# dict behind a lock, a synchronous pass most tests call directly, and a
# threaded wrapper an HTTP request actually uses (see scanner._scan /
# scanner.start_scan and test_scanner.py's own comment on why most of that
# suite calls scanner._scan() directly). The issue itself asks for exactly
# this: "run as a background job with progress and a running count, following
# the pattern the library scan already uses rather than adding a queue."
#
# RESUMABLE BY CONSTRUCTION RATHER THAN BY DESIGN EFFORT. There is no
# persisted job state to resume FROM - `_state` lives in memory only, so a
# killed process forgets a pass was ever running the moment it dies, exactly
# like `scanner._state`. What makes that safe rather than lossy is that
# every score's own write is a complete, committed unit (api._store_
# extraction_result's single INSERT ... ON CONFLICT, same as transcribe()'s
# own) - so a kill between two scores leaves the ones already written fully
# transcribed and the rest simply unprocessed, never a half-written row.
# Starting a fresh pass over the same selection afterwards is then just
# ordinary bulk transcription: the already-written scores come back
# "already_transcribed" (see api._batch_process_one) and the rest are
# attempted for the first time. No separate "resume" concept exists because
# none is needed - the whole point of persisting nothing is that starting
# over IS resuming.
#
# WHAT THIS MODULE DOES NOT KNOW, ON PURPOSE. It has no idea what a score is,
# what extract() does, or what "already transcribed" means - `process_one`,
# passed in by the caller, decides all of that per score and hands back a
# plain dict naming the outcome. That is api._batch_process_one today, and
# the dependency runs THIS DIRECTION - api.py imports this module, not the
# other way - because the alternative (this module importing api.py to reach
# _store_extraction_result) would be a circular import: api.py has to import
# this module already, to register the two routes that drive it.
log = logging.getLogger("fermata.transcribe_batch")

# The outcome categories a per-score result may report. Never a silent skip:
# every score handed to a batch pass ends up in exactly one of these, with a
# reason attached to every one but "transcribed" - see api._batch_process_one
# for what earns each one. Named to double as the state dict's own running
# totals below, so recording a result is "increment the key already named by
# its own outcome" rather than a second mapping that could drift from this
# list.
OUTCOMES = ("transcribed", "already_transcribed", "non_extractable", "errored")

_state = {
    "running": False,
    "total": 0,
    "processed": 0,
    "transcribed": 0,
    "already_transcribed": 0,
    "non_extractable": 0,
    "errored": 0,
    # How many of the "transcribed" outcomes came back with at least one bar
    # that does not add up (bars_defective > 0) - the issue's own ask: "how
    # many came out with bars that do not add up, so the state of the
    # library is visible without opening scores one by one." An aggregate
    # count beside `results` rather than something a caller has to derive by
    # scanning every line itself.
    "with_defective_bars": 0,
    # Whether the run under way (or the last one) was asked to replace an
    # existing EXTRACTED transcription rather than skip it - carried here so
    # a client reading only /status still knows what the counts above mean.
    # Never affects an EDITED row - see api._batch_process_one.
    "reconvert": False,
    # One line per score processed so far, in the order they were processed
    # - see api's TranscribeBatchResultLineOut for the shape of each line.
    # Not capped: this is the one place "which scores, and why" lives, and
    # the issue asks for that to be visible without opening scores one by
    # one.
    "results": [],
    "started_at": None,
    "finished_at": None,
}
_state_lock = threading.Lock()


def batch_status() -> dict:
    """A snapshot of the current (or most recent) pass. `results` is copied
    out under the lock so a caller iterating it cannot race a pass still
    appending to the live list."""
    with _state_lock:
        return dict(_state, results=list(_state["results"]))


def _run_batch(process_one, score_ids: list, reconvert: bool) -> None:
    """One synchronous pass over `score_ids`, in order - `process_one(score_id,
    reconvert)` decides each one's outcome. Most tests call this directly,
    the same way test_scanner.py calls scanner._scan() directly, for
    determinism; start_batch (below) is the threaded wrapper an HTTP request
    actually uses.

    Resets the running totals and `results` but NOT `running` itself - the
    caller (start_batch, or a test standing in for it) owns that flag, the
    same split scanner._scan/start_scan makes for `scanning`.

    process_one MUST NOT raise for an ordinary per-score refusal - a missing
    file, a non-extractable pdf, an already-transcribed score - because that
    is precisely the information a silent skip would lose; it returns a dict
    naming the outcome instead (see api._batch_process_one). An exception
    that reaches here regardless is still not allowed to end the pass early:
    it is caught, recorded as `errored` with the exception's own text as the
    reason, and logged, so one score's bug does not cost every score after it
    its result.
    """
    with _state_lock:
        _state.update(
            total=len(score_ids),
            processed=0,
            transcribed=0,
            already_transcribed=0,
            non_extractable=0,
            errored=0,
            with_defective_bars=0,
            reconvert=reconvert,
            results=[],
        )
    for score_id in score_ids:
        try:
            outcome = process_one(score_id, reconvert)
            if outcome.get("outcome") not in OUTCOMES:
                raise ValueError(f"process_one returned an unrecognised outcome: {outcome!r}")
        except Exception as exc:
            outcome = {
                "score_id": score_id,
                "title": None,
                "outcome": "errored",
                "reason": str(exc),
                "bars_defective": None,
                "bars_measured": None,
            }
            log.error("bulk transcription of score %s failed: %s", score_id, exc)
        with _state_lock:
            _state["results"].append(outcome)
            _state["processed"] += 1
            _state[outcome["outcome"]] += 1
            if outcome["outcome"] == "transcribed" and (outcome.get("bars_defective") or 0) > 0:
                _state["with_defective_bars"] += 1


def start_batch(process_one, score_ids: list, reconvert: bool = False) -> bool:
    """Begin a bulk transcription pass in the background over `score_ids`.

    Refuses (returns False, changes nothing) if a pass is already running -
    the same rule scanner.start_scan applies to a second scan, and for the
    same reason: the caller is an HTTP request with a person behind it, and
    "a batch is already running, try again in a moment" is a better answer
    than a second pass racing the first one's writes to the same rows.

    DELIBERATELY NOT HELD AGAINST A RUNNING SCAN, in either direction - a
    scan may start while a batch is running and a batch may start while a
    scan is running. Argued in full where the two actually meet:
    api._batch_process_one reads a score's current row (not a snapshot taken
    when the batch started) before touching its file, so a scan marking a
    score missing or relinking it mid-batch is read as of THAT score's own
    turn rather than raced against. See scanner.hold_library_still's own
    comment for the reasoning this mirrors: transcribing a score only ever
    reads its file and writes to the transcriptions table, never moves or
    removes anything a scan's directory listing depends on - the same
    reasoning that keeps upload() from being held against a scan either.
    """
    with _state_lock:
        if _state["running"]:
            return False
        _state["running"] = True
        _state["started_at"] = time.time()
        _state["finished_at"] = None

    def run():
        try:
            _run_batch(process_one, score_ids, reconvert)
        except Exception as exc:  # pragma: no cover - _run_batch itself does not raise
            log.error("bulk transcription pass crashed: %s", exc)
        finally:
            with _state_lock:
                _state["running"] = False
                _state["finished_at"] = time.time()

    threading.Thread(target=run, name="fermata-transcribe-batch", daemon=True).start()
    return True
