"""Response shapes for the REST surface in api.py (issue #19).

Every model here describes what a handler ACTUALLY returns, not an aspiration
of what it should return - each was written by reading the handler and the
dict-building helper it calls (`_with_tags`, `practice.session_dict`,
`practice.goal_dict`, `_transcription_dict`, `instruments.string_details`,
`scanner.scan_status`, ...) and writing down every key those produce, with a
type that admits every value that key can actually hold.

Two things follow from that discipline, and both are deliberate:

- A field is `| None` whenever the handler can hand back `None` for it - a
  transcription's Rule 8 figures before anything has measured them, a
  session's tempo, a goal's `score_id`. Making it non-optional to look tidier
  would be documentation that lies the first time a reader hits the real
  value.
- Where two branches of the SAME handler produce dicts with different key
  sets - `practice.goal_progress`'s uncountable-goal branch omits
  `sessions_inferred` and the per-day `inferred` key that the countable
  branch's `period_facts()` always includes - the field is optional with a
  default rather than "fixed" to always be present. This module documents
  response shapes; it does not change what the handlers compute, and
  response-model validation must not 500 a real, existing response.

WHY THIS IS A SEPARATE MODULE and not classes sitting next to their routes in
api.py: api.py is being edited concurrently by other work (issue #143's
`_BAR_KEYS` tuple, and unrelated route logic), and a large mechanical set of
response classes interleaved with route bodies would be exactly the kind of
diff that turns a two-line change into a conflict. Every model here is
imported by api.py and attached with `response_model=`; nothing in api.py's
route bodies changes shape because of it.

`TranscriptionOut`'s bar-figure and provenance fields are the one place this
module has to track another module's data by hand rather than by importing
it: they mirror api.py's `_BAR_KEYS`, `_BAR_LIST_KEYS`, `_BAR_AMOUNT_KEYS` and
`_PROVENANCE_KEYS` tuples, which this module cannot import without a circular
dependency (those tuples are defined in api.py, which imports this module for
`response_model=`). tests/test_api_docs.py pins the two against each other,
so a future change to those tuples fails a test here rather than silently
documenting a narrower response than the one actually sent.
"""

from typing import Any

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------


class HealthOut(BaseModel):
    status: str


class VersionOut(BaseModel):
    version: str
    commit: str
    built: str


class MeOut(BaseModel):
    """The identity, if any, that fermata.authproxy's reverse-proxy auth
    middleware attached to this request (issue #16). Fermata has no accounts
    of its own and builds none here - `username` is only ever a name a
    trusted reverse proxy vouched for, exposed for a future consumer (the
    planned MCP server, a possible sharing layer) to read. `enabled` is
    whether reverse-proxy auth is turned on at all, independent of whether
    THIS request carried an identity, so a client can tell "no auth
    configured" apart from "auth configured but nothing to show" without
    guessing from a null."""

    enabled: bool
    username: str | None


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class SettingsOut(BaseModel):
    """The two preferences Fermata stores today - see api.SETTINGS_DEFAULTS.
    A new setting key needs a field added here alongside SETTINGS_DEFAULTS,
    the same way an undocumented new route needs a response model: the point
    of this file is that the schema cannot silently fall behind what is
    actually sent."""

    staff_theme: str
    week_starts_on: str


# ---------------------------------------------------------------------------
# Instruments
# ---------------------------------------------------------------------------


class InstrumentStringOut(BaseModel):
    """One string's nominal tuning and what it actually sounds under the
    instrument's capo - see instruments.string_details."""

    number: int
    pitch: str
    midi: int
    frequency: float
    sounding_pitch: str
    sounding_midi: int
    sounding_frequency: float


class InstrumentOut(BaseModel):
    id: int
    owner: str
    kind: str
    name: str
    fretted: bool
    string_count: int
    string_pitches: list[str]
    fret_count: int | None
    capo: int | None
    reference_pitch: float
    created_at: str
    updated_at: str
    # Derived, not stored - see api._instrument_dict.
    strings: list[InstrumentStringOut]


class InstrumentPresetOut(BaseModel):
    """A built-in tuning offered before anything is saved - see
    instruments.presets(). Shares every field InstrumentOut has except the
    ones that only exist once a definition is a row: id, owner, and the two
    timestamps."""

    key: str
    name: str
    kind: str
    fretted: bool
    string_count: int
    string_pitches: list[str]
    fret_count: int | None
    capo: int | None
    reference_pitch: float
    strings: list[InstrumentStringOut]


class InstrumentDeleteOut(BaseModel):
    deleted: int
    scores_unlinked: int


# ---------------------------------------------------------------------------
# Scores / library
# ---------------------------------------------------------------------------


class ScoreOut(BaseModel):
    """A score row as api._with_tags presents it: the scores table's own
    columns plus tags, transcription presence, and practice totals joined in
    for every list and detail view alike."""

    id: int
    title: str
    composer: str | None
    collection: str | None
    series: str | None
    source: str | None
    path: str
    file_type: str
    content_kind: str
    pages: int | None
    favorite: bool
    hash: str
    size: int
    mtime: float
    last_page: int
    missing_since: str | None
    # Set only on a score somebody deleted (#56). `deleted_at` says when, and
    # `deleted_from` is the library-relative path the file was at before it was
    # moved into the trash - which is where restoring puts it back. Both null
    # for every score in the library proper, which is the great majority of
    # them; `path` on a deleted row names the file's location inside the trash.
    deleted_at: str | None
    deleted_from: str | None
    added_at: str
    instrument_id: int | None
    tags: list[str]
    has_transcription: bool
    practice_seconds: int
    last_practiced: str | None


class DuplicateGroupOut(BaseModel):
    hash: str
    count: int
    scores: list[ScoreOut]


class CollectionOut(BaseModel):
    collection: str
    count: int
    missing: int


class TagOut(BaseModel):
    name: str
    count: int


# ---------------------------------------------------------------------------
# Practice: sessions
# ---------------------------------------------------------------------------


class PracticeSessionOut(BaseModel):
    """One session as practice.session_dict presents it: the stored row plus
    three derived facts - see that function's docstring for what each one
    means and why it is derived rather than stored."""

    id: int
    owner: str
    score_id: int | None
    activity: str
    mode: str | None
    started_at: str
    local_date: str
    seconds: int
    from_bar: int | None
    to_bar: int | None
    from_page: int | None
    to_page: int | None
    tempo_bpm: int | None
    target_tempo_bpm: int | None
    rating: int | None
    note: str | None
    local_date_source: str
    reached_target: bool | None
    score_missing: bool


class PracticeSessionListOut(PracticeSessionOut):
    """A session as GET /api/practice/sessions lists it - the same fields,
    plus the piece's title joined in so a reader is not left with a bare
    score_id (or, for a session with none, `null`).

    `score_deleted` is the same fact `top_scores` and `by_score` carry, on the
    list that names individual sessions: the piece is in the trash. It is NOT
    `score_missing`, which means the row itself has gone and there is no piece
    left to name at all - a deleted score still has its title, its history and
    a way back, and a client that conflates the two would offer to restore
    something that cannot be restored. Both are false for a session logged
    against no piece.
    """

    score_title: str | None
    score_deleted: bool


class ScorePracticeOut(BaseModel):
    """GET /api/scores/{id}/practice: this piece's recent sessions and
    totals - see api._practice_totals and api._recent_sessions."""

    sessions: list[PracticeSessionOut]
    session_count: int
    practice_seconds: int
    last_practiced: str | None


class LogPracticeOut(ScorePracticeOut):
    """POST /api/scores/{id}/practice: the session just logged, plus the
    same recent-sessions-and-totals view ScorePracticeOut carries."""

    session: PracticeSessionOut


class SessionListOut(BaseModel):
    sessions: list[PracticeSessionListOut]
    total: int
    truncated: bool


class TopScoreOut(BaseModel):
    id: int
    title: str
    practice_seconds: int
    # True when this score has been deleted (#56). It is still counted - the
    # hours were spent, and dropping it would stop these figures adding up to
    # the week beside them - but it is no longer in the library, so a client
    # must not offer it as somewhere to go. See api.practice_summary.
    deleted: bool


class PracticeSummaryOut(BaseModel):
    week_seconds: int
    week_sessions: int
    top_scores: list[TopScoreOut]


class SessionDeleteOut(BaseModel):
    deleted: int


# ---------------------------------------------------------------------------
# Practice: history / review facts
# ---------------------------------------------------------------------------


class DayFactOut(BaseModel):
    """One day's totals - see practice.period_facts. Always carries
    `inferred`: this shape is only ever produced by period_facts, which
    always sets it (contrast GoalDayOut, below)."""

    date: str
    seconds: int
    sessions: int
    inferred: int


class ByScoreOut(BaseModel):
    score_id: int
    title: str
    seconds: int
    sessions: int
    last_practised: str | None
    # Same fact, same reason as TopScoreOut.deleted - see practice.time_spent.
    deleted: bool


class ByActivityOut(BaseModel):
    activity: str
    seconds: int
    sessions: int


class PeriodFactsOut(BaseModel):
    """practice.period_facts()'s own return shape, exactly - no window bounds
    and no by-score/by-activity breakdown, because period_facts never returns
    either. Used both as the base of PracticeHistoryOut (which adds the
    window and practice.time_spent's breakdown) and as WeekReviewOut.facts
    (which is period_facts's return value, untouched, nested under a key)."""

    days: list[DayFactOut]
    seconds: int
    minutes: int
    days_practised: int
    sessions: int
    sessions_inferred: int


class PracticeHistoryOut(PeriodFactsOut):
    """GET /api/practice/history: practice.period_facts and
    practice.time_spent over one window, with the window's own bounds."""

    start: str
    end: str
    by_score: list[ByScoreOut]
    by_activity: list[ByActivityOut]
    scores_worked: int
    by_score_truncated: bool


# ---------------------------------------------------------------------------
# Practice: goals
# ---------------------------------------------------------------------------


class GoalDayOut(BaseModel):
    """One day inside a goal's progress. `inferred` is absent - and reads as
    None here - on the uncountable-goal branch of practice.goal_progress,
    which builds its own placeholder days rather than calling period_facts;
    every other path through goal_progress sets it. See that function's
    docstring for what `countable` means."""

    date: str
    seconds: int
    sessions: int
    inferred: int | None = None


class GoalProgressOut(BaseModel):
    """practice.goal_progress's return shape. `sessions_inferred` and every
    per-day `inferred` are unset only on the uncountable branch - see
    GoalDayOut and goal_progress's own docstring for `countable`, `met_days`,
    `met_minutes` and `met`."""

    days: list[GoalDayOut]
    seconds: int
    minutes: int
    days_practised: int
    sessions: int
    sessions_inferred: int | None = None
    status: str
    days_left: int
    countable: bool
    met_days: bool | None
    met_minutes: bool | None
    met: bool | None


class GoalOut(BaseModel):
    """practice.goal_dict's return shape: a practice_goals row, the piece's
    title if it names one, and its progress."""

    id: int
    owner: str
    period: str
    period_start: str
    period_end: str
    target_days: int | None
    target_minutes: int | None
    scope: str
    score_id: int | None
    activity: str | None
    intent: str | None
    reflection: str | None
    realistic: str | None
    created_at: str
    updated_at: str
    score_title: str | None
    progress: GoalProgressOut


class CurrentGoalOut(BaseModel):
    goal: GoalOut | None
    today: str
    week_starts_on: str
    week_start: str
    week_end: str


class GoalListOut(BaseModel):
    goals: list[GoalOut]


class GoalDeleteOut(BaseModel):
    deleted: int


class WeekReviewOut(BaseModel):
    """One week of GET /api/practice/review - the week's own facts (unscoped,
    even when a goal narrows to one piece - see practice_review's docstring),
    its goal if one was set, and where the practice went that week."""

    period: str
    period_start: str
    period_end: str
    status: str
    goal: GoalOut | None
    facts: PeriodFactsOut
    by_score: list[ByScoreOut]
    by_activity: list[ByActivityOut]
    scores_worked: int
    by_score_truncated: bool


class PracticeReviewOut(BaseModel):
    today: str
    week_starts_on: str
    weeks: list[WeekReviewOut]


# ---------------------------------------------------------------------------
# Practice: one piece (#57)
# ---------------------------------------------------------------------------


class ScoreAllTimeOut(BaseModel):
    """practice.score_all_time: this piece's whole record, ignoring the window
    every other block on the response is bounded by. `first_practised` and
    `last_practised` are practice DAYS, not timestamps - null when the piece
    has never been practised, which is not the same as a day of zero."""

    sessions: int
    seconds: int
    minutes: int
    first_practised: str | None
    last_practised: str | None
    sessions_inferred: int


class TempoPointOut(BaseModel):
    """One session's tempo, as practice.tempo_progression reports it. Two
    numbers somebody entered and the day they entered them for; nothing here
    is fitted, smoothed or extrapolated."""

    session_id: int
    date: str
    tempo_bpm: int
    target_tempo_bpm: int | None
    reached_target: bool | None
    mode: str | None


class TempoProgressionOut(BaseModel):
    """practice.tempo_progression's return shape.

    `axis_low` / `axis_high` are the bounds a chart of these points needs and
    are not a personal best - see that function's docstring. `comparable` is
    false for a single point, which is the response saying outright that there
    is no progression here to draw. Both bounds are null when there are no
    points at all."""

    points: list[TempoPointOut]
    count: int
    sessions_without_tempo: int
    axis_low: int | None
    axis_high: int | None
    latest_target: int | None
    comparable: bool


class ByModeOut(BaseModel):
    """practice.mode_totals: section work against run-throughs, for one piece.
    `mode` is null for the sessions that did not say which they were."""

    mode: str | None
    seconds: int
    sessions: int


class RatingCountOut(BaseModel):
    rating: int
    sessions: int


class RatingsOut(BaseModel):
    """practice.rating_counts: how many sessions got each 1-5 rating. Counts
    and never a mean - see that function's docstring."""

    counts: list[RatingCountOut]
    rated: int
    unrated: int


class ScoreProgressOut(BaseModel):
    """GET /api/scores/{id}/practice/progress - how one piece is going (#57).

    `grouped_by` names the column every figure below was grouped and filtered
    by, and is always practice.GROUPED_BY. It is stated rather than assumed
    because it is the answer to a question a reader of these numbers has to
    ask: a day here is the practiser's own calendar day where their client
    recorded one, and the UTC day of the timestamp where it did not. See
    `all_time.sessions_inferred` and `window.sessions_inferred` for how much of
    each total rests on the second kind.

    `deleted` is true for a piece in the trash. Its practice is still counted
    here in full - the hours were spent - and a client must not offer it as
    somewhere to go (#56).

    `practised` is whether this piece has ever been practised at all. False
    means every figure below is a zero about a piece nobody has played yet,
    which is a different thing from a quiet window, and a view that cannot tell
    them apart shows a new user a wall of noughts as though it were data."""

    score_id: int
    title: str
    deleted: bool
    practised: bool
    start: str
    end: str
    grouped_by: str
    all_time: ScoreAllTimeOut
    window: PeriodFactsOut
    tempo: TempoProgressionOut
    modes: list[ByModeOut]
    ratings: RatingsOut
    goals: list[GoalOut]
    sessions: list[PracticeSessionOut]
    session_total: int
    sessions_truncated: bool


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------


class TranscriptionAnalysisOut(BaseModel):
    """GET /api/scores/{id}/transcription/analysis - tabextract.analyze()'s
    shape, which api.get_transcription_analysis also returns by hand for a
    non-pdf score."""

    extractable: bool
    reason: str | None
    vector: bool
    tab_staff_count: int
    standard_staff_count: int
    page_count: int


class TranscriptionOut(BaseModel):
    """A transcription row as api._transcription_dict presents it: the raw
    columns, the Rule 8 conformance figures, and the meter/key/tuning
    provenance - every one of them `| None` because "not recorded" (a hand
    edit, or a row from before a figure was persisted) is a real, common
    state and not a defect in this model. See _transcription_dict's own
    module-level comment for what each group of fields means and why none of
    it is recoverable from the warning prose alone.
    """

    id: int
    score_id: int
    format: str
    content: str
    source: str
    # The stored blob, parsed - or the raw column text if it did not parse as
    # JSON, or None/empty if nothing was ever stored. See _transcription_dict.
    #
    # `Any` and not `dict[str, Any] | str | None`: the column is a bare TEXT
    # value nothing but transcribe() constrains in the ordinary path (always
    # a JSON object there), but _transcription_dict's own parse only checks
    # that json.loads succeeded, not that the result was a dict - a stored
    # value that happens to be a JSON array, number or bool parses cleanly
    # and is handed back as-is. That row shape is not reachable through this
    # application today, but the helper tolerates it and this model has to
    # tolerate whatever the helper actually hands it, or a row nothing here
    # wrote could 500 a plain GET instead of degrading the way the helper
    # already does.
    confidence: Any = None
    created_at: str
    updated_at: str
    warnings: list[str]

    # _BAR_KEYS
    bars_overfull: int | None
    bars_short: int | None
    bars_defective: int | None
    bars_measured: int | None
    bars_padded: int | None
    bars_unread: int | None
    notes_no_stem: int | None
    staves_no_stem: int | None
    dots_unassigned: int | None
    dots_unassigned_no_candidate: int | None
    dots_unassigned_eliminated: int | None
    staves_dots_unassigned: int | None
    repeats_unread: int | None
    endings_unread: int | None
    endings_truncated: int | None
    form_marks_unanchored: int | None
    endings_incomplete: int | None
    unison_digits_shared: int | None
    coincident_unsplit_pairs: int | None
    staves_coincident_unsplit: int | None
    nav_marks_unanchored: int | None
    nav_marks_unresolved: int | None
    # How many staff systems' durations came from the horizontal gaps between
    # noteheads rather than from the noteheads, and how many were read from
    # the engraving with something on them left unread (issue #117). These are
    # the counts belonging to `spacing_bars` / `degraded_bars` below, which
    # have been on this model since they existed while the counts lived only
    # in an extraction-time field nothing stored.
    staves_spacing_rhythm: int | None
    staves_degraded_rhythm: int | None
    # Printed time signatures REFUSED because a glyph with no category sat
    # among their digits (issue #129). Distinct from anything
    # `time_signature_source` can say: that field describes the meter that IS
    # reported, and cannot say that a different, unread one is printed on the
    # page.
    meter_digits_unreadable: int | None
    # An end of a tie the decoder matched in the engraving whose other end it
    # did not, so the tie is not written and its second note is transcribed as
    # separately re-struck rather than held (issue #81). Every tie that WAS
    # written is countable from the transcription itself; this is the only
    # place the ones that were not appear at all.
    tie_ends_unpaired: int | None
    # A system whose bars were not read at all (issue #152) - music ABSENT
    # from this transcription rather than imperfect in it. Every other figure
    # on this model describes only the systems that WERE read, which makes
    # this the one that says how far that qualification reaches.
    systems_unread: int | None

    # _BAR_LIST_KEYS
    padded_bars: list[int] | None
    unread_bars: list[int] | None
    spacing_bars: list[int] | None
    degraded_bars: list[int] | None
    repeats_unread_bars: list[int] | None
    endings_unread_bars: list[int] | None
    endings_truncated_bars: list[int] | None
    form_marks_unanchored_bars: list[int] | None
    # `nav_marks_unanchored` has no list of its own on purpose: a navigation
    # mark with no bar to name has no bar number to report (issue #134
    # phase 2, Rule 16).
    nav_marks_unresolved_bars: list[int] | None
    # PAGES, not bars, and for the reason `nav_marks_unanchored` has no list
    # at all: a system that was never read has no bar numbers to report. The
    # page is the coordinate that survives (issue #152).
    systems_unread_pages: list[int] | None
    # WHICH bars hold an end of a tie whose other end was not found (issue
    # #81). The bar named is where the unmatched END is, which for a tie
    # broken across a system break is as often the bar the phrase resumes in
    # as the one it left.
    tie_ends_unpaired_bars: list[int] | None

    # _BAR_AMOUNT_KEYS
    inferred_rest_quarters: float | None

    # _PROVENANCE_KEYS
    time_signature: list[int] | None
    time_signature_source: str | None
    key_fifths: int | None
    key_signature_source: str | None
    tuning: list[str] | None
    tuning_label: str | None
    # How `tuning` was obtained: "instrument", "label", "assumed standard", or
    # null on a hand-edited row or one extracted before this was recorded (issue
    # #80). The word that stops an assumed tuning being read back as a read one.
    tuning_source: str | None
    tuning_unread: list[str] | None


class TranscribeResultOut(TranscriptionOut):
    """POST /api/scores/{id}/transcribe: everything TranscriptionOut carries,
    plus extraction detail that exists only on this one response - see
    transcribe()'s closing comment for why bars/beats/notes/tempo are not
    re-read from the stored row the way the rest of this response is."""

    bars: int
    beats: int
    notes: int
    tempo: int | None


class TranscribeBatchResultLineOut(BaseModel):
    """One score's outcome from a bulk transcription pass - see
    api._batch_process_one for what earns each `outcome`. `reason` is set
    for every outcome except "transcribed"; never a silent skip is the
    whole point of this shape (issue #55). `bars_defective` /
    `bars_measured` are only ever set for "transcribed" - see
    api._store_extraction_result's own comment on why a bar count cannot be
    inherited from anything but a measurement of the content that produced
    it."""

    score_id: int
    # None only when the id named no score at all - every other outcome has
    # the title of a real row, even a deleted or non-pdf one, because "which
    # score" is the first thing a person reading this list needs.
    title: str | None
    outcome: str
    reason: str | None
    bars_defective: int | None
    bars_measured: int | None


class TranscribeBatchStatusOut(BaseModel):
    """transcribe_batch.batch_status()'s shape - see transcribe_batch._state
    for what each running total means, and TranscribeBatchResultLineOut for
    one line of `results`."""

    running: bool
    total: int
    processed: int
    transcribed: int
    already_transcribed: int
    non_extractable: int
    errored: int
    with_defective_bars: int
    reconvert: bool
    results: list[TranscribeBatchResultLineOut]
    started_at: float | None
    finished_at: float | None


class TranscribeBatchTriggerOut(TranscribeBatchStatusOut):
    """POST /api/transcribe/batch: whether this call actually started a
    pass, plus the status left behind by whichever pass (this one, or one
    already running) is current - mirrors ScanTriggerOut for the same
    reason POST /transcribe/batch mirrors POST /scan."""

    started: bool


# ---------------------------------------------------------------------------
# Scan / upload
# ---------------------------------------------------------------------------


class ScanStatusOut(BaseModel):
    """scanner.scan_status()'s shape - see scanner._state for what each field
    means, and the module comment above it for the refusal fields
    (`refused`, `refused_reason`, `unmatched_paths`, `unmatched_count`,
    `acknowledge_token`)."""

    scanning: bool
    total: int
    processed: int
    added: int
    updated: int
    missing: int
    restored: int
    unmatched_moves: int
    refused: bool
    refused_reason: str | None
    unmatched_paths: list[str]
    unmatched_count: int
    acknowledge_token: str | None
    errors: int
    last_error: str | None
    started_at: float | None
    finished_at: float | None


class ScanTriggerOut(ScanStatusOut):
    """POST /api/scan and POST /api/scan/acknowledge: whether this call
    actually started a pass, plus the status left behind by whichever pass
    (this one, or one already running) is current."""

    started: bool


class UploadOut(BaseModel):
    saved: str


# ---------------------------------------------------------------------------
# Managing the library: moving, renaming, deleting and reorganising (#56)
#
# This is the one part of the API that WRITES to a person's own files, so the
# shapes here are built around saying what happened rather than around saying
# it worked. Every operation answers with the exact paths involved; every
# destructive one answers with what it kept and what it destroyed, counted
# from the database rather than assumed.
# ---------------------------------------------------------------------------


class MovePlanItemOut(BaseModel):
    """One line of a move, whether or not it has happened yet.

    The same shape is used for a dry run and for a move that was applied,
    deliberately: a preview that is a different shape from the thing it
    previews is a preview of something else. `status` is 'move' (this file
    goes, or went, from `from_path` to `to_path`), 'unchanged' (it is already
    where it was asked to be, and nothing will be touched) or 'blocked'
    (something is in the way - `reason` says what, in words meant for a
    person). A plan containing any 'blocked' line is refused as a whole rather
    than applied in part.
    """

    score_id: int
    title: str
    from_path: str
    to_path: str
    status: str
    reason: str | None = None


class ScoreMoveOut(BaseModel):
    """POST /api/scores/{id}/move: where one score's file went.

    `score` is the score as it now stands - unchanged when this was a dry run,
    which is what makes a dry run's response readable as "this is what you have
    at the moment, and this is what would change".
    """

    dry_run: bool
    applied: bool
    moves: list[MovePlanItemOut]
    score: ScoreOut


class LibraryMoveOut(BaseModel):
    """POST /api/library/move: a batch move, and its per-score plan.

    `moved`, `unchanged` and `blocked` count the lines in `moves` by status, so
    a caller has the headline without walking the list. On a dry run they
    describe what WOULD happen; nothing on disk has been touched.
    """

    dry_run: bool
    applied: bool
    folder: str
    moves: list[MovePlanItemOut]
    moved: int
    unchanged: int
    blocked: int


class FolderOut(BaseModel):
    """One folder in the library tree, as the move dialog offers it.

    `score_count` counts the scores whose file is directly in this folder, not
    the ones under it - a total including subfolders would make the top-level
    entry read as though everything were in it.
    """

    path: str
    name: str
    depth: int
    score_count: int


class FolderCreateOut(BaseModel):
    """POST /api/library/folders. `existed` is true when the folder was
    already there - which is not an error (asking for a folder that exists
    leaves you with the folder you asked for) but is worth saying, so an
    interface can tell "created" from "was already yours"."""

    created: str
    existed: bool


class FolderRenameOut(BaseModel):
    """POST /api/library/folders/rename: a folder moved, and every score that
    went with it."""

    dry_run: bool
    applied: bool
    from_path: str
    to_path: str
    moves: list[MovePlanItemOut]
    moved: int


class ScoreDeleteOut(BaseModel):
    """DELETE /api/scores/{id}: a score moved to the trash.

    The four `_kept` counts are the point of this shape. This deletion destroys
    nothing, and the way to make that credible is to state, from the database
    and after the fact, exactly how much is still attached to the row: the
    practice sessions, the goals, the tags and the transcriptions. An interface
    can then say "still holding 14 sessions" rather than "deleted (trust us)".
    """

    deleted: int
    title: str
    deleted_from: str
    # Null when there was no file to move - a score whose file had already gone
    # can still be deleted, and saying it was "trashed to" somewhere would be a
    # path with nothing at it. `file_moved` is the same fact as a flag, stated
    # so a client branches on a boolean rather than on a null.
    trashed_to: str | None
    file_moved: bool
    practice_sessions_kept: int
    goals_kept: int
    tags_kept: int
    transcriptions_kept: int


class ScoreRestoreOut(BaseModel):
    """POST /api/trash/{id}/restore: a score taken back out of the trash.

    `restored_to` is where the file actually ended up, which is usually where
    it came from and is not always: something else may have taken that path
    while the score was in the trash, in which case the file lands beside it
    under a distinct name rather than overwriting it. Reported rather than
    assumed, because "it is back" and "it is back where it was" are different
    claims.
    """

    restored: int
    restored_from: str
    restored_to: str
    # False when there was no file in the trash to bring back - the score
    # returns to the library flagged as missing rather than being stranded in
    # the trash for ever. See api.restore_score.
    file_restored: bool
    score: ScoreOut


class ScorePurgeOut(BaseModel):
    """DELETE /api/trash/{id}: the destructive one.

    Everything this shape reports is a thing that is now gone or a thing that
    survived, counted before the delete ran so the numbers describe what was
    actually there. `tags_destroyed` and `transcriptions_destroyed` cascade
    with the row; `practice_sessions_kept` and `goals_kept` do not, because
    the hours were still spent (see db.py's schema notes on why those two
    references are ON DELETE SET NULL and these two are ON DELETE CASCADE).
    """

    deleted: int
    title: str
    file_deleted: str | None
    tags_destroyed: int
    transcriptions_destroyed: int
    practice_sessions_kept: int
    goals_kept: int


# ---------------------------------------------------------------------------
# Getting everything in and out (#58)
#
# GET /api/export has no response_model - it answers with the zip archive's
# bytes, not JSON, the same reason GET .../file and .../thumb have none (see
# _BINARY_ROUTES in test_api_docs.py). This model is for the other half:
# POST /api/import, whose shape is the same whether it actually wrote
# anything or only validated the archive and reported what it would do - see
# ImportOut.dry_run, and api.import_library's own docstring for why that
# symmetry (same shape, dry or applied) is deliberate rather than incidental,
# the same reasoning ScoreMoveOut and LibraryMoveOut already state for #56's
# bulk operations.
# ---------------------------------------------------------------------------


class ImportOut(BaseModel):
    """POST /api/import: what an archive held, or what was actually written
    from it - see `dry_run`. Every count is of ROWS (or, for `files_written`,
    of files), not of "things that changed" - import never updates or
    replaces anything already in the library, only adds, so a count here is
    also a count of what is new since this call."""

    dry_run: bool
    schema_version: int
    exported_at: str
    fermata_version: str
    scores_imported: int
    scores_trashed_imported: int
    files_written: int
    transcriptions_imported: int
    tags_imported: int
    # How many of the archive's tags matched a tag already in this library by
    # name, and were reused rather than duplicated - the one place import
    # deduplicates against what is already here rather than only adding (see
    # api._apply_import). Always 0 on a dry run: nothing has been compared
    # against the live tags table without a transaction open to read it
    # through, and reporting a real number here would claim a merge decision
    # that has not actually been made yet.
    tags_reused: int
    score_tags_imported: int
    instruments_imported: int
    practice_sessions_imported: int
    practice_goals_imported: int
    settings_imported: int
    # #6's two: how many setlists, and how many membership rows across them,
    # the archive carried (or would restore). A membership row for a score the
    # export left out was already dropped on the way out, so this counts what
    # actually travels, not what the setlists held before export.
    setlists_imported: int
    setlist_scores_imported: int


# ---------------------------------------------------------------------------
# Trainer: per-attempt fretboard drill results (issue #27)
# ---------------------------------------------------------------------------


class TrainerAttemptOut(BaseModel):
    """One question from a fretboard drill, as trainer.attempt_dict presents
    it - the stored row verbatim, plus `correct` as a real bool.

    Exactly one of (target_string, target_fret) or (given_string, given_fret)
    is non-null, decided by `direction` - see trainer.py's module docstring
    for why a position-to-note question has no given position and a
    note-to-position one has no single target position.
    """

    id: int
    owner: str
    session_id: int | None
    drill: str
    direction: str
    target_string: int | None
    target_fret: int | None
    target_note: str
    given_string: int | None
    given_fret: int | None
    given_note: str
    correct: bool
    response_ms: int | None
    created_at: str


class TrainerAttemptListOut(BaseModel):
    """GET /api/trainer/attempts: the raw, queryable record - which positions
    and which notes get missed is a WHERE clause over this, not something a
    reader has to parse out of a session's note."""

    attempts: list[TrainerAttemptOut]
    total: int
    truncated: bool


# ---------------------------------------------------------------------------
# Chord flash cards: per-attempt results (issue #28), in their own table -
# see db.py's comment on trainer_chord_attempts for why a chord does not fit
# TrainerAttemptOut's single-note columns above.
# ---------------------------------------------------------------------------


class ChordShapePosition(BaseModel):
    """One fretted position of a shown or tapped chord shape - a string and
    the fret held on it. A string simply absent from a shape's list is
    muted, the same convention an "x" marks in a chord diagram."""

    string: int
    fret: int


class TrainerChordAttemptOut(BaseModel):
    """One question from the chord flash card drill, as trainer.
    chord_attempt_dict presents it - the stored row verbatim, `correct` as a
    real bool, and target_shape/given_shape/given_notes turned back into
    lists rather than the JSON text they are stored as.

    Exactly one of (given_root, given_quality) or (given_notes, given_shape)
    is non-null, decided by `direction` - see trainer.py's module docstring
    on why a chord name and a tapped shape are graded by the same rule even
    so."""

    id: int
    owner: str
    session_id: int | None
    drill: str
    direction: str
    target_root: str
    target_quality: str
    target_shape: list[ChordShapePosition] | None
    given_root: str | None
    given_quality: str | None
    given_notes: list[str] | None
    given_shape: list[ChordShapePosition] | None
    correct: bool
    response_ms: int | None
    created_at: str


class TrainerChordAttemptListOut(BaseModel):
    """GET /api/trainer/chord-attempts: the raw, queryable record - which
    chords get missed is a WHERE clause over this."""

    attempts: list[TrainerChordAttemptOut]
    total: int
    truncated: bool


# ---------------------------------------------------------------------------
# Setlists (#6)
#
# A setlist is an ordered collection of scores a player works through. The
# shapes here mirror api.setlist_dict / api.setlist_summary exactly, the same
# discipline the rest of this module keeps: every key those helpers put on the
# wire has a field here, typed to admit every value it can actually hold.
#
# A MEMBER CARRIES ITS WHOLE SCORE, not a thinned-down reference. That is
# issue #32's rule - every field readable through the documented API, one
# source of truth - and it is what lets the setlist view show per-piece
# practice progress (each ScoreOut carries practice_seconds and last_practiced)
# without a second round of calls. A member whose score is in the trash (#56)
# is still listed, with `score.deleted_at` set: the setlist marks it deleted
# rather than dropping it or showing a broken link. A member whose score has
# been PURGED is not here at all - the membership row cascaded away with the
# score row - so there is no such thing as a member with a null score.
# ---------------------------------------------------------------------------


class SetlistOut(BaseModel):
    """A setlist as api.setlist_summary presents it, for the list view and as
    the echo returned by create and rename: the row's own columns plus
    `score_count`, how many scores it holds (trashed members included - they
    are still in the setlist). The ordered members themselves are on
    SetlistDetailOut, fetched one setlist at a time."""

    id: int
    owner: str
    name: str
    created_at: str
    updated_at: str
    score_count: int


class SetlistMemberOut(BaseModel):
    """One entry in a setlist: a whole score at its position in the order.

    `position` is the stored order key (see db.py's setlist_scores) - members
    arrive already sorted by it, so a client renders them in list order without
    sorting, and it is exposed rather than left implicit so the order is a fact
    of the API and not of one client's rendering. `score` is the same ScoreOut
    every library and trash view returns; a member in the trash carries its
    `deleted_at`, which is how the setlist marks it deleted rather than
    linking to something that is not in the library."""

    position: int
    score: ScoreOut


class SetlistDetailOut(BaseModel):
    """One setlist with its ordered members - the shape GET /api/setlists/{id}
    returns, and the shape every mutation that changes membership or order
    (add, remove, reorder) echoes back so a client never has to re-fetch to
    learn the new state."""

    id: int
    owner: str
    name: str
    created_at: str
    updated_at: str
    scores: list[SetlistMemberOut]


class SetlistDeleteOut(BaseModel):
    """DELETE /api/setlists/{id}: the setlist is gone; the scores are not.

    `scores_untouched` is counted before the delete so a caller can say how
    many scores were in the setlist and every one of them - with its file,
    practice history, tags and transcription - is still exactly where it was.
    Deleting a setlist reaches nothing but the membership rows (see db.py's
    setlist_scores note on why setlist_id is ON DELETE CASCADE and what that
    does and does not remove)."""

    deleted: int
    scores_untouched: int
