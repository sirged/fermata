<script>
  import { untrack } from "svelte";

  import { api } from "./api.js";
  import { keySignatureLabel } from "./provenance.js";

  let scores = $state([]);
  let collections = $state([]);
  let tags = $state([]);
  let search = $state("");
  let collection = $state("");
  let kind = $state("");
  let tag = $state("");
  let favorite = $state(false);
  let practiced = $state("");
  let transcribed = $state("");
  // #8: filters on the two fields worth narrowing a grid by. `key`/`difficulty`
  // travel as STRINGS, the same way every other select on this page does (kind,
  // transcribed, practiced) - "" means unset, and every real value (including
  // "0", the common "no sharps or flats" key) is a non-empty string, so the
  // truthy check api.scores() already does to drop unset filters never mistakes
  // a real, falsy-looking NUMBER for one. Tempo has no filter control here: #8's
  // Done-when asks for key and difficulty in the interface, and a range needs a
  // pair of inputs a single "beside the existing filter row" select cannot be.
  let key = $state("");
  let difficulty = $state("");
  let scan = $state(null);
  let loading = $state(true);
  let uploadInput;
  let showDuplicates = $state(false);
  let duplicates = $state([]);

  // --- Managing the library (issue #56) --------------------------------------
  //
  // This is the only part of Fermata that writes to somebody's own files, and
  // the interface is built around that rather than around convenience:
  //
  //   ORGANISE IS A MODE, not something a stray tap can start. Outside it a
  //   card opens a score, which is what a card has always done; inside it a
  //   card selects, and the whole grid says so. Nothing here is a drag - this
  //   is used on a music stand, often with a tablet, and a drag that starts by
  //   accident on a touchscreen would be a move nobody asked for.
  //
  //   A MOVE IS PREVIEWED BEFORE IT HAPPENS. The dialog asks the server for
  //   the plan (its dry run) and shows every line of it - from and to - before
  //   the button that applies it is worth pressing. The server refuses a plan
  //   with a blocked line, and so does this.
  //
  //   DELETING SAYS WHAT IT KEEPS. The confirmation is not "are you sure": it
  //   says the file goes to the library's trash and the practice history stays,
  //   and afterwards it says how many sessions, tags and transcriptions are
  //   still attached, counted by the server.
  //
  //   DESTROYING IS SOMEWHERE ELSE ENTIRELY. The only control that really
  //   deletes lives in the trash view, needs a second press, and lists what it
  //   will destroy first.
  let organising = $state(false);
  let selected = $state([]);
  let notice = $state("");
  let busy = $state(false);

  let folders = $state([]);
  let moveOpen = $state(false);
  let moveError = $state("");
  let targetFolder = $state(null);
  let newFolderName = $state("");
  let movePlan = $state(null);
  let renamingFolder = $state(false);
  let renameTo = $state("");
  let renamePlan = $state(null);

  let deleteOpen = $state(false);
  let deleteError = $state("");

  let showTrash = $state(false);
  let trashItems = $state([]);
  let destroyConfirmId = $state(null);

  const selectedScores = $derived(scores.filter((s) => selected.includes(s.id)));
  const blockedLines = $derived((movePlan ?? []).filter((line) => line.status === "blocked"));

  function toggleSelected(score, ev) {
    if (!organising) return;
    ev.preventDefault();
    ev.stopPropagation();
    selected = selected.includes(score.id)
      ? selected.filter((id) => id !== score.id)
      : [...selected, score.id];
  }

  function leaveOrganising() {
    organising = false;
    selected = [];
    moveOpen = false;
    deleteOpen = false;
    transcribeOpen = false;
  }

  async function loadFolders() {
    folders = await api.folders();
  }

  async function openMove() {
    moveError = "";
    movePlan = null;
    renamingFolder = false;
    renamePlan = null;
    targetFolder = null;
    newFolderName = "";
    await loadFolders();
    moveOpen = true;
  }

  // The preview IS the dry run - the same endpoint, the same plan shape, asked
  // for with dry_run true. Showing a preview computed in the browser would be
  // showing a different operation's plan and calling it this one's.
  async function previewMove(folder) {
    targetFolder = folder;
    moveError = "";
    movePlan = null;
    try {
      const result = await api.moveScores(selected, folder, true);
      movePlan = result.moves;
    } catch (err) {
      moveError = err?.message ?? "Fermata could not work out what that would do.";
    }
  }

  async function applyMove() {
    if (targetFolder === null || blockedLines.length) return;
    busy = true;
    moveError = "";
    try {
      const result = await api.moveScores(selected, targetFolder, false);
      notice = `Moved ${result.moved} score${result.moved === 1 ? "" : "s"} to ${
        targetFolder || "the library root"
      }.`;
      moveOpen = false;
      leaveOrganising();
      await refresh();
    } catch (err) {
      moveError = err?.message ?? "Fermata could not move those.";
    } finally {
      busy = false;
    }
  }

  async function createFolder() {
    const name = newFolderName.trim();
    if (!name) return;
    moveError = "";
    try {
      const path = targetFolder ? `${targetFolder}/${name}` : name;
      const created = await api.createFolder(path);
      newFolderName = "";
      await loadFolders();
      await previewMove(created.created);
    } catch (err) {
      moveError = err?.message ?? "Fermata could not create that folder.";
    }
  }

  async function previewFolderRename() {
    renamePlan = null;
    moveError = "";
    try {
      const result = await api.renameFolder(targetFolder, renameTo.trim(), true);
      renamePlan = result.moves;
    } catch (err) {
      moveError = err?.message ?? "Fermata could not work out what that would do.";
    }
  }

  async function applyFolderRename() {
    busy = true;
    moveError = "";
    try {
      const from = targetFolder;
      const result = await api.renameFolder(from, renameTo.trim(), false);
      notice = `Renamed ${from} to ${result.to_path}; ${result.moved} score${
        result.moved === 1 ? "" : "s"
      } moved with it.`;
      moveOpen = false;
      leaveOrganising();
      await refresh();
    } catch (err) {
      moveError = err?.message ?? "Fermata could not rename that folder.";
    } finally {
      busy = false;
    }
  }

  async function applyDelete() {
    busy = true;
    deleteError = "";
    try {
      let sessions = 0;
      let transcriptions = 0;
      for (const id of selected) {
        const result = await api.deleteScore(id);
        sessions += result.practice_sessions_kept;
        transcriptions += result.transcriptions_kept;
      }
      const count = selected.length;
      notice =
        `Moved ${count} score${count === 1 ? "" : "s"} to the trash. ` +
        `${sessions} practice session${sessions === 1 ? "" : "s"} and ${transcriptions} ` +
        `transcription${transcriptions === 1 ? "" : "s"} are still attached - nothing was ` +
        `destroyed. They are in Trash until you say otherwise.`;
      deleteOpen = false;
      leaveOrganising();
      await refresh();
    } catch (err) {
      deleteError = err?.message ?? "Fermata could not delete those.";
    } finally {
      busy = false;
    }
  }

  async function loadTrash() {
    trashItems = await api.trash();
  }

  $effect(() => {
    if (!showTrash) return;
    loadTrash();
  });

  async function restore(score) {
    busy = true;
    try {
      const result = await api.restoreScore(score.id);
      notice =
        result.restored_to === result.restored_from
          ? `${score.title} is back at ${result.restored_to}.`
          : `${score.title} is back, at ${result.restored_to} - something else had taken ` +
            `${result.restored_from}, and Fermata did not overwrite it.`;
      await loadTrash();
      await refresh();
    } catch (err) {
      notice = err?.message ?? "Fermata could not put that back.";
    } finally {
      busy = false;
    }
  }

  async function destroy(score) {
    busy = true;
    try {
      const result = await api.destroyScore(score.id);
      notice =
        `${result.title} is gone for good, with ${result.tags_destroyed} tag${
          result.tags_destroyed === 1 ? "" : "s"
        } and ${result.transcriptions_destroyed} transcription${
          result.transcriptions_destroyed === 1 ? "" : "s"
        }. ${result.practice_sessions_kept} practice session${
          result.practice_sessions_kept === 1 ? "" : "s"
        } stayed in your history.`;
      destroyConfirmId = null;
      await loadTrash();
      await refresh();
    } catch (err) {
      notice = err?.message ?? "Fermata could not destroy that.";
    } finally {
      busy = false;
    }
  }
  // Which build is actually running (issue #119) - fetched once, quietly, so
  // a stale deployment is diagnosable at a glance instead of by guessing from
  // which features seem to be missing. null until it arrives, which renders
  // as nothing rather than a placeholder worth reading twice.
  let buildInfo = $state(null);
  const buildLabel = $derived(
    buildInfo
      ? buildInfo.commit === "dev"
        ? `v${buildInfo.version} (dev)`
        : `v${buildInfo.version} (${buildInfo.commit}, ${buildInfo.built})`
      : "",
  );

  const KINDS = [
    ["", "All"],
    ["notation", "Notation"],
    ["tab", "Tab"],
    ["both", "Notation + Tab"],
    ["unknown", "Unsorted"],
  ];

  // A freshly scanned library transcribes itself (#190), and this is how a
  // person sees which of it that reached: narrow to "Transcribed" and the
  // grid is exactly the scores a bulk pass could read; narrow to "Not
  // transcribed" and it is exactly the complement - whatever a scan judged
  // non-extractable, plus anything a manual pass has not reached yet.
  const TRANSCRIBED = [
    ["", "Any transcription"],
    ["yes", "Transcribed"],
    ["no", "Not transcribed"],
  ];

  // #8: the server's own closed ranges (api.MIN_KEY_FIFTHS..MAX_KEY_FIFTHS,
  // api.MIN_DIFFICULTY..MAX_DIFFICULTY) mirrored the same way KINDS above
  // mirrors VALID_KINDS - nothing here asks the server what is valid, so a
  // number offered here that the server would refuse is a drift bug, not a
  // possibility this file defends against at runtime.
  const KEY_FILTERS = [
    ["", "Any key"],
    ...Array.from({ length: 15 }, (_, i) => i - 7).map((fifths) => [
      String(fifths),
      keySignatureLabel(fifths),
    ]),
  ];
  const DIFFICULTY_FILTERS = [
    ["", "Any difficulty"],
    ...[1, 2, 3, 4, 5].map((n) => [String(n), "★".repeat(n) + "☆".repeat(5 - n)]),
  ];

  async function refresh() {
    loading = true;
    try {
      [scores, collections, tags] = await Promise.all([
        api.scores({
          search,
          collection,
          kind,
          tag,
          favorite,
          practiced,
          transcribed,
          key,
          difficulty,
        }),
        api.collections(),
        api.tags(),
      ]);
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    // re-query whenever a filter changes
    void search, collection, kind, tag, favorite, practiced, transcribed, key, difficulty;
    const t = setTimeout(refresh, 150);
    return () => clearTimeout(t);
  });

  $effect(() => {
    if (!showDuplicates) return;
    api.duplicates().then((d) => (duplicates = d));
  });

  $effect(() => {
    api.version().then((v) => (buildInfo = v));
  });

  $effect(() => {
    let timer;
    async function poll() {
      scan = await api.scanStatus();
      if (scan.scanning) {
        timer = setTimeout(poll, 1500);
      } else {
        refresh();
        // A scan this page is tracking just finished - the single instant
        // most likely to have just started an automatic transcription pass
        // (#190's own hook). Check right away rather than wait out
        // whatever is left of the background poll's own idle interval -
        // see nudgeBackgroundBatchPoll's own comment.
        nudgeBackgroundBatchPoll();
      }
    }
    api.scanStatus().then((s) => {
      scan = s;
      if (s.scanning) poll();
    });
    return () => clearTimeout(timer);
  });

  // For a bulk pass THIS page never started (#190 review, F3, three
  // passes) - a scan's own automatic one, or one started from another tab
  // or client. Polled independently of both the scan poll above and the
  // transcribe dialog's own poll (see `backgroundBatch`'s own comment).
  //
  // RATE: 3s while a pass is confirmed running, 15s while idle. Idle
  // forever at 3s (an earlier version of this) was roughly 1200
  // requests/hour/tab with nothing running at all; backing off to 15s
  // while idle - measured over 60s with nothing running: 4 requests, once
  // every ~15s, as designed - cuts that by roughly 80% while a pass is
  // NOT running, which is the overwhelmingly common case. Backing off
  // rather than stopping outright (which the brief also allowed) is what
  // still catches a pass THIS PAGE neither started nor is watching, from
  // another tab or another client, without waiting for this page's own
  // next reload to notice it.
  //
  // NUDGED - an immediate check, replacing whatever is left of the idle
  // wait - the moment a scan this page is tracking finishes (both the
  // mount-time scan poll above and pollUntilDone below call
  // nudgeBackgroundBatchPoll() in their own "not scanning any more"
  // branch). That is what keeps the 15s idle floor from being a real gap
  // for the single most common case this feature is FOR - clicking "Scan
  // library" and watching: the automatic pass this page's own scan may
  // have started is checked within one request round trip of the scan
  // itself finishing, not after up to 15s of nothing. A pass from
  // elsewhere still relies on the 15s idle floor - there is no local
  // signal for those to nudge on.
  //
  // RESILIENT to one failed request (#190 review, F3-1). api.js throws
  // ApiError on a non-2xx response and fetch itself rejects on a dropped
  // connection - a self-hosted server restarting with this page open is
  // the ordinary way to see one - so without the try/finally below, ONE
  // failed request ended this loop for the rest of the page's life
  // (measured: baseline 2 requests/9s; after one aborted response, 0
  // requests in the following 15s, plus an unhandled rejection). The
  // reschedule lives in `finally` specifically so it runs whether the
  // request above it succeeded or not. Logged and otherwise swallowed,
  // nothing louder: this poll is ambient awareness, not a request a
  // person is waiting on, and the ordinary scan poll and every other
  // request on this page already surface a real outage of their own.
  //
  // `transcribeOpen` is read through `untrack` rather than as an ordinary
  // reactive read, on purpose: reading it as a normal reactive value here
  // would make this effect's own synchronous setup one of
  // `transcribeOpen`'s dependents, so Svelte would tear this whole loop
  // down and restart it - one extra request - every time the dialog opens
  // or closes, exactly backwards from "skip a beat while it's open".
  // `untrack` reads the current value without subscribing to it, so
  // opening or closing the dialog no longer touches this loop at all; the
  // next already-scheduled tick simply sees the new value when it runs.
  let backgroundBatchTimer;
  let backgroundBatchCancelled = true;
  let wasBackgroundBatchRunning = false;

  async function pollBackgroundBatch() {
    clearTimeout(backgroundBatchTimer);
    let nextDelay = 15000;
    try {
      if (!untrack(() => transcribeOpen)) {
        const status = await api.transcribeBatchStatus();
        if (backgroundBatchCancelled) return;
        if (status.running) {
          backgroundBatch = status;
          wasBackgroundBatchRunning = true;
          nextDelay = 3000;
        } else {
          if (wasBackgroundBatchRunning) refresh();
          wasBackgroundBatchRunning = false;
          backgroundBatch = null;
        }
      }
    } catch (err) {
      console.error("background transcription status check did not complete", err);
    } finally {
      if (!backgroundBatchCancelled) {
        backgroundBatchTimer = setTimeout(pollBackgroundBatch, nextDelay);
      }
    }
  }

  function nudgeBackgroundBatchPoll() {
    if (backgroundBatchCancelled) return;
    clearTimeout(backgroundBatchTimer);
    backgroundBatchTimer = setTimeout(pollBackgroundBatch, 0);
  }

  $effect(() => {
    backgroundBatchCancelled = false;
    pollBackgroundBatch();
    return () => {
      backgroundBatchCancelled = true;
      clearTimeout(backgroundBatchTimer);
    };
  });

  async function triggerScan() {
    await api.scan();
    scan = { ...(scan ?? {}), scanning: true };
    pollUntilDone();
  }

  function pollUntilDone() {
    const poll = async () => {
      scan = await api.scanStatus();
      if (scan.scanning) {
        setTimeout(poll, 1500);
      } else {
        refresh();
        // This page's own click just finished scanning - nudge the
        // background-batch poll rather than leave it to its own idle
        // interval, since this is exactly the moment #190's own hook may
        // have started an automatic pass. See nudgeBackgroundBatchPoll's
        // own comment.
        nudgeBackgroundBatchPoll();
      }
    };
    setTimeout(poll, 800);
  }

  // --- Bulk transcription (issue #55) -----------------------------------
  //
  // The scan's own pattern, reused rather than reinvented: start a pass,
  // poll its status. Two ways in - select scores in Organise mode, or point
  // at a whole folder from the sidebar - both open the same dialog and share
  // the same progress/results view, because what happens after starting is
  // identical either way.
  //
  // EVERY SCORE IN THE RESPONSE IS SHOWN, never filtered down to only the
  // successes: the server's own promise is no silent skip, and a UI that
  // then hid the skipped and failed lines would throw that promise away one
  // layer up.
  let transcribeOpen = $state(false);
  let transcribeTarget = $state(null); // { kind: "ids", ids } | { kind: "collection", collection }
  let transcribeReconvert = $state(false);
  let transcribeError = $state("");
  let transcribeStatus = $state(null);
  let transcribePollTimer;
  // A pass this page did not start - a scan's own automatic one (#190), or
  // someone else's - running right now. Polled independently of the dialog
  // above, which only ever knows about a pass THIS page started: without
  // this, the one moment a click here would be refused (see
  // startTranscribeBatch's own `!result.started` branch) was invisible until
  // the click itself failed.
  let backgroundBatch = $state(null);

  const OUTCOME_LABEL = {
    already_transcribed: "already had one",
    non_extractable: "not extractable",
    errored: "error",
    transcribed: "transcribed",
  };

  function transcribeLineDetail(line) {
    if (line.outcome === "transcribed") {
      return line.bars_defective
        ? `${line.bars_defective} bar${line.bars_defective === 1 ? "" : "s"} do not add up`
        : "";
    }
    return line.reason ?? "";
  }

  function openTranscribeSelected() {
    transcribeTarget = { kind: "ids", ids: [...selected] };
    transcribeError = "";
    transcribeStatus = null;
    transcribeReconvert = false;
    transcribeOpen = true;
  }

  function openTranscribeCollection(name) {
    transcribeTarget = { kind: "collection", collection: name };
    transcribeError = "";
    transcribeStatus = null;
    transcribeReconvert = false;
    transcribeOpen = true;
  }

  function pollTranscribeBatch() {
    const poll = async () => {
      transcribeStatus = await api.transcribeBatchStatus();
      if (transcribeStatus.running) {
        transcribePollTimer = setTimeout(poll, 800);
      } else {
        refresh();
      }
    };
    transcribePollTimer = setTimeout(poll, 500);
  }

  async function startTranscribeBatch() {
    if (!transcribeTarget) return;
    busy = true;
    transcribeError = "";
    try {
      const opts = { reconvert: transcribeReconvert };
      if (transcribeTarget.kind === "collection") opts.collection = transcribeTarget.collection;
      const ids = transcribeTarget.kind === "ids" ? transcribeTarget.ids : null;
      const result = await api.transcribeBatch(ids, opts);
      if (!result.started) {
        // Refused - a pass is already running, and what came back is THAT
        // pass's own status (api.transcribeBatch's own shape: "the status
        // left behind by whichever pass - this one, or one already running -
        // is current"), not one for the selection just made. Adopting it
        // here would show somebody else's progress and results as though
        // they belonged to this selection, and close having silently
        // transcribed nothing this person chose - reachable with one click
        // right after an upload or a boot scan starts its own pass (#190).
        transcribeError =
          `a pass over ${result.total} score${result.total === 1 ? "" : "s"} is already ` +
          "running in the background - your selection was not started. Try again once it " +
          "finishes.";
        return;
      }
      transcribeStatus = result;
      pollTranscribeBatch();
    } catch (err) {
      transcribeError = err?.message ?? "Fermata could not start that.";
    } finally {
      busy = false;
    }
  }

  function closeTranscribe() {
    clearTimeout(transcribePollTimer);
    if (transcribeStatus && !transcribeStatus.running) {
      notice =
        `${transcribeStatus.transcribed} transcribed, ${transcribeStatus.already_transcribed} ` +
        `already had one, ${transcribeStatus.non_extractable} not extractable, ` +
        `${transcribeStatus.errored} errored.`;
    }
    transcribeOpen = false;
    if (organising) leaveOrganising();
  }

  // A refused scan is the one thing here that needs a person, so it is the one
  // thing that gets a button. Fermata will not mark scores missing when the
  // evidence looks like a mount problem rather than like somebody tidying up -
  // and without a way to say "I meant it", that refusal would repeat on every
  // scan for ever, because the same files are missing every time.
  let acknowledging = $state(false);
  let acknowledgeError = $state("");

  async function acknowledgeRemovals() {
    if (!scan?.acknowledge_token) return;
    acknowledging = true;
    acknowledgeError = "";
    try {
      await api.acknowledgeScan(scan.acknowledge_token);
      scan = { ...scan, scanning: true, refused: false };
      pollUntilDone();
    } catch (err) {
      // The usual cause is the library having changed again while this message
      // was on screen, which makes the token stale on purpose - saying so is
      // more use than a silent no-op.
      acknowledgeError =
        err?.message ?? "Fermata could not confirm that. Scan again and re-read the message.";
    } finally {
      acknowledging = false;
    }
  }

  async function onUpload(ev) {
    const files = [...ev.target.files];
    for (const f of files) await api.upload(f);
    ev.target.value = "";
    setTimeout(refresh, 1200);
  }

  async function toggleFavorite(score, ev) {
    ev.preventDefault();
    ev.stopPropagation();
    const updated = await api.patch(score.id, { favorite: !score.favorite });
    scores = scores.map((s) => (s.id === score.id ? updated : s));
  }

  const kindLabel = { notation: "notation", tab: "tab", both: "notation + tab", unknown: "" };

  function practicedAgo(lastPracticed) {
    if (!lastPracticed) return "";
    // A practice DAY (YYYY-MM-DD), which is the day in the practiser's own
    // time - not the UTC timestamp this used to be handed. That mattered
    // because the answer here is a count of calendar days: reading it off a
    // UTC instant put an evening's practice on the next day for anyone west of
    // Greenwich, so "practised today" became "practised 1d ago" at nine at
    // night. The slice also tolerates a timestamp, so an older server (or a
    // row read through some other path) still reads sensibly rather than
    // showing nothing.
    const [year, month, day] = String(lastPracticed).slice(0, 10).split("-").map(Number);
    if (!year || !month || !day) return "";
    const then = new Date(year, month - 1, day);
    if (!Number.isFinite(then.getTime())) return "";
    const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
    const days = Math.round((startOfDay(new Date()) - startOfDay(then)) / 86400000);
    if (days <= 0) return "practiced today";
    if (days === 1) return "practiced 1d ago";
    return `practiced ${days}d ago`;
  }
</script>

<div class="layout">
  <aside>
    <div class="brand">
      <svg viewBox="0 0 32 32" width="30" height="30" aria-hidden="true">
        <path d="M4 22 A12 12 0 0 1 28 22" fill="none" stroke="var(--brass)" stroke-width="3" stroke-linecap="round" />
        <circle cx="16" cy="22" r="3" fill="var(--brass)" />
      </svg>
      <h1>fermata</h1>
    </div>

    <nav>
      <button class="side-item" class:active={!collection && !favorite && !practiced && !showDuplicates && !showTrash} onclick={() => { collection = ""; favorite = false; practiced = ""; showDuplicates = false; showTrash = false; }}>
        All scores
      </button>
      <button class="side-item" class:active={favorite} onclick={() => { favorite = !favorite; showDuplicates = false; showTrash = false; }}>
        ★ Favorites
      </button>
      <button class="side-item" class:active={practiced === "recent"} onclick={() => { practiced = practiced === "recent" ? "" : "recent"; showDuplicates = false; showTrash = false; }}>
        ◷ Recently practiced
      </button>
      <button class="side-item" class:active={practiced === "neglected"} onclick={() => { practiced = practiced === "neglected" ? "" : "neglected"; showDuplicates = false; showTrash = false; }}>
        ⌛ Needs attention
      </button>
      <button class="side-item" class:active={showDuplicates} onclick={() => { showDuplicates = !showDuplicates; showTrash = false; }}>
        ⧉ Duplicates
      </button>
      <!-- Deleted scores are HERE and nowhere else. They are gone from the grid
           and from every count in this sidebar, which is what deleting has to
           mean, and they are one press from coming back - which is what makes
           deleting safe to offer at all (issue #56). -->
      <button class="side-item trash-link" class:active={showTrash} onclick={() => { showTrash = !showTrash; showDuplicates = false; leaveOrganising(); }}>
        🗑 Trash
      </button>

      <div class="side-label">Collections</div>
      {#each collections as c}
        <div class="side-row">
          <button
            class="side-item"
            class:active={collection === c.collection}
            onclick={() => { collection = collection === c.collection ? "" : c.collection; showDuplicates = false; showTrash = false; }}
          >
            {c.collection}
            <span class="count">{c.count}</span>
            {#if c.missing}
              <!-- Files this collection has on record that are not on disk. Shown
                   because a folder that has partly gone used to be counted as
                   though it were whole. -->
              <span class="count missing-count" title="{c.missing} file(s) not found in your library folder">
                {c.missing} missing
              </span>
            {/if}
          </button>
          <!-- The "point at a folder" half of issue #55 - the other half is
               selecting particular scores in Organise mode. Its own button
               rather than folded into the row's click, because the row's
               click already means "filter to this folder". -->
          <button
            class="side-transcribe"
            title="Transcribe every un-transcribed score in {c.collection}"
            onclick={() => openTranscribeCollection(c.collection)}
          >
            ♪
          </button>
        </div>
      {/each}

      {#if tags.length}
        <div class="side-label">Tags</div>
        <div class="tag-cloud">
          {#each tags as t}
            <button class="chip" class:active={tag === t.name} onclick={() => { tag = tag === t.name ? "" : t.name; showDuplicates = false; showTrash = false; }}>
              {t.name}
            </button>
          {/each}
        </div>
      {/if}
    </nav>

    <div class="side-actions">
      <button onclick={() => uploadInput.click()}>Upload</button>
      <input
        bind:this={uploadInput}
        type="file"
        accept=".pdf,.musicxml,.mxl,.gp,.gp3,.gp4,.gp5,.gpx"
        multiple
        hidden
        onchange={onUpload}
      />
      <button onclick={triggerScan} disabled={scan?.scanning}>
        {scan?.scanning ? `Scanning ${scan.processed}/${scan.total}…` : "Scan library"}
      </button>
      <a class="demo-link practice-link" href="#/practice">◴ Practice &amp; goals</a>
      <a class="demo-link setlists-link" href="#/setlists">☰ Setlists</a>
      <a class="demo-link" href="#/metronome">♩ Metronome</a>
      <a class="demo-link ear-training-link" href="#/ear-training">♪ Hear a note, name it</a>
      <a class="demo-link fretboard-link" href="#/fretboard">▤ Fret to note</a>
      <a class="demo-link chords-link" href="#/chords">♫ Chord flash cards</a>
      <a class="demo-link" href="#/demo">Notation/tab demo →</a>
      <a class="demo-link" href="#/settings">⚙ Settings</a>
    </div>

    {#if buildLabel}
      <!-- Quiet and always on screen - no hover, this app's primary form
           factor is a tablet with no pointer to hover with (issue #119). -->
      <div class="build-tag" title="Fermata build">{buildLabel}</div>
    {/if}
  </aside>

  <main>
    {#if scan?.refused}
      <!-- The scan declined to change anything because what it saw did not look
           like a description of this library. This used to be invisible: the
           status carried `refused` and a reason and nothing rendered either, so
           somebody with 296 of 297 files gone saw a healthy-looking scan, a full
           library, and no hint that anything was wrong. -->
      <div class="alert" role="alert">
        <div class="alert-head">Fermata did not update your library</div>
        <p class="alert-body">{scan.refused_reason}</p>
        {#if scan.unmatched_count}
          <details class="alert-paths">
            <summary>
              {scan.unmatched_count} file{scan.unmatched_count === 1 ? "" : "s"} not found
              {#if scan.unmatched_count > scan.unmatched_paths.length}
                (first {scan.unmatched_paths.length} shown)
              {/if}
            </summary>
            <ul>
              {#each scan.unmatched_paths as path}
                <li>{path}</li>
              {/each}
            </ul>
          </details>
        {/if}
        {#if scan.acknowledge_token}
          <div class="alert-actions">
            <button onclick={acknowledgeRemovals} disabled={acknowledging}>
              {acknowledging ? "Confirming…" : "Yes, I meant to do that"}
            </button>
            <button onclick={triggerScan} disabled={scan.scanning || acknowledging}>
              Scan again
            </button>
          </div>
          <p class="alert-note">
            Confirming never deletes anything. Files that have moved are matched back to
            their own score; anything Fermata cannot find is marked as missing and keeps
            its practice history, tags and transcriptions.
          </p>
        {/if}
        {#if acknowledgeError}
          <p class="alert-error">{acknowledgeError}</p>
        {/if}
      </div>
    {/if}

    {#if !scan?.scanning && scan?.restored}
      <!-- The other half of the missing-file story, and until now the half
           nobody was told. The scanner counts rows whose file turned up again
           AT THE PATH IT LEFT FROM - deliberately not a content-hash relink,
           which is a guess about identity - specifically so it can stand as
           evidence that a remount really did recover. It stood as evidence to
           nobody: the count was on /api/scan/status and nothing read it, so
           somebody who put a drive back saw flags quietly disappear with no
           statement that anything had been recovered (issue #103).

           Attributed to the LAST SCAN rather than stated as a bare number,
           because that is what it is - the counter resets when a scan starts. -->
      <p class="scan-note">
        Last scan: {scan.restored} score{scan.restored === 1 ? "" : "s"} found again
        {scan.restored === 1 ? "at the path it" : "at the paths they"} went missing from.
      </p>
    {/if}

    {#if !scan?.scanning && scan?.transcribe_batch_note}
      <!-- A freshly scanned library transcribes itself (#190) - what its
           chain's own bulk pass decided, said the same way the scan's other
           after-the-fact notes are: attributed to the LAST SCAN, because the
           note is replaced the next time a chain decides anything. Produced
           since the feature shipped but never rendered anywhere until this
           review (F3) - the one place a person could otherwise learn a
           background pass was declined was by noticing nothing got marked. -->
      <p class="scan-note">Last scan: {scan.transcribe_batch_note}.</p>
    {/if}

    {#if backgroundBatch && !transcribeOpen}
      <!-- Live, not after-the-fact: a bulk pass is running RIGHT NOW that
           this page did not start (#190 review, F3) - the scan's own
           automatic one, or one from another tab or client. Unobtrusive on
           purpose - this is ambient awareness, not the dialog's own detailed
           progress, which is why it steps aside while that dialog is open. -->
      <p class="scan-note">
        Transcribing {backgroundBatch.total} score{backgroundBatch.total === 1 ? "" : "s"} in
        the background…
      </p>
    {/if}

    {#if notice}
      <!-- What just happened to somebody's files, in their own terms and in
           full: which scores moved where, or what a deletion kept. Dismissible
           rather than timed - this is the receipt for a change to their
           library, and a receipt that disappears on its own while it is being
           read is not one. -->
      <p class="notice" role="status">
        {notice}
        <button class="notice-dismiss" onclick={() => (notice = "")}>Dismiss</button>
      </p>
    {/if}

    {#if showTrash}
      <header>
        <span class="result-count">{trashItems.length} in the trash</span>
      </header>
      <p class="trash-intro">
        These scores have been deleted. Their files are in a trash folder inside your library
        and their practice history, tags and transcriptions are still attached — putting one
        back leaves it exactly as it was.
      </p>
      {#if !trashItems.length}
        <p class="empty">The trash is empty.</p>
      {:else}
        <div class="trash-list">
          {#each trashItems as score (score.id)}
            <div class="trash-row">
              <div class="trash-what">
                <div class="trash-title">{score.title}</div>
                <div class="trash-path">was {score.deleted_from}</div>
                <div class="trash-kept">
                  {Math.round(score.practice_seconds / 60)} min practised{score.tags.length
                    ? `, ${score.tags.length} tag${score.tags.length === 1 ? "" : "s"}`
                    : ""}{score.has_transcription ? ", transcription kept" : ""}
                </div>
              </div>
              <div class="trash-actions">
                <button class="trash-restore" disabled={busy} onclick={() => restore(score)}>
                  Put it back
                </button>
                {#if destroyConfirmId === score.id}
                  <!-- The second press. It names what it destroys and what it
                       cannot: the file and the transcription go, the hours
                       stay in the practice history. -->
                  <button class="trash-destroy-confirm" disabled={busy} onclick={() => destroy(score)}>
                    Destroy the file{score.has_transcription ? " and its transcription" : ""} —
                    your practice history stays
                  </button>
                  <button class="trash-destroy-cancel" onclick={() => (destroyConfirmId = null)}>
                    Keep it
                  </button>
                {:else}
                  <button class="trash-destroy" onclick={() => (destroyConfirmId = score.id)}>
                    Delete permanently…
                  </button>
                {/if}
              </div>
            </div>
          {/each}
        </div>
      {/if}
    {:else if showDuplicates}
      <header>
        <span class="result-count">{duplicates.length} duplicate group{duplicates.length === 1 ? "" : "s"}</span>
      </header>

      {#if !duplicates.length}
        <p class="empty">No duplicates found — every score in your library is unique.</p>
      {:else}
        <div class="dupe-list">
          {#each duplicates as group (group.hash)}
            <div class="dupe-group">
              <div class="dupe-head">{group.count} copies — {group.scores[0]?.title ?? "Untitled"}</div>
              <div class="dupe-paths">
                {#each group.scores as s (s.id)}
                  <a class="dupe-path" href={"#/score/" + s.id}>{s.path}</a>
                {/each}
              </div>
            </div>
          {/each}
        </div>
      {/if}
    {:else}
      <header>
        <input class="search" type="search" placeholder="Search title, composer, source…" bind:value={search} />
        <select bind:value={kind}>
          {#each KINDS as [value, label]}
            <option {value}>{label}</option>
          {/each}
        </select>
        <select class="transcribed-filter" bind:value={transcribed}>
          {#each TRANSCRIBED as [value, label]}
            <option {value}>{label}</option>
          {/each}
        </select>
        <select class="key-filter" bind:value={key} title="Filter by key">
          {#each KEY_FILTERS as [value, label]}
            <option {value}>{label}</option>
          {/each}
        </select>
        <select class="difficulty-filter" bind:value={difficulty} title="Filter by difficulty">
          {#each DIFFICULTY_FILTERS as [value, label]}
            <option {value}>{label}</option>
          {/each}
        </select>
        <button
          class="organise-toggle"
          class:on={organising}
          onclick={() => (organising ? leaveOrganising() : (organising = true))}
        >
          {organising ? "Done organising" : "Organise"}
        </button>
        <span class="result-count">{scores.length} score{scores.length === 1 ? "" : "s"}</span>
      </header>

      {#if organising}
        <!-- The bar only exists in organise mode, and the buttons on it only
             do anything with something selected: a "Move" that is always
             pressable on a page where nothing is chosen is a button that
             either does nothing or does something surprising. -->
        <div class="organise-bar">
          <span class="selected-count">
            {selected.length} selected
          </span>
          <button class="move-open" disabled={!selected.length} onclick={openMove}>
            Move to folder…
          </button>
          <button class="transcribe-open" disabled={!selected.length} onclick={openTranscribeSelected}>
            Transcribe…
          </button>
          <button class="delete-open" disabled={!selected.length} onclick={() => { deleteError = ""; deleteOpen = true; }}>
            Delete…
          </button>
          <span class="organise-hint">Tap a score to choose it.</span>
        </div>
      {/if}

      {#if loading && !scores.length}
        <p class="empty">Loading…</p>
      {:else if !scores.length}
        <p class="empty">
          Nothing here yet. Drop files into your library folder and hit <em>Scan library</em>,
          or use <em>Upload</em>.
        </p>
      {:else}
        <div class="grid">
          {#each scores as score (score.id)}
            <a
              class="card"
              class:is-missing={score.missing_since}
              class:selectable={organising}
              class:selected={selected.includes(score.id)}
              href={"#/score/" + score.id}
              onclick={(e) => toggleSelected(score, e)}
            >
              <div class="sheet">
                {#if organising}
                  <!-- The whole card is the target, not a small checkbox in a
                       corner: this is used on a stand, at arm's length, often
                       with a tablet. The mark says which state the card is in
                       rather than offering a second thing to hit. -->
                  <span class="select-mark">{selected.includes(score.id) ? "✓" : ""}</span>
                {/if}
                {#if score.file_type === "pdf"}
                  <img src={api.thumbUrl(score.id)} alt="" loading="lazy" onerror={(e) => (e.target.style.display = "none")} />
                {:else}
                  <div class="sheet-icon">𝄞</div>
                {/if}
                <button class="fav" class:on={score.favorite} onclick={(e) => toggleFavorite(score, e)} title="Favorite">★</button>
                {#if score.has_transcription}
                  <!-- Whether it came from a scan's own bulk pass or a hand
                       edit makes no difference here on purpose (#190's own
                       No-gos) - this says only that a transcription exists,
                       which is what the transcription filter beside it
                       narrows the grid to. -->
                  <span class="transcribed-mark" title="This score has a transcription">♪</span>
                {/if}
                {#if kindLabel[score.content_kind]}
                  <span class="kind">{kindLabel[score.content_kind]}</span>
                {/if}
                {#if score.missing_since}
                  <!-- The file is not where Fermata last saw it. The SCORE is
                       untouched - its practice history, tags and any
                       hand-corrected transcription are all still attached, and
                       putting the file back (under this name or another) clears
                       this by itself on the next scan. Saying so on the card is
                       what makes "your library is intact, these files are not
                       reachable" visible instead of merely true. -->
                  <span class="missing-flag" title="Fermata cannot find this file. Nothing about the score has been lost - put the file back and scan again.">
                    file missing
                  </span>
                {/if}
              </div>
              <div class="meta">
                <div class="title">{score.title}</div>
                <div class="sub">{score.source ?? score.composer ?? score.collection ?? ""}</div>
                {#if score.last_practiced}
                  <div class="practiced">{practicedAgo(score.last_practiced)}</div>
                {/if}
              </div>
            </a>
          {/each}
        </div>
      {/if}
    {/if}

    {#if moveOpen}
      <div class="dialog-scrim">
        <div class="dialog move" role="dialog" aria-label="Move scores">
          <div class="dialog-head">
            Move {selected.length} score{selected.length === 1 ? "" : "s"}
          </div>
          <div class="folder-list">
            {#each folders as folder (folder.path)}
              <button
                class="folder-option"
                class:chosen={targetFolder === folder.path}
                style={`padding-left:${12 + folder.depth * 16}px`}
                onclick={() => previewMove(folder.path)}
              >
                <span class="folder-name">{folder.name}</span>
                <span class="count">{folder.score_count}</span>
              </button>
            {/each}
          </div>

          <div class="new-folder">
            <input
              class="new-folder-input"
              type="text"
              placeholder={targetFolder ? `New folder inside ${targetFolder}` : "New folder"}
              bind:value={newFolderName}
            />
            <button class="new-folder-create" disabled={!newFolderName.trim()} onclick={createFolder}>
              Create folder
            </button>
          </div>

          {#if targetFolder}
            <div class="folder-rename">
              {#if renamingFolder}
                <input class="folder-rename-input" type="text" bind:value={renameTo} />
                <button
                  class="folder-rename-preview"
                  disabled={!renameTo.trim() || renameTo.trim() === targetFolder}
                  onclick={previewFolderRename}
                >
                  Show what that would move
                </button>
                {#if renamePlan}
                  <div class="rename-preview">
                    <p class="plan-head">
                      {renamePlan.length} score{renamePlan.length === 1 ? "" : "s"} would move
                      with it.
                    </p>
                    <button class="folder-rename-apply" disabled={busy} onclick={applyFolderRename}>
                      Rename the folder
                    </button>
                  </div>
                {/if}
              {:else}
                <button
                  class="folder-rename-open"
                  onclick={() => { renamingFolder = true; renameTo = targetFolder; renamePlan = null; }}
                >
                  Rename “{targetFolder}” instead…
                </button>
              {/if}
            </div>
          {/if}

          {#if movePlan}
            <!-- THE PREVIEW IS THE SERVER'S OWN DRY RUN, line for line - the
                 same request with dry_run set, so what is shown here is what
                 would happen rather than this page's guess at it. -->
            <div class="move-preview">
              <p class="plan-head">This is what will happen:</p>
              <ul>
                {#each movePlan as line (line.score_id)}
                  <li class="plan-line" class:blocked={line.status === "blocked"}>
                    {#if line.status === "move"}
                      <span class="plan-from">{line.from_path}</span> →
                      <span class="plan-to">{line.to_path}</span>
                    {:else if line.status === "unchanged"}
                      <span class="plan-from">{line.from_path}</span> is already there
                    {:else}
                      <span class="plan-from">{line.from_path}</span> — {line.reason}
                    {/if}
                  </li>
                {/each}
              </ul>
            </div>
          {/if}

          {#if moveError}
            <p class="alert-error">{moveError}</p>
          {/if}

          <div class="dialog-actions">
            <button
              class="move-apply"
              disabled={busy || targetFolder === null || !movePlan || blockedLines.length > 0}
              onclick={applyMove}
            >
              {blockedLines.length
                ? "Fix the problems above first"
                : `Move ${selected.length} score${selected.length === 1 ? "" : "s"} here`}
            </button>
            <button class="dialog-cancel" onclick={() => (moveOpen = false)}>Cancel</button>
          </div>
        </div>
      </div>
    {/if}

    {#if deleteOpen}
      <div class="dialog-scrim">
        <div class="dialog delete" role="dialog" aria-label="Delete scores">
          <div class="dialog-head">
            Delete {selected.length} score{selected.length === 1 ? "" : "s"}?
          </div>
          <ul class="delete-list">
            {#each selectedScores as score (score.id)}
              <li>{score.title} <span class="plan-from">{score.path}</span></li>
            {/each}
          </ul>
          <!-- Said before the button is pressed, not after: this is the whole
               reason deleting can be offered at a tap in a music-stand
               interface at all. -->
          <p class="delete-note">
            The file moves to a trash folder inside your library. Your practice history, tags,
            goals and any transcription stay attached, and Trash puts it all back.
          </p>
          {#if deleteError}
            <p class="alert-error">{deleteError}</p>
          {/if}
          <div class="dialog-actions">
            <button class="delete-apply" disabled={busy} onclick={applyDelete}>
              Move to trash
            </button>
            <button class="dialog-cancel" onclick={() => (deleteOpen = false)}>Cancel</button>
          </div>
        </div>
      </div>
    {/if}

    {#if transcribeOpen}
      <div class="dialog-scrim">
        <div class="dialog transcribe" role="dialog" aria-label="Transcribe scores">
          <div class="dialog-head">
            {#if transcribeTarget?.kind === "collection"}
              Transcribe {transcribeTarget.collection}
            {:else}
              Transcribe {transcribeTarget?.ids?.length ?? 0} score{(transcribeTarget?.ids?.length ?? 0) === 1 ? "" : "s"}
            {/if}
          </div>

          {#if !transcribeStatus}
            <!-- Said before the button that starts it, not after - the same
                 habit as the delete dialog's own note. -->
            <p class="transcribe-note">
              Runs in the background - progress shows here, and every score gets its own
              outcome. A hand-edited transcription is never replaced.
            </p>
            <label class="reconvert-option">
              <input type="checkbox" bind:checked={transcribeReconvert} />
              Also redo scores that already have an extracted transcription
            </label>
            {#if transcribeError}
              <p class="alert-error">{transcribeError}</p>
            {/if}
            <div class="dialog-actions">
              <button class="transcribe-apply" disabled={busy} onclick={startTranscribeBatch}>
                Start
              </button>
              <button class="dialog-cancel" onclick={() => (transcribeOpen = false)}>Cancel</button>
            </div>
          {:else}
            <p class="transcribe-progress">
              {transcribeStatus.running
                ? `Transcribing… ${transcribeStatus.processed}/${transcribeStatus.total}`
                : `${transcribeStatus.transcribed} transcribed, ${transcribeStatus.already_transcribed} ` +
                  `already had one, ${transcribeStatus.non_extractable} not extractable, ` +
                  `${transcribeStatus.errored} errored.`}
            </p>
            {#if !transcribeStatus.running && transcribeStatus.with_defective_bars}
              <p class="transcribe-progress">
                {transcribeStatus.with_defective_bars} of those transcribed with bars that do not
                add up - open the score to see which.
              </p>
            {/if}
            <ul class="transcribe-results">
              {#each transcribeStatus.results as line (line.score_id)}
                <li class="transcribe-line outcome-{line.outcome}">
                  <span class="transcribe-title">{line.title ?? `Score #${line.score_id}`}</span>
                  <span class="transcribe-outcome">
                    {OUTCOME_LABEL[line.outcome] ?? line.outcome}{#if transcribeLineDetail(line)}
                      &nbsp;— {transcribeLineDetail(line)}{/if}
                  </span>
                </li>
              {/each}
            </ul>
            {#if !transcribeStatus.running}
              <div class="dialog-actions">
                <button class="dialog-cancel" onclick={closeTranscribe}>Close</button>
              </div>
            {/if}
          {/if}
        </div>
      </div>
    {/if}
  </main>
</div>

<style>
  .layout {
    display: grid;
    grid-template-columns: 250px 1fr;
    height: 100vh;
  }

  aside {
    display: flex;
    flex-direction: column;
    border-right: 1px solid var(--line);
    background: var(--bg-raised);
    padding: 18px 14px;
    overflow-y: auto;
  }

  .brand {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 2px 8px 16px;
  }

  .brand h1 {
    font-size: 26px;
    font-weight: 600;
    letter-spacing: 0.5px;
    color: var(--brass-bright);
  }

  nav {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .side-label {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: var(--ink-dim);
    margin: 16px 8px 6px;
  }

  .side-item {
    text-align: left;
    background: none;
    border: none;
    padding: 7px 10px;
    border-radius: 8px;
    color: var(--ink);
    display: flex;
    justify-content: space-between;
    gap: 8px;
  }

  .side-item:hover {
    background: var(--surface);
  }

  .side-item.active {
    background: var(--surface);
    color: var(--brass-bright);
  }

  .count {
    color: var(--ink-dim);
    font-size: 12px;
  }

  .tag-cloud {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    padding: 0 8px;
  }

  .chip {
    font-size: 12px;
    padding: 3px 10px;
    border-radius: 99px;
  }

  .chip.active {
    background: var(--brass);
    color: #241d0f;
    border-color: var(--brass);
  }

  .side-actions {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding-top: 16px;
  }

  .demo-link {
    font-size: 13px;
    text-align: center;
    color: var(--ink-dim);
  }

  .demo-link:hover {
    color: var(--brass-bright);
  }

  .build-tag {
    text-align: center;
    font-size: 11px;
    color: var(--ink-dim);
    opacity: 0.65;
    padding-top: 10px;
    letter-spacing: 0.02em;
  }

  main {
    overflow-y: auto;
    padding: 20px 28px 40px;
  }

  header {
    display: flex;
    align-items: center;
    gap: 12px;
    position: sticky;
    top: 0;
    padding: 8px 0 14px;
    background: linear-gradient(var(--bg) 75%, transparent);
    z-index: 2;
  }

  .search {
    flex: 1;
    max-width: 420px;
  }

  .result-count {
    color: var(--ink-dim);
    font-size: 13px;
    margin-left: auto;
  }

  .empty {
    color: var(--ink-dim);
    margin-top: 60px;
    text-align: center;
  }

  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 22px;
  }

  .dupe-list {
    display: flex;
    flex-direction: column;
    gap: 14px;
  }

  .dupe-group {
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 12px 16px;
  }

  .dupe-head {
    font-size: 14px;
    margin-bottom: 8px;
  }

  .dupe-paths {
    display: flex;
    flex-direction: column;
    gap: 3px;
  }

  .dupe-path {
    display: block;
    font-family: "SFMono-Regular", Consolas, Menlo, monospace;
    font-size: 12px;
    color: var(--ink-dim);
  }

  .dupe-path:hover {
    color: var(--brass-bright);
  }

  .card {
    color: var(--ink);
    display: block;
  }

  .sheet {
    position: relative;
    aspect-ratio: 3 / 4;
    background: var(--paper);
    border-radius: 6px;
    overflow: hidden;
    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.5), 0 10px 24px rgba(0, 0, 0, 0.35);
    transition: transform 0.18s ease, box-shadow 0.18s ease;
  }

  .card:hover .sheet {
    transform: translateY(-4px) rotate(-0.4deg);
    box-shadow: 0 4px 8px rgba(0, 0, 0, 0.5), 0 18px 36px rgba(0, 0, 0, 0.45);
  }

  .sheet img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    object-position: top;
  }

  .sheet-icon {
    display: grid;
    place-items: center;
    height: 100%;
    font-size: 64px;
    color: #6b5d3f;
  }

  .fav {
    position: absolute;
    top: 8px;
    right: 8px;
    border: none;
    background: rgba(22, 19, 14, 0.65);
    color: rgba(240, 232, 214, 0.55);
    border-radius: 99px;
    width: 30px;
    height: 30px;
    padding: 0;
    font-size: 15px;
    opacity: 0;
    transition: opacity 0.15s;
  }

  .card:hover .fav,
  .fav.on {
    opacity: 1;
  }

  .fav.on {
    color: var(--brass-bright);
  }

  /* Top-left is otherwise empty on a card: .fav sits top-right, .kind and
     .missing-flag sit along the bottom. */
  .transcribed-mark {
    position: absolute;
    left: 8px;
    top: 8px;
    font-size: 13px;
    line-height: 1;
    background: rgba(22, 19, 14, 0.75);
    color: var(--brass-bright);
    padding: 4px 7px;
    border-radius: 99px;
  }

  .kind {
    position: absolute;
    left: 8px;
    bottom: 8px;
    font-size: 11px;
    letter-spacing: 0.04em;
    background: rgba(22, 19, 14, 0.75);
    color: var(--brass-bright);
    padding: 2px 8px;
    border-radius: 99px;
  }

  /* Amber rather than red: nothing is broken and nothing is lost, the file is
     just not reachable. Red would say "you have lost this", which is the
     opposite of what happened. */
  .missing-flag {
    position: absolute;
    right: 8px;
    bottom: 8px;
    font-size: 11px;
    letter-spacing: 0.04em;
    background: rgba(22, 19, 14, 0.82);
    color: #e8b45c;
    border: 1px solid rgba(232, 180, 92, 0.5);
    padding: 2px 8px;
    border-radius: 99px;
  }

  /* Dimmed, not hidden. The score is still here and still opens; only the file
     behind it is unreachable, so the card stays reachable too. */
  .card.is-missing .sheet {
    opacity: 0.45;
  }

  .card.is-missing .title {
    color: var(--ink-dim);
  }

  .missing-count {
    color: #e8b45c;
    margin-left: 4px;
  }

  .alert {
    border: 1px solid rgba(232, 180, 92, 0.55);
    background: rgba(232, 180, 92, 0.08);
    border-radius: 8px;
    padding: 14px 16px;
    margin-bottom: 18px;
  }

  .alert-head {
    color: #e8b45c;
    font-weight: 600;
    margin-bottom: 6px;
  }

  .alert-body {
    /* The reason text is written as prose with paragraph breaks in it, and it is
       the same sentence the log carries. Preserving the breaks is what keeps it
       readable rather than one long run. */
    white-space: pre-line;
    margin: 0 0 10px;
    color: var(--ink);
  }

  .alert-paths {
    margin-bottom: 10px;
    color: var(--ink-dim);
    font-size: 13px;
  }

  .alert-paths ul {
    margin: 6px 0 0;
    padding-left: 20px;
    max-height: 220px;
    overflow-y: auto;
  }

  .alert-actions {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }

  .alert-note {
    color: var(--ink-dim);
    font-size: 13px;
    margin: 8px 0 0;
  }

  /* A recovery is good news, so it is not styled as an alert - same register
     as any other quiet statement of fact on this page. */
  .scan-note {
    color: var(--ink-dim);
    font-size: 13px;
    margin: 0 0 12px;
  }

  .alert-error {
    color: #e8b45c;
    font-size: 13px;
    margin: 8px 0 0;
  }

  /* ---- Managing the library (issue #56) ----------------------------------
     Everything here is sized for a music stand: the smallest target below is
     44px tall, because the hand reaching for it is often holding a plectrum
     and the screen is often at arm's length. */

  .organise-toggle {
    min-height: 44px;
    padding: 0 16px;
  }

  .organise-toggle.on {
    background: var(--brass);
    color: #241d0f;
    border-color: var(--brass);
  }

  .organise-bar {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 10px;
    margin: 0 0 16px;
    padding: 10px 14px;
    border: 1px solid rgba(232, 180, 92, 0.4);
    background: rgba(232, 180, 92, 0.06);
    border-radius: 8px;
  }

  .organise-bar button {
    min-height: 44px;
    padding: 0 18px;
  }

  .selected-count {
    font-family: var(--font-display);
    font-size: 15px;
  }

  .organise-hint {
    color: var(--ink-dim);
    font-size: 13px;
    margin-left: auto;
  }

  .card.selectable .sheet {
    outline: 2px solid transparent;
  }

  .card.selected .sheet {
    outline: 3px solid var(--brass-bright);
    outline-offset: 2px;
  }

  .select-mark {
    position: absolute;
    inset: 0;
    display: grid;
    place-items: center;
    font-size: 60px;
    color: var(--brass-bright);
    background: rgba(22, 19, 14, 0.35);
  }

  .notice {
    border: 1px solid rgba(232, 180, 92, 0.45);
    background: rgba(232, 180, 92, 0.07);
    border-radius: 8px;
    padding: 12px 14px;
    margin: 0 0 16px;
    color: var(--ink);
    line-height: 1.5;
  }

  .notice-dismiss {
    margin-left: 10px;
    font-size: 12px;
    padding: 4px 12px;
  }

  .dialog-scrim {
    position: fixed;
    inset: 0;
    background: rgba(12, 10, 7, 0.72);
    display: grid;
    place-items: center;
    z-index: 20;
    padding: 20px;
  }

  .dialog {
    background: var(--bg-raised);
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 20px;
    width: min(620px, 100%);
    max-height: 88vh;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }

  .dialog-head {
    font-family: var(--font-display);
    font-size: 20px;
    color: var(--brass-bright);
  }

  .folder-list {
    display: flex;
    flex-direction: column;
    gap: 4px;
    max-height: 280px;
    overflow-y: auto;
  }

  .folder-option {
    display: flex;
    justify-content: space-between;
    gap: 10px;
    text-align: left;
    min-height: 44px;
    align-items: center;
    background: none;
    border: 1px solid transparent;
    border-radius: 8px;
    color: var(--ink);
  }

  .folder-option:hover {
    background: var(--surface);
  }

  .folder-option.chosen {
    background: var(--surface);
    border-color: var(--brass);
    color: var(--brass-bright);
  }

  .new-folder,
  .folder-rename,
  .dialog-actions {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    align-items: center;
  }

  .new-folder input,
  .folder-rename input {
    flex: 1;
    min-width: 180px;
    min-height: 44px;
  }

  .new-folder button,
  .folder-rename button,
  .dialog-actions button {
    min-height: 44px;
    padding: 0 18px;
  }

  .move-preview,
  .rename-preview,
  .delete-list {
    border-top: 1px solid var(--line);
    padding-top: 12px;
    font-size: 13px;
    color: var(--ink-dim);
  }

  .plan-head {
    margin: 0 0 6px;
    color: var(--ink);
  }

  .move-preview ul,
  .delete-list {
    margin: 0;
    padding-left: 18px;
    max-height: 200px;
    overflow-y: auto;
  }

  .plan-line {
    font-family: "SFMono-Regular", Consolas, Menlo, monospace;
    font-size: 12px;
    line-height: 1.6;
    word-break: break-all;
  }

  .plan-line.blocked {
    color: #e8b45c;
  }

  .plan-to {
    color: var(--brass-bright);
  }

  .delete-note {
    margin: 0;
    color: var(--ink-dim);
    font-size: 13px;
    line-height: 1.5;
  }

  /* ---- Bulk transcription (issue #55) ------------------------------------ */

  .side-row {
    display: flex;
    align-items: center;
    gap: 2px;
  }

  .side-row .side-item {
    flex: 1;
    min-width: 0;
  }

  .side-transcribe {
    flex: none;
    width: 30px;
    height: 30px;
    display: grid;
    place-items: center;
    background: none;
    border: none;
    border-radius: 8px;
    color: var(--ink-dim);
    font-size: 14px;
  }

  .side-transcribe:hover {
    background: var(--surface);
    color: var(--brass-bright);
  }

  .transcribe-note {
    margin: 0;
    color: var(--ink-dim);
    font-size: 13px;
    line-height: 1.5;
  }

  .reconvert-option {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 14px;
  }

  .transcribe-progress {
    margin: 0;
    color: var(--ink);
    font-size: 14px;
    line-height: 1.5;
  }

  .transcribe-results {
    list-style: none;
    margin: 0;
    padding: 0;
    max-height: 320px;
    overflow-y: auto;
    border-top: 1px solid var(--line);
    display: flex;
    flex-direction: column;
  }

  .transcribe-line {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    padding: 8px 2px;
    border-bottom: 1px solid var(--line);
    font-size: 13px;
  }

  .transcribe-title {
    color: var(--ink);
  }

  .transcribe-outcome {
    color: var(--ink-dim);
    text-align: right;
  }

  .transcribe-line.outcome-transcribed .transcribe-outcome {
    color: var(--brass-bright);
  }

  .transcribe-line.outcome-errored .transcribe-outcome,
  .transcribe-line.outcome-non_extractable .transcribe-outcome {
    color: #e8b45c;
  }

  .trash-intro {
    color: var(--ink-dim);
    font-size: 13px;
    line-height: 1.5;
    margin: 0 0 16px;
    max-width: 70ch;
  }

  .trash-list {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .trash-row {
    display: flex;
    justify-content: space-between;
    gap: 14px;
    flex-wrap: wrap;
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 12px 16px;
  }

  .trash-title {
    font-family: var(--font-display);
    font-size: 15px;
  }

  .trash-path,
  .trash-kept {
    font-size: 12px;
    color: var(--ink-dim);
    margin-top: 2px;
  }

  .trash-actions {
    display: flex;
    gap: 8px;
    align-items: center;
    flex-wrap: wrap;
  }

  .trash-actions button {
    min-height: 44px;
    padding: 0 16px;
  }

  /* Amber, not red, and only on the one control that really destroys
     something. Everything else in this view is recoverable. */
  .trash-destroy-confirm {
    border-color: #e8b45c;
    color: #e8b45c;
  }

  .meta {
    padding: 8px 2px 0;
  }

  .title {
    font-family: var(--font-display);
    font-size: 15px;
    line-height: 1.25;
  }

  .sub {
    font-size: 12.5px;
    color: var(--ink-dim);
    margin-top: 2px;
  }

  .practiced {
    font-size: 11px;
    color: var(--ink-dim);
    opacity: 0.7;
    margin-top: 3px;
  }
</style>
