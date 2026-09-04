"""Bulk transcription (issue #55): many scores transcribed in one background
pass, with an honest per-score outcome for every one of them.

WHAT THIS FILE PROVES, beyond what test_transcription_api.py already proves
about a single POST /transcribe:

  1. a mixed batch - transcribable, already transcribed, hand-edited,
     non-extractable, file missing - reports the CORRECT outcome for EVERY
     one of them, by literal assertion, never a silent skip;
  2. an edited transcription is never clobbered by a bulk pass, `reconvert`
     or not - checked literally before and after, through the API, the same
     #146 lesson test_transcription_api.py's structural guard exists for;
  3. `reconvert` replaces an EXTRACTED row and leaves an edited one alone;
  4. an explicit score_ids selection is honoured exactly - a non-pdf or
     deleted id still gets its own outcome rather than vanishing from the
     results;
  5. the scan-interaction matrix: a scan running while a bulk pass is under
     way, and a bulk pass running while a scan is under way, neither one
     refusing the other and neither corrupting the other's work - see
     transcribe_batch.start_batch's own docstring for why the two are not
     held against one another, mirroring scanner.hold_library_still's
     reasoning for why upload() is not held against a scan either;
  6. a second batch refuses while one is already running, the same rule
     scanner.start_scan applies to a second scan;
  7. issue #190's own scan-triggered hook: the LAST scan of a chain starts
     one bulk pass over exactly the ids that chain added (never one per
     pass), that hook is refused rather than interrupting a pass already
     running by hand - recorded into the scan's own status in words - and an
     upload (which triggers a scan) is covered by the same hook, proven
     directly rather than only inferred.
"""
import threading
import time

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from fermata import api, db, scanner, thumbs, transcribe_batch


@pytest.fixture(autouse=True)
def _reset_batch_state():
    """transcribe_batch._state is module-level, like scanner._state - reset
    around every test in this file so one test's run cannot leak into the
    next one's assertions."""
    transcribe_batch._state.update(
        running=False, total=0, processed=0, transcribed=0, already_transcribed=0,
        non_extractable=0, errored=0, with_defective_bars=0, reconvert=False,
        results=[], started_at=None, finished_at=None,
    )
    yield
    transcribe_batch._state.update(running=False)


@pytest.fixture
def library(app_env, tmp_path, monkeypatch):
    """A throwaway library the scanner will actually walk, for the tests
    below that need a REAL scan to run - same fixture as test_scanner.py's,
    duplicated rather than imported across test modules (see that file's
    own comment on why scanner and api hold their own module-level bindings
    of LIBRARY_DIR)."""
    root = tmp_path / "library"
    monkeypatch.setattr(scanner, "LIBRARY_DIR", root)
    monkeypatch.setattr(api, "LIBRARY_DIR", root)
    monkeypatch.setattr(thumbs, "CACHE_DIR", tmp_path / "config" / "cache")
    return root


@pytest.fixture
def client(library):
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def _wait_for_batch(timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while transcribe_batch.batch_status()["running"]:
        if time.monotonic() > deadline:
            raise AssertionError("the bulk transcription pass did not finish")
        time.sleep(0.02)


def _wait_for_scan(timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while scanner.scan_status()["scanning"]:
        if time.monotonic() > deadline:
            raise AssertionError("the scan did not finish")
        time.sleep(0.02)


def _wait_for_scan_chain_decision(timeout: float = 10.0) -> dict:
    """Wait past the exact race zz-library-missing.spec.js's own scanAndWait
    warns about, one layer further down (#190): `scanning` going false only
    means the SCAN is done, not that its own chain-completion hook
    (scanner._finish_scan_chain) has decided anything about a bulk pass yet.
    `transcribe_batch_started` moving away from `None` IS that hook having
    decided - only once it has is a read of transcribe_batch.batch_status()
    actually about the pass THIS chain may have started, rather than one
    left over from before."""
    deadline = time.monotonic() + timeout
    while True:
        status = scanner.scan_status()
        if not status["scanning"] and status["transcribe_batch_started"] is not None:
            return status
        if time.monotonic() > deadline:
            raise AssertionError(
                f"the scan's chain never finished deciding about a bulk pass: {status}"
            )
        time.sleep(0.02)


def _outcomes_by_title(status: dict) -> dict:
    return {line["title"]: line for line in status["results"]}


# ---------------------------------------------------------------------------
# 1. Honest per-score outcomes over a mixed batch.
# ---------------------------------------------------------------------------


def test_a_mixed_batch_reports_the_correct_outcome_for_every_score(
    app_env, extractable_pdf, non_extractable_pdf, monkeypatch, insert_score, tmp_path
):
    # A score's `path` is UNIQUE, so exercising several scores that share one
    # fixture's content needs several distinct files on disk - copied into
    # their own scratch library rather than pointed at the read-only fixtures
    # directory, which the "missing" case below must also be free to NOT
    # create a file in.
    lib = tmp_path / "mixed_library"
    lib.mkdir()
    monkeypatch.setattr(api, "LIBRARY_DIR", lib)
    conn = db.connect()

    def _copy(src, name):
        dest = lib / name
        dest.write_bytes(src.read_bytes())
        return name

    fresh_id = insert_score(conn, _copy(extractable_pdf, "fresh.pdf"), "Fresh")
    already_id = insert_score(conn, _copy(extractable_pdf, "already.pdf"), "Already transcribed")
    api.transcribe(already_id, body=None)  # give it an extracted row up front
    edited_id = insert_score(conn, _copy(extractable_pdf, "edited.pdf"), "Hand edited")
    api.transcribe(edited_id, body=None)
    api.save_transcription(
        edited_id, api.TranscriptionEditIn(content='\\title "hand edited"\n.\n:4 0.1 |')
    )
    bad_id = insert_score(conn, _copy(non_extractable_pdf, "bad.pdf"), "Not extractable")
    missing_id = insert_score(conn, "nowhere.pdf", "File missing")

    transcribe_batch._run_batch(
        api._batch_process_one, [fresh_id, already_id, edited_id, bad_id, missing_id], False
    )
    status = transcribe_batch.batch_status()

    assert status["total"] == 5
    assert status["processed"] == 5
    assert status["transcribed"] == 1
    assert status["already_transcribed"] == 2
    assert status["non_extractable"] == 1
    assert status["errored"] == 1

    by_title = _outcomes_by_title(status)
    assert by_title["Fresh"]["outcome"] == "transcribed"
    assert by_title["Fresh"]["reason"] is None
    assert by_title["Fresh"]["bars_measured"] > 0

    assert by_title["Already transcribed"]["outcome"] == "already_transcribed"
    assert "reconvert" in by_title["Already transcribed"]["reason"]

    assert by_title["Hand edited"]["outcome"] == "already_transcribed"
    assert "never overwrites" in by_title["Hand edited"]["reason"]

    assert by_title["Not extractable"]["outcome"] == "non_extractable"
    assert by_title["Not extractable"]["reason"]  # the extractor's own reason, non-empty

    assert by_title["File missing"]["outcome"] == "errored"
    assert by_title["File missing"]["reason"] == "file missing from library"

    # Nobody was skipped without a place in the results - the whole point.
    assert {line["score_id"] for line in status["results"]} == {
        fresh_id, already_id, edited_id, bad_id, missing_id
    }

    # The score actually transcribed really did get a stored row; the ones
    # that were reported as skipped or failed did not gain one.
    assert api.get_transcription(fresh_id)["source"] == "extracted"
    with pytest.raises(HTTPException) as exc_info:
        api.get_transcription(missing_id)
    assert exc_info.value.status_code == 404


def test_an_unrecognised_score_id_gets_its_own_errored_outcome(app_env):
    transcribe_batch._run_batch(api._batch_process_one, [999999], False)
    status = transcribe_batch.batch_status()
    assert status["results"] == [
        {
            "score_id": 999999, "title": None, "outcome": "errored",
            "reason": "score not found", "bars_defective": None, "bars_measured": None,
        }
    ]


def test_a_killed_job_leaves_finished_work_intact_and_a_fresh_pass_resumes(
    app_env, extractable_pdf, monkeypatch, insert_score, tmp_path
):
    """No job state is persisted anywhere (see transcribe_batch.py's own
    comment on why) - so "resuming" a killed pass is just running a fresh
    one over the same selection. This proves that is actually safe: the
    scores a first, interrupted-in-spirit pass already wrote stay written,
    exactly as they were, and a second pass over the same ids only touches
    the ones still outstanding."""
    lib = tmp_path / "resume_library"
    lib.mkdir()
    monkeypatch.setattr(api, "LIBRARY_DIR", lib)
    conn = db.connect()

    def _copy(name):
        dest = lib / name
        dest.write_bytes(extractable_pdf.read_bytes())
        return name

    ids = [insert_score(conn, _copy(f"score{i}.pdf"), f"Score {i}") for i in range(4)]

    # The "kill" - a pass that only ever reaches the first two ids, standing
    # in for a process that died after committing those two scores' rows and
    # before touching the rest. Each committed row is a complete unit (see
    # api._store_extraction_result), so nothing about score0/score1 is
    # half-written by this.
    transcribe_batch._run_batch(api._batch_process_one, ids[:2], False)
    first_pass = transcribe_batch.batch_status()
    assert first_pass["transcribed"] == 2
    first_updated_at = {
        score_id: api.get_transcription(score_id)["updated_at"] for score_id in ids[:2]
    }

    # The "restart" - a fresh pass over the FULL original selection, the same
    # request a client would naturally retry with. The two already-written
    # scores are recognised as already done and left untouched; only the
    # previously-unreached two are actually transcribed now.
    transcribe_batch._run_batch(api._batch_process_one, ids, False)
    resumed = transcribe_batch.batch_status()
    assert resumed["total"] == 4
    by_id = {line["score_id"]: line for line in resumed["results"]}
    assert by_id[ids[0]]["outcome"] == "already_transcribed"
    assert by_id[ids[1]]["outcome"] == "already_transcribed"
    assert by_id[ids[2]]["outcome"] == "transcribed"
    assert by_id[ids[3]]["outcome"] == "transcribed"

    # The rows the "killed" pass wrote were not touched a second time - same
    # updated_at, not re-written and not corrupted.
    for score_id in ids[:2]:
        assert api.get_transcription(score_id)["updated_at"] == first_updated_at[score_id]
    for score_id in ids[2:]:
        assert api.get_transcription(score_id)["source"] == "extracted"


# ---------------------------------------------------------------------------
# 2 & 3. Edited transcriptions are never clobbered; reconvert only replaces
#         the extracted row.
# ---------------------------------------------------------------------------


def test_edited_transcription_survives_a_bulk_run_through_the_api(
    app_env, extractable_pdf, monkeypatch, insert_score
):
    """The #146 lesson, applied to the bulk path: literal before/after
    through the API, not just a state assertion inside the process."""
    monkeypatch.setattr(api, "LIBRARY_DIR", extractable_pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, extractable_pdf.name)

    api.transcribe(score_id, body=None)
    edited_content = '\\title "hand edited"\n.\n:4 0.1 |'
    api.save_transcription(score_id, api.TranscriptionEditIn(content=edited_content))

    before = api.get_transcription(score_id)
    assert before["source"] == "edited"
    assert before["content"] == edited_content

    # Even with reconvert=True - the flag that DOES replace an extracted row
    # below - an edited one is untouched.
    transcribe_batch._run_batch(api._batch_process_one, [score_id], True)
    status = transcribe_batch.batch_status()
    assert status["results"][0]["outcome"] == "already_transcribed"
    assert "never overwrites" in status["results"][0]["reason"]

    after = api.get_transcription(score_id)
    assert after["source"] == "edited"
    assert after["content"] == edited_content

    rows = conn.execute(
        "SELECT source, content FROM transcriptions WHERE score_id = ? ORDER BY source",
        (score_id,),
    ).fetchall()
    assert {r["source"] for r in rows} == {"edited", "extracted"}
    edited_row = next(r for r in rows if r["source"] == "edited")
    assert edited_row["content"] == edited_content


def test_reconvert_replaces_the_extracted_row_but_not_edited(
    app_env, extractable_pdf, monkeypatch, insert_score
):
    monkeypatch.setattr(api, "LIBRARY_DIR", extractable_pdf.parent)
    conn = db.connect()
    plain_id = insert_score(conn, extractable_pdf.name, "Plain")
    api.transcribe(plain_id, body=None)
    first_updated_at = api.get_transcription(plain_id)["updated_at"]

    # Without reconvert, a second pass leaves it alone and says why.
    transcribe_batch._run_batch(api._batch_process_one, [plain_id], False)
    assert transcribe_batch.batch_status()["results"][0]["outcome"] == "already_transcribed"
    assert api.get_transcription(plain_id)["updated_at"] == first_updated_at

    # With reconvert, the extracted row is re-written.
    transcribe_batch._run_batch(api._batch_process_one, [plain_id], True)
    status = transcribe_batch.batch_status()
    assert status["results"][0]["outcome"] == "transcribed"
    assert status["reconvert"] is True
    assert api.get_transcription(plain_id)["source"] == "extracted"


# ---------------------------------------------------------------------------
# 4. Explicit score_ids are honoured exactly - never silently filtered.
# ---------------------------------------------------------------------------


def test_explicit_score_ids_report_a_non_pdf_and_a_deleted_score_honestly(
    app_env, extractable_pdf, monkeypatch, insert_score
):
    monkeypatch.setattr(api, "LIBRARY_DIR", extractable_pdf.parent)
    conn = db.connect()
    cur = conn.execute(
        """INSERT INTO scores(title, path, file_type, hash, size, mtime)
           VALUES ('Sheet music XML', 'x.musicxml', 'musicxml', 'h', 1, 0.0)"""
    )
    conn.commit()
    musicxml_id = cur.lastrowid

    deleted_id = insert_score(conn, extractable_pdf.name, "Deleted score")
    conn.execute("UPDATE scores SET deleted_at = datetime('now') WHERE id = ?", (deleted_id,))
    conn.commit()

    body = api.TranscribeBatchIn(score_ids=[musicxml_id, deleted_id])
    result = api.start_transcribe_batch(body)
    assert result["started"] is True
    _wait_for_batch()
    status = transcribe_batch.batch_status()

    by_id = {line["score_id"]: line for line in status["results"]}
    assert by_id[musicxml_id]["outcome"] == "errored"
    assert "pdf" in by_id[musicxml_id]["reason"]
    assert by_id[deleted_id]["outcome"] == "errored"
    assert "trash" in by_id[deleted_id]["reason"]


def test_score_ids_and_collection_together_is_rejected(app_env):
    body = api.TranscribeBatchIn(score_ids=[1], collection="Bach")
    with pytest.raises(HTTPException) as exc_info:
        api.start_transcribe_batch(body)
    assert exc_info.value.status_code == 422


def test_collection_selects_only_that_folders_eligible_scores(
    app_env, extractable_pdf, monkeypatch, insert_score, tmp_path
):
    lib = tmp_path / "collection_library"
    lib.mkdir()
    monkeypatch.setattr(api, "LIBRARY_DIR", lib)
    conn = db.connect()

    def _copy(name):
        dest = lib / name
        dest.write_bytes(extractable_pdf.read_bytes())
        return name

    conn.execute(
        """UPDATE scores SET collection = 'Bach' WHERE id = ?""",
        (insert_score(conn, _copy("in_bach.pdf"), "In Bach"),),
    )
    conn.commit()
    outside_id = insert_score(conn, _copy("outside_bach.pdf"), "Outside Bach")

    result = api.start_transcribe_batch(api.TranscribeBatchIn(collection="Bach"))
    assert result["started"] is True
    _wait_for_batch()
    status = transcribe_batch.batch_status()
    assert status["total"] == 1
    assert status["results"][0]["score_id"] != outside_id
    assert status["results"][0]["outcome"] == "transcribed"


# ---------------------------------------------------------------------------
# 5. The scan-interaction matrix.
# ---------------------------------------------------------------------------


def test_a_scan_runs_while_a_bulk_transcription_pass_is_in_progress(
    library, extractable_pdf, monkeypatch
):
    """A scan started WHILE a bulk pass is still working must not be refused,
    and must not corrupt either one's results - see
    transcribe_batch.start_batch's docstring for why the two are not held
    against one another."""
    import shutil

    (library / "one.pdf").write_bytes(extractable_pdf.read_bytes())
    shutil.copy(extractable_pdf, library / "two.pdf")
    scanner._scan()
    conn = db.connect()
    ids = [r["id"] for r in conn.execute("SELECT id FROM scores ORDER BY path")]
    assert len(ids) == 2

    # Slow the batch down enough that a scan started right after it can
    # genuinely overlap it, rather than racing to finish first every time.
    def slow_process_one(score_id, reconvert):
        time.sleep(0.15)
        return api._batch_process_one(score_id, reconvert)

    started = transcribe_batch.start_batch(slow_process_one, ids, False)
    assert started is True

    try:
        scan_started = scanner.start_scan()
        assert scan_started is True  # NOT refused by the bulk pass running
    finally:
        # Waited out unconditionally, even if an assertion above already
        # failed - both are daemon threads that read module-level LIBRARY_DIR
        # bindings this test's fixtures monkeypatch, and letting either
        # outlive this test would have it read those bindings back reverted
        # mid-run.
        _wait_for_scan()
        _wait_for_batch()

    status = transcribe_batch.batch_status()
    assert status["total"] == 2
    assert status["processed"] == 2
    assert status["transcribed"] == 2
    assert not scanner.scan_status()["refused"]
    # The scan's own reconciliation is undisturbed: both files are still
    # accounted for, neither marked missing by a batch that never touches
    # scores.missing_since.
    rows = {r["path"]: dict(r) for r in conn.execute("SELECT * FROM scores")}
    assert rows["one.pdf"]["missing_since"] is None
    assert rows["two.pdf"]["missing_since"] is None


def test_a_bulk_transcription_pass_runs_while_a_scan_is_in_progress(
    library, extractable_pdf, monkeypatch
):
    """The other direction: a scan already under way when a bulk pass
    starts must not block it either."""
    import os
    import shutil

    (library / "one.pdf").write_bytes(extractable_pdf.read_bytes())
    shutil.copy(extractable_pdf, library / "two.pdf")
    scanner._scan()
    conn = db.connect()
    ids = [r["id"] for r in conn.execute("SELECT id FROM scores ORDER BY path")]
    assert len(ids) == 2

    # Slow every file's hashing down and force all of them to be re-hashed
    # (bump every mtime forward, defeating _scan_file's same-size-same-mtime
    # shortcut) so the scan is still genuinely running, several times over,
    # by the time the assertion below checks it - not racing a fast pass
    # that might finish inside the wake-up sleep on a loaded CI runner.
    real_hash_file = scanner.hash_file

    def slow_hash_file(path):
        time.sleep(0.2)
        return real_hash_file(path)

    monkeypatch.setattr(scanner, "hash_file", slow_hash_file)
    future = time.time() + 3600
    for name in ("one.pdf", "two.pdf"):
        os.utime(library / name, (future, future))

    # scanner._scan() itself never touches _state["scanning"] - only
    # start_scan() does, around spawning its own thread (see scanner.py) -
    # so the real threaded entry point is what this needs, not a bare Thread
    # wrapping _scan directly.
    scan_started = scanner.start_scan()
    assert scan_started is True
    try:
        time.sleep(0.1)  # let the scan actually get going before the batch starts
        assert scanner.scan_status()["scanning"] is True

        started = transcribe_batch.start_batch(api._batch_process_one, ids, False)
        assert started is True  # NOT refused by the scan running

        _wait_for_batch()
    finally:
        _wait_for_scan()

    status = transcribe_batch.batch_status()
    assert status["total"] == 2
    assert status["transcribed"] == 2
    assert not scanner.scan_status()["refused"]


# ---------------------------------------------------------------------------
# 6. A second batch refuses while one is already running.
# ---------------------------------------------------------------------------


def test_a_second_batch_refuses_while_one_is_running(app_env, extractable_pdf, monkeypatch, insert_score):
    monkeypatch.setattr(api, "LIBRARY_DIR", extractable_pdf.parent)
    conn = db.connect()
    score_id = insert_score(conn, extractable_pdf.name)

    release = threading.Event()

    def blocking_process_one(sid, reconvert):
        release.wait(timeout=10)
        return api._batch_process_one(sid, reconvert)

    first_started = transcribe_batch.start_batch(blocking_process_one, [score_id], False)
    assert first_started is True

    second_started = transcribe_batch.start_batch(api._batch_process_one, [score_id], False)
    assert second_started is False  # refused - nothing was changed by it

    release.set()
    _wait_for_batch()
    # The first (only) pass completed normally once released.
    assert transcribe_batch.batch_status()["processed"] == 1


# ---------------------------------------------------------------------------
# 7. The scan's own scan-triggered hook (#190): a freshly scanned library
#    transcribes itself, and says so.
# ---------------------------------------------------------------------------


def test_a_scan_that_adds_scores_starts_one_pass_over_exactly_those_ids(
    library, extractable_pdf, non_extractable_pdf, monkeypatch
):
    """#190's core mechanism: at the end of a scan's own chain, exactly one
    bulk transcription pass starts over exactly the ids that chain added -
    not the whole library, and not one pass per file the scan touched."""
    import shutil

    shutil.copy(extractable_pdf, library / "good.pdf")
    shutil.copy(non_extractable_pdf, library / "bad.pdf")

    calls = []
    real_start_batch = transcribe_batch.start_batch

    def recording_start_batch(process_one, score_ids, reconvert=False):
        calls.append(list(score_ids))
        return real_start_batch(process_one, score_ids, reconvert)

    monkeypatch.setattr(transcribe_batch, "start_batch", recording_start_batch)

    scan_started = scanner.start_scan()
    assert scan_started is True
    status = _wait_for_scan_chain_decision()
    _wait_for_batch()

    assert len(calls) == 1, f"expected exactly one bulk pass, got {len(calls)}: {calls}"
    conn = db.connect()
    ids = {r["id"] for r in conn.execute("SELECT id FROM scores")}
    assert len(ids) == 2
    assert set(calls[0]) == ids

    batch = transcribe_batch.batch_status()
    assert batch["total"] == 2
    assert batch["transcribed"] == 1
    assert batch["non_extractable"] == 1

    good_id = conn.execute("SELECT id FROM scores WHERE path='good.pdf'").fetchone()["id"]
    bad_id = conn.execute("SELECT id FROM scores WHERE path='bad.pdf'").fetchone()["id"]
    assert conn.execute(
        "SELECT COUNT(*) FROM transcriptions WHERE score_id=?", (good_id,)
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM transcriptions WHERE score_id=?", (bad_id,)
    ).fetchone()[0] == 0

    # Said in the scan's own status, in words - the one place anybody could
    # read it, since the startup scan runs unattended.
    assert status["transcribe_batch_started"] is True
    assert "2" in status["transcribe_batch_note"]


def test_a_scan_that_adds_a_score_does_not_interrupt_a_bulk_pass_already_running_by_hand(
    library, extractable_pdf, monkeypatch
):
    """#190's own rabbit hole: a hand-started bulk pass takes priority over
    the scan's own hook, not the other way round. The hook is refused the
    same way transcribe_batch.start_batch already refuses a second manual
    call - never a queue, never a retry - and the scan says so in its own
    status, in words a person can read."""
    import shutil

    shutil.copy(extractable_pdf, library / "existing.pdf")
    scanner._scan()
    conn = db.connect()
    existing_id = conn.execute(
        "SELECT id FROM scores WHERE path='existing.pdf'"
    ).fetchone()["id"]

    release = threading.Event()

    def blocking_process_one(score_id, reconvert):
        release.wait(timeout=10)
        return api._batch_process_one(score_id, reconvert)

    hand_started = transcribe_batch.start_batch(blocking_process_one, [existing_id], False)
    assert hand_started is True

    try:
        shutil.copy(extractable_pdf, library / "fresh.pdf")
        scan_started = scanner.start_scan()
        assert scan_started is True
        status = _wait_for_scan_chain_decision()

        assert status["transcribe_batch_started"] is False, status
        assert status["transcribe_batch_note"]
        assert "already running" in status["transcribe_batch_note"]

        # The hand-started pass itself is untouched - still just the one
        # score it was given, not the one the scan just added.
        running = transcribe_batch.batch_status()
        assert running["running"] is True
        assert running["total"] == 1
    finally:
        release.set()
        _wait_for_batch()

    # No queue (#190's No-gos): the freshly scanned score really was not
    # transcribed, not merely "not transcribed yet".
    fresh_id = conn.execute("SELECT id FROM scores WHERE path='fresh.pdf'").fetchone()["id"]
    assert conn.execute(
        "SELECT COUNT(*) FROM transcriptions WHERE score_id=?", (fresh_id,)
    ).fetchone()[0] == 0


def test_a_chained_rescan_starts_one_bulk_pass_over_the_union_of_every_id_the_chain_added(
    library, extractable_pdf, monkeypatch
):
    """#190: a scan whose chain has more than one pass - a second request
    landing while the first is still walking the library, so
    scanner._run_pending_rescan starts a follow-up pass over the library's
    new state - must still start only ONE bulk pass, over every id ANY pass
    in the chain added, never one pass per link.

    Built the same way test_a_bulk_transcription_pass_runs_while_a_scan_is_
    in_progress builds a scan slow enough to prove genuine overlap:
    hash_file slowed down enough that a second start_scan() call, made
    while the first pass is still hashing its only file, is genuinely
    declined for re-entrancy (see scanner._rescan_pending) rather than
    racing to go first.
    """
    import shutil

    shutil.copy(extractable_pdf, library / "one.pdf")

    calls = []
    real_start_batch = transcribe_batch.start_batch

    def recording_start_batch(process_one, score_ids, reconvert=False):
        calls.append(list(score_ids))
        return real_start_batch(process_one, score_ids, reconvert)

    monkeypatch.setattr(transcribe_batch, "start_batch", recording_start_batch)

    real_hash_file = scanner.hash_file

    def slow_hash_file(path):
        time.sleep(0.3)
        return real_hash_file(path)

    monkeypatch.setattr(scanner, "hash_file", slow_hash_file)

    scan_started = scanner.start_scan()
    assert scan_started is True
    time.sleep(0.1)  # let it start hashing one.pdf
    assert scanner.scan_status()["scanning"] is True

    # A second file lands mid-pass, and the request made for it is declined
    # rather than starting a scan of its own - the running pass already took
    # its directory listing, before this file existed.
    shutil.copy(extractable_pdf, library / "two.pdf")
    second_call_started = scanner.start_scan()
    assert second_call_started is False

    _wait_for_scan_chain_decision()
    _wait_for_batch()

    assert len(calls) == 1, (
        f"expected one bulk pass for the whole chain, got {len(calls)}: {calls}"
    )
    conn = db.connect()
    ids = {r["id"] for r in conn.execute("SELECT id FROM scores")}
    assert len(ids) == 2
    assert set(calls[0]) == ids

    batch = transcribe_batch.batch_status()
    assert batch["total"] == 2
    assert batch["transcribed"] == 2


def test_an_upload_starts_exactly_one_bulk_pass_over_exactly_the_uploaded_scores_id(
    client, library, extractable_pdf, monkeypatch
):
    """#190: POST /api/upload already triggers a scan (see api.upload) -
    this is the one hook covering uploads too, proven directly through the
    real endpoint rather than only inferred from the scan-triggered tests
    above."""
    calls = []
    real_start_batch = transcribe_batch.start_batch

    def recording_start_batch(process_one, score_ids, reconvert=False):
        calls.append(list(score_ids))
        return real_start_batch(process_one, score_ids, reconvert)

    monkeypatch.setattr(transcribe_batch, "start_batch", recording_start_batch)

    res = client.post(
        "/api/upload?folder=Uploads",
        files={"file": ("fresh.pdf", extractable_pdf.read_bytes(), "application/pdf")},
    )
    assert res.status_code == 200

    _wait_for_scan_chain_decision()
    _wait_for_batch()

    conn = db.connect()
    uploaded_id = conn.execute(
        "SELECT id FROM scores WHERE path='Uploads/fresh.pdf'"
    ).fetchone()["id"]

    assert len(calls) == 1, f"expected exactly one bulk pass, got {len(calls)}: {calls}"
    assert calls[0] == [uploaded_id]

    status = transcribe_batch.batch_status()
    assert status["total"] == 1
    assert status["transcribed"] == 1
