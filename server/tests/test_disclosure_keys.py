"""Keeps web/tests/disclosure-keys.json honest against api._BAR_KEYS -
issue #155's fourth mirror guard.

The structural-form/inference disclosure counters (repeats_unread,
nav_marks_unresolved, coincident_unsplit_pairs, ...) are named in FOUR
places that cannot import one another and so cannot be kept in sync by the
type checker or the import graph: tabextract.py's ExtractionResult fields,
this module's own _BAR_KEYS tuple, api_models.py's TranscriptionOut fields
(guarded against _BAR_KEYS by
test_transcription_model_stays_in_sync_with_api_pys_bar_key_tuples in
test_api_docs.py), and web/src/lib/disclosures.js's DISCLOSURE_ROWS - the
interface config that decides what a reader actually sees.

Nothing checked that fourth copy at all: a fake counter added to _BAR_KEYS,
or a row quietly dropped from DISCLOSURE_ROWS, passed every test that
existed before this one. web/tests/unit/disclosures.spec.js now checks
DISCLOSURE_ROWS against web/tests/disclosure-keys.json in both directions -
but a browser test cannot import api.py, so nothing on that side can check
the VENDORED file itself against the real source of truth. This is that
check, on the only side that can make it.
"""

import json
from pathlib import Path

from fermata import api

_VENDORED_PATH = (
    Path(__file__).resolve().parents[2] / "web" / "tests" / "disclosure-keys.json"
)

# The Rule 8 conformance figures inside _BAR_KEYS that are NOT structural
# disclosures - already shown elsewhere (ScoreCompare's bar-count headline,
# and the warning prose the *_bars lists feed), not through the disclosures
# panel issue #155 built. Every other key in _BAR_KEYS is a structural
# disclosure and belongs in the vendored file.
_RULE8_CONFORMANCE_KEYS = {
    "bars_overfull", "bars_short", "bars_defective", "bars_measured",
    "bars_padded", "bars_unread",
}


def test_vendored_disclosure_keys_stay_in_sync_with_api_pys_bar_keys():
    """A key added to (or removed from) api._BAR_KEYS that is not one of the
    Rule 8 conformance keys above must be added to (or removed from)
    web/tests/disclosure-keys.json in the same change, or a disclosure the
    decoder counts can silently stop being guarded on the frontend - and, by
    extension, stop being rendered at all when it's removed from
    DISCLOSURE_ROWS - without any test, anywhere, going red."""
    expected = set(api._BAR_KEYS) - _RULE8_CONFORMANCE_KEYS
    vendored = set(json.loads(_VENDORED_PATH.read_text(encoding="utf-8")))
    missing = expected - vendored
    extra = vendored - expected
    assert not missing, (
        f"{_VENDORED_PATH} is missing disclosure key(s) that api._BAR_KEYS "
        f"now carries: {sorted(missing)}"
    )
    assert not extra, (
        f"{_VENDORED_PATH} has stale key(s) no longer in api._BAR_KEYS (or "
        f"reclassified as Rule 8 conformance above): {sorted(extra)}"
    )
