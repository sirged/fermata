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
    score_id (or, for a session with none, `null`)."""

    score_title: str | None


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

    # _BAR_AMOUNT_KEYS
    inferred_rest_quarters: float | None

    # _PROVENANCE_KEYS
    time_signature: list[int] | None
    time_signature_source: str | None
    key_fifths: int | None
    key_signature_source: str | None
    tuning: list[str] | None
    tuning_label: str | None
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
