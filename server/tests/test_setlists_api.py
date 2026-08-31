"""Setlists (issue #6), endpoint by endpoint, with literal-value assertions.

The setlist surface is the whole of what a setlist IS - a documented set of
endpoints over the setlists and setlist_scores tables, not client-side state -
so these tests pin what each endpoint actually does to a literal value: the
order after a reorder is asserted as the exact list of ids, not "some order";
the counts a delete reports are the exact numbers; the state of a member whose
score is trashed is asserted, not assumed.

Each invariant from the issue has a test whose assertion a matching mutation
would turn red:

  - order is explicit and stored -> break reorder's position write and
    test_reorder_sets_the_exact_order goes red.
  - removing a score from a setlist does not delete the score ->
    test_removing_a_score_leaves_the_score_and_its_history.
  - deleting a setlist does not touch its scores ->
    test_deleting_a_setlist_leaves_every_score.
  - a score can be in multiple setlists ->
    test_a_score_can_be_in_several_setlists.
  - a trashed score stays in its setlists, marked, never a broken link ->
    test_a_trashed_score_stays_in_the_setlist_marked and
    test_a_purged_score_leaves_the_setlist.

The client is the router alone against a throwaway database, the same fixture
pattern test_instruments_api.py uses.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fermata import api, db


@pytest.fixture
def client(app_env):
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


@pytest.fixture
def score(insert_score):
    """A factory for scores in the throwaway library, each a distinct row."""
    conn = db.connect()

    def _make(name: str, title: str | None = None) -> int:
        return insert_score(conn, name, title=title or name)

    return _make


def _ids(detail: dict) -> list[int]:
    """The member score ids of a setlist detail response, in returned order."""
    return [m["score"]["id"] for m in detail["scores"]]


def _positions(detail: dict) -> list[int]:
    return [m["position"] for m in detail["scores"]]


# ---------------------------------------------------------------------------
# Create, name, list, get
# ---------------------------------------------------------------------------


def test_create_names_a_setlist_and_starts_it_empty(client):
    resp = client.post("/api/setlists", json={"name": "Friday gig"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Friday gig"
    assert body["score_count"] == 0
    assert body["owner"] == "local"
    assert isinstance(body["id"], int)

    detail = client.get(f"/api/setlists/{body['id']}").json()
    assert detail["scores"] == []


def test_create_cleans_and_requires_a_name(client):
    # Whitespace collapsed and trimmed.
    body = client.post("/api/setlists", json={"name": "  Lesson   plan \n"}).json()
    assert body["name"] == "Lesson plan"
    # A name that is only whitespace is refused, not stored blank.
    resp = client.post("/api/setlists", json={"name": "   "})
    assert resp.status_code == 422


def test_list_returns_setlists_newest_first_with_counts(client, score):
    a = client.post("/api/setlists", json={"name": "First"}).json()
    b = client.post("/api/setlists", json={"name": "Second"}).json()
    s = score("piece.pdf")
    client.post(f"/api/setlists/{b['id']}/scores", json={"score_id": s})

    listed = client.get("/api/setlists").json()
    assert [x["id"] for x in listed] == [b["id"], a["id"]]  # newest first
    by_id = {x["id"]: x for x in listed}
    assert by_id[b["id"]]["score_count"] == 1
    assert by_id[a["id"]]["score_count"] == 0


def test_get_and_delete_unknown_setlist_are_404(client):
    assert client.get("/api/setlists/999999").status_code == 404
    assert client.delete("/api/setlists/999999").status_code == 404
    assert client.patch("/api/setlists/999999", json={"name": "x"}).status_code == 404


def test_rename_changes_the_name_and_keeps_the_scores(client, score):
    setlist = client.post("/api/setlists", json={"name": "Old"}).json()
    s = score("piece.pdf")
    client.post(f"/api/setlists/{setlist['id']}/scores", json={"score_id": s})

    renamed = client.patch(f"/api/setlists/{setlist['id']}", json={"name": "New"})
    assert renamed.status_code == 200, renamed.text
    body = renamed.json()
    assert body["name"] == "New"
    assert _ids(body) == [s]


# ---------------------------------------------------------------------------
# Add / remove
# ---------------------------------------------------------------------------


def test_add_appends_in_order(client, score):
    setlist = client.post("/api/setlists", json={"name": "Set"}).json()
    first = score("a.pdf")
    second = score("b.pdf")
    third = score("c.pdf")

    client.post(f"/api/setlists/{setlist['id']}/scores", json={"score_id": first})
    client.post(f"/api/setlists/{setlist['id']}/scores", json={"score_id": second})
    detail = client.post(
        f"/api/setlists/{setlist['id']}/scores", json={"score_id": third}
    ).json()

    assert _ids(detail) == [first, second, third]
    assert _positions(detail) == [1, 2, 3]


def test_a_member_carries_its_whole_score_and_practice_total(client, score):
    setlist = client.post("/api/setlists", json={"name": "Set"}).json()
    s = score("piece.pdf", title="A Piece")
    client.post(f"/api/scores/{s}/practice", json={"seconds": 600})

    detail = client.post(
        f"/api/setlists/{setlist['id']}/scores", json={"score_id": s}
    ).json()
    member = detail["scores"][0]
    assert member["score"]["title"] == "A Piece"
    # The one-source-of-truth rule (#32): the score's own practice total comes
    # through on the member, not recomputed by any client.
    assert member["score"]["practice_seconds"] == 600


def test_add_rejects_a_duplicate(client, score):
    setlist = client.post("/api/setlists", json={"name": "Set"}).json()
    s = score("piece.pdf")
    client.post(f"/api/setlists/{setlist['id']}/scores", json={"score_id": s})
    resp = client.post(f"/api/setlists/{setlist['id']}/scores", json={"score_id": s})
    assert resp.status_code == 409


def test_add_rejects_a_missing_score_id(client):
    setlist = client.post("/api/setlists", json={"name": "Set"}).json()
    resp = client.post(f"/api/setlists/{setlist['id']}/scores", json={"score_id": 424242})
    assert resp.status_code == 404


def test_remove_takes_a_score_out_and_leaves_the_others(client, score):
    setlist = client.post("/api/setlists", json={"name": "Set"}).json()
    a, b, c = score("a.pdf"), score("b.pdf"), score("c.pdf")
    for s in (a, b, c):
        client.post(f"/api/setlists/{setlist['id']}/scores", json={"score_id": s})

    detail = client.delete(f"/api/setlists/{setlist['id']}/scores/{b}").json()
    assert _ids(detail) == [a, c]


def test_remove_of_a_non_member_is_404(client, score):
    setlist = client.post("/api/setlists", json={"name": "Set"}).json()
    a = score("a.pdf")
    b = score("b.pdf")
    client.post(f"/api/setlists/{setlist['id']}/scores", json={"score_id": a})
    resp = client.delete(f"/api/setlists/{setlist['id']}/scores/{b}")
    assert resp.status_code == 404


def test_removing_a_score_leaves_the_score_and_its_history(client, score):
    """Removing a score from a setlist must not delete the score. Break this by
    making remove_setlist_score also delete the score row and this goes red."""
    setlist = client.post("/api/setlists", json={"name": "Set"}).json()
    s = score("piece.pdf")
    client.post(f"/api/scores/{s}/practice", json={"seconds": 300})
    client.post(f"/api/setlists/{setlist['id']}/scores", json={"score_id": s})

    client.delete(f"/api/setlists/{setlist['id']}/scores/{s}")

    still_there = client.get(f"/api/scores/{s}")
    assert still_there.status_code == 200
    assert still_there.json()["practice_seconds"] == 300


# ---------------------------------------------------------------------------
# Reorder
# ---------------------------------------------------------------------------


def test_reorder_sets_the_exact_order(client, score):
    """The headline invariant: order is a stored fact, reorder writes it. Break
    reorder's position write (the UPDATE ... SET position) and this goes red."""
    setlist = client.post("/api/setlists", json={"name": "Set"}).json()
    a, b, c = score("a.pdf"), score("b.pdf"), score("c.pdf")
    for s in (a, b, c):
        client.post(f"/api/setlists/{setlist['id']}/scores", json={"score_id": s})

    reordered = client.put(
        f"/api/setlists/{setlist['id']}/order", json={"score_ids": [c, a, b]}
    )
    assert reordered.status_code == 200, reordered.text
    body = reordered.json()
    assert _ids(body) == [c, a, b]
    assert _positions(body) == [1, 2, 3]

    # And it is durable, not just echoed: a fresh GET reads the same order.
    assert _ids(client.get(f"/api/setlists/{setlist['id']}").json()) == [c, a, b]


def test_reorder_rejects_a_subset(client, score):
    setlist = client.post("/api/setlists", json={"name": "Set"}).json()
    a, b = score("a.pdf"), score("b.pdf")
    for s in (a, b):
        client.post(f"/api/setlists/{setlist['id']}/scores", json={"score_id": s})
    resp = client.put(f"/api/setlists/{setlist['id']}/order", json={"score_ids": [a]})
    assert resp.status_code == 422
    # Nothing written: the order is unchanged.
    assert _ids(client.get(f"/api/setlists/{setlist['id']}").json()) == [a, b]


def test_reorder_rejects_a_foreign_score(client, score):
    setlist = client.post("/api/setlists", json={"name": "Set"}).json()
    a, b = score("a.pdf"), score("b.pdf")
    outsider = score("outsider.pdf")
    for s in (a, b):
        client.post(f"/api/setlists/{setlist['id']}/scores", json={"score_id": s})
    resp = client.put(
        f"/api/setlists/{setlist['id']}/order", json={"score_ids": [a, outsider]}
    )
    assert resp.status_code == 422


def test_reorder_rejects_a_duplicate(client, score):
    setlist = client.post("/api/setlists", json={"name": "Set"}).json()
    a, b = score("a.pdf"), score("b.pdf")
    for s in (a, b):
        client.post(f"/api/setlists/{setlist['id']}/scores", json={"score_id": s})
    resp = client.put(
        f"/api/setlists/{setlist['id']}/order", json={"score_ids": [a, a]}
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Delete a setlist
# ---------------------------------------------------------------------------


def test_delete_reports_scores_untouched(client, score):
    setlist = client.post("/api/setlists", json={"name": "Set"}).json()
    a, b = score("a.pdf"), score("b.pdf")
    for s in (a, b):
        client.post(f"/api/setlists/{setlist['id']}/scores", json={"score_id": s})

    resp = client.delete(f"/api/setlists/{setlist['id']}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"deleted": setlist["id"], "scores_untouched": 2}
    assert client.get(f"/api/setlists/{setlist['id']}").status_code == 404


def test_deleting_a_setlist_leaves_every_score(client, score):
    """Deleting a setlist must not touch its scores or their history. Make
    delete_setlist cascade into scores and this goes red."""
    setlist = client.post("/api/setlists", json={"name": "Set"}).json()
    a, b = score("a.pdf"), score("b.pdf")
    client.post(f"/api/scores/{a}/practice", json={"seconds": 120})
    for s in (a, b):
        client.post(f"/api/setlists/{setlist['id']}/scores", json={"score_id": s})

    client.delete(f"/api/setlists/{setlist['id']}")

    kept = client.get(f"/api/scores/{a}")
    assert kept.status_code == 200, "the setlist delete removed a score it must not touch"
    assert kept.json()["practice_seconds"] == 120
    assert client.get(f"/api/scores/{b}").status_code == 200
    # The membership rows are gone with the setlist, and nothing else is.
    conn = db.connect()
    assert conn.execute("SELECT COUNT(*) AS n FROM setlist_scores").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM scores").fetchone()["n"] == 2


# ---------------------------------------------------------------------------
# A score in several setlists
# ---------------------------------------------------------------------------


def test_a_score_can_be_in_several_setlists(client, score):
    s = score("shared.pdf")
    one = client.post("/api/setlists", json={"name": "One"}).json()
    two = client.post("/api/setlists", json={"name": "Two"}).json()
    client.post(f"/api/setlists/{one['id']}/scores", json={"score_id": s})
    client.post(f"/api/setlists/{two['id']}/scores", json={"score_id": s})

    assert _ids(client.get(f"/api/setlists/{one['id']}").json()) == [s]
    assert _ids(client.get(f"/api/setlists/{two['id']}").json()) == [s]

    # Removing it from one leaves it in the other.
    client.delete(f"/api/setlists/{one['id']}/scores/{s}")
    assert _ids(client.get(f"/api/setlists/{one['id']}").json()) == []
    assert _ids(client.get(f"/api/setlists/{two['id']}").json()) == [s]


# ---------------------------------------------------------------------------
# The deleted-score-member matrix (#56)
# ---------------------------------------------------------------------------


def test_a_trashed_score_stays_in_the_setlist_marked(client, score):
    """A member whose score is soft-deleted (in the trash) is still listed, and
    carries its deleted_at so a client marks it rather than showing a broken
    link. It is not silently dropped."""
    setlist = client.post("/api/setlists", json={"name": "Set"}).json()
    a, b = score("a.pdf"), score("b.pdf")
    for s in (a, b):
        client.post(f"/api/setlists/{setlist['id']}/scores", json={"score_id": s})

    # Soft-delete a directly at the row level (delete_score needs a real file
    # on disk / library machinery; the setlist behaviour is about the row's
    # deleted_at, which is what the trash sets).
    conn = db.connect()
    conn.execute(
        "UPDATE scores SET deleted_at = datetime('now'), deleted_from = path WHERE id = ?",
        (a,),
    )
    conn.commit()

    detail = client.get(f"/api/setlists/{setlist['id']}").json()
    assert _ids(detail) == [a, b]  # still present, still in order
    trashed = detail["scores"][0]["score"]
    assert trashed["id"] == a
    assert trashed["deleted_at"] is not None


def test_a_trashed_score_cannot_be_added(client, score):
    setlist = client.post("/api/setlists", json={"name": "Set"}).json()
    s = score("piece.pdf")
    conn = db.connect()
    conn.execute(
        "UPDATE scores SET deleted_at = datetime('now'), deleted_from = path WHERE id = ?",
        (s,),
    )
    conn.commit()
    resp = client.post(f"/api/setlists/{setlist['id']}/scores", json={"score_id": s})
    assert resp.status_code == 409


def test_a_purged_score_leaves_the_setlist(client, score):
    """A score PURGED (its row really deleted, #56's second step) leaves the
    setlist on its own, because the membership row cascades away with it - a
    purged score is gone for good, and a membership row naming nothing is
    nothing. Trashed is marked; purged is gone."""
    setlist = client.post("/api/setlists", json={"name": "Set"}).json()
    a, b = score("a.pdf"), score("b.pdf")
    for s in (a, b):
        client.post(f"/api/setlists/{setlist['id']}/scores", json={"score_id": s})

    # A real purge is DELETE FROM scores with foreign keys on - db.connect()
    # sets PRAGMA foreign_keys=ON, so the cascade fires exactly as it would
    # through purge_score.
    conn = db.connect()
    conn.execute("DELETE FROM scores WHERE id = ?", (a,))
    conn.commit()

    detail = client.get(f"/api/setlists/{setlist['id']}").json()
    assert _ids(detail) == [b]


def test_deleting_a_score_that_is_in_a_setlist_keeps_it_marked_through_the_api(
    client, score, monkeypatch, tmp_path
):
    """End to end through the real DELETE /api/scores/{id} (soft delete, #56):
    a score in a setlist is trashed, and the setlist still lists it marked.
    Uses a real library file because delete_score moves the file to the trash
    folder - the same setup test_api_docs.py's library-management test uses."""
    from fermata import config, scanner

    root = config.LIBRARY_DIR
    monkeypatch.setattr(api, "LIBRARY_DIR", root)
    monkeypatch.setattr(scanner, "LIBRARY_DIR", root)
    (root / "Inbox").mkdir(parents=True, exist_ok=True)
    score_file = root / "Inbox" / "Study.pdf"
    score_file.write_bytes(b"a score file")
    stat = score_file.stat()

    conn = db.connect()
    score_id = conn.execute(
        """INSERT INTO scores(title, collection, path, file_type, hash, size, mtime)
           VALUES ('Study', 'Inbox', 'Inbox/Study.pdf', 'pdf', ?, ?, ?)""",
        (scanner.hash_file(score_file), stat.st_size, stat.st_mtime),
    ).lastrowid
    conn.commit()

    setlist = client.post("/api/setlists", json={"name": "Set"}).json()
    client.post(f"/api/setlists/{setlist['id']}/scores", json={"score_id": score_id})

    deleted = client.delete(f"/api/scores/{score_id}")
    assert deleted.status_code == 200, deleted.text

    detail = client.get(f"/api/setlists/{setlist['id']}").json()
    assert _ids(detail) == [score_id]
    assert detail["scores"][0]["score"]["deleted_at"] is not None
