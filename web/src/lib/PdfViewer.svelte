<script>
  import * as pdfjs from "pdfjs-dist";
  import { api } from "./api.js";

  pdfjs.GlobalWorkerOptions.workerSrc = new URL(
    "pdfjs-dist/build/pdf.worker.min.mjs",
    import.meta.url,
  ).toString();

  let {
    score,
    gigMode = false,
    onToggleGig = () => {},
    practiceLabel = null,
    onStopPractice = () => {},
    // Whether Space/PageUp/PageDown/the arrow keys should turn pages here at
    // all. Defaults true - the only other caller (Viewer.svelte, for a PDF
    // with no transcription) always wants this on. ScoreCompare mounts this
    // alongside a TabViewer and keeps BOTH mounted even while only one pane
    // is on screen (see its own snippets), and TabViewer grew single-key
    // shortcuts of its own on several of the same keys (#92) - Space and the
    // plain arrow keys chief among them. Left both listening unconditionally,
    // showing the staff pane would still silently turn a page in the PDF
    // pane sitting behind it on every Space press. ScoreCompare passes this
    // `true` while the PDF pane is the one actually on screen AND while
    // BOTH panes are (side-by-side, the default layout the moment a score
    // has a transcription) - this is the one page-turning has always owned,
    // predating #92 by way of issue #106's own gig-mode-pedal reasoning, so
    // it keeps the keys there and TabViewer's newer ones stand down instead
    // (see ScoreCompare's own comment on the two `active` props for why).
    active = true,
  } = $props();

  let container;
  let darkMode = $state(true);
  let pageCount = $state(0);
  let currentPage = $state(1);
  let halfPage = $state(false);
  let saveTimer;
  // A page turn asked for before that page's canvas exists to scroll to.
  //
  // The document's page COUNT is known the moment its metadata parses, which
  // is a few hundred milliseconds before renderAllPages() has appended a
  // single canvas - so for that whole window the HUD already reads "1 / 2",
  // the arrow keys are already live, and goto()'s `?.` silently swallowed
  // every turn: onKey called preventDefault(), the key was consumed, and
  // nothing happened, with nothing left to retry it. A pedal tap on a score
  // that has just been opened is exactly that press, and it is what made
  // issue #168's spec fail on CI (measured on an idle machine: the HUD read
  // "1 / 2" at 293ms with zero canvases in the container; page 2's canvas
  // arrived at 327ms - and CI load widens that gap, it does not close it).
  // Remembered here instead, and honoured as soon as rendering reaches it.
  let pendingPage = null;
  // Whether a turn like that was actually honoured, so the score.last_page
  // restore below does not immediately yank the reader back off the page
  // they just asked for.
  let turnedBeforeRestore = false;
  // The page THIS COMPONENT last decided to show, while that scroll is still
  // settling. null the rest of the time.
  //
  // The IntersectionObserver below is how ordinary reader scrolling - wheel,
  // touch drag, the scrollbar - is followed, and for that it is right. It
  // cannot also be the only way a turn the component performed ITSELF gets
  // recorded, for two reasons that bite together:
  //
  //   - it is blanked wholesale for the duration of a resize re-render (see
  //     suppressTracking), and
  //   - it only ever fires on a CHANGE, so a crossing that lands inside that
  //     blanked window is not re-delivered afterwards. Nothing retries it.
  //
  // So a turn whose crossing fell in the window used to leave the pane
  // showing the new page while the indicator still read the old one, for as
  // long as the score stayed open - the press was accepted and then
  // discarded, exactly the shape of the bug this issue opened on, one layer
  // further in. Measured on this branch (a 20-page score, resize re-render
  // deliberately in flight): the flush began at 1569ms, the key at 1606ms,
  // the turn's own crossing was delivered at 1615ms and dropped as
  // suppressed, and the re-render's scroll restore then put the reader back
  // on page 1 at 1876ms. The HUD read "1 / 20" from then on.
  //
  // Recording the decision here, the moment it is made, is what makes the
  // indicator independent of whether that one callback survives.
  let intendedPage = null;
  let settleTimer;
  // The scroll position this component last asked for on the reader's
  // behalf, as a page plus a fraction down it, and a counter of how many
  // times that has happened.
  //
  // intendedPage above answers "which page does the INDICATOR belong on
  // while a turn settles". This answers the different question a resize
  // re-render has to ask: "did the reader ask to be somewhere else while I
  // was busy, and where". A whole-page turn and a half-page one are both
  // recorded here, in the same shape, so the re-render's restore does not
  // have to know which kind it is putting back - which is the reason the
  // half-page branch went uncovered by #169 and #175 in the first place.
  let requestedScroll = null;
  let scrollRequestSeq = 0;

  // half-page advance defaults on each time gig mode is entered, but the
  // performer can still turn it off without it snapping back mid-set
  let wasGig = false;
  $effect(() => {
    if (gigMode && !wasGig) halfPage = true;
    wasGig = gigMode;
  });

  $effect(() => {
    let cancelled = false;
    let observer;
    let resizeObserver;
    let resizeTimer;
    let pdfDoc = null;
    let renderedWidth = 0;
    // coalesce rapid resizes into a single re-render pass rather than
    // stacking overlapping page.render() calls on the same canvases
    let rerendering = false;
    let pendingWidth = null;
    // canvases resize one at a time mid-loop, so the observer sees a
    // transient, inconsistent layout while a re-render is in flight - ignore
    // its callbacks until the final scroll restore has settled
    let suppressTracking = false;

    function computeWidth() {
      return Math.min(container.clientWidth - 32, 1100);
    }

    async function renderPageInto(page, canvas, width, dpr) {
      const base = page.getViewport({ scale: 1 });
      const scale = width / base.width;
      const viewport = page.getViewport({ scale: scale * dpr });
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      canvas.style.width = `${width}px`;
      await page.render({ canvasContext: canvas.getContext("2d"), viewport }).promise;
    }

    async function renderAllPages(width) {
      renderedWidth = width;
      const dpr = window.devicePixelRatio || 1;
      for (let n = 1; n <= pdfDoc.numPages; n++) {
        const page = await pdfDoc.getPage(n);
        if (cancelled) return;
        const canvas = document.createElement("canvas");
        canvas.className = "pdf-page";
        canvas.dataset.page = n;
        container.appendChild(canvas);
        await renderPageInto(page, canvas, width, dpr);
        // A turn pressed before this page existed. Clamped to the last page
        // here rather than in goto(), because goto() may well have run before
        // pageCount was known at all and so had nothing to clamp against.
        // Pages are appended in order, so nothing rendered after this one can
        // move it - no need to wait for the whole document to finish.
        if (pendingPage !== null && Math.min(pendingPage, pdfDoc.numPages) === n) {
          pendingPage = null;
          turnedBeforeRestore = true;
          intendedPage = n;
          setPage(n);
          canvas.scrollIntoView({ block: "start" });
        }
      }
    }

    async function rerenderAtWidth(width) {
      renderedWidth = width;
      const dpr = window.devicePixelRatio || 1;
      // Where the reader is, as a page plus a fraction down it, captured
      // BEFORE the canvases change size.
      //
      // The restore below used to aim at the page's TOP, which discards
      // every within-page position there is - and in gig mode that is most
      // of them, because a half-page turn exists precisely to leave the
      // reader halfway down a page. So a re-render landing shortly after a
      // pedal tap scrolled them straight back off the half they had just
      // turned to, with nothing left to retry it: the same shape as #168 and
      // #175, on the one branch of turn() neither of them covered, and
      // entering gig mode is exactly the moment both happen together
      // (widening the pane forces the re-render). Measured, a 2-page score
      // entering gig mode: the tap left the scroller at 313px and the
      // restore put it back to 24px, page one's top.
      //
      // Read off the geometry rather than off currentPage, which is a moving
      // target: the awaits below take hundreds of milliseconds, a key press
      // inside them runs goto() and changes currentPage synchronously, and
      // measuring the fraction against the page BEFORE and restoring against
      // the page AFTER silently adds one page's intra-page offset to a
      // different page's top. Measured on a 20-page score, reader a third
      // down page one and a turn pressed mid-re-render: the restore landed
      // 140px past page two's top, scaling with how far down page one they
      // had been.
      const before = positionAt(container.scrollTop);
      const seqBefore = scrollRequestSeq;
      // re-render the existing canvases in place (same elements, same
      // order) so the IntersectionObserver's page tracking keeps working
      // without needing to be torn down and reattached
      for (let n = 1; n <= pdfDoc.numPages; n++) {
        if (cancelled) return;
        const canvas = container.querySelector(`[data-page="${n}"]`);
        if (!canvas) continue;
        const page = await pdfDoc.getPage(n);
        await renderPageInto(page, canvas, width, dpr);
      }
      if (cancelled) return;
      // A turn taken DURING the re-render outranks where the reader was when
      // it started - it is the most recent thing they asked for, and it is
      // the one with nothing to retry it if this restore overwrites it. A
      // whole-page turn asks for that page's top; a half-page one asks for
      // an offset within a page, and both say so the same way, so this does
      // not have to know which kind it was.
      const target = scrollRequestSeq !== seqBefore ? requestedScroll : before;
      // canvases changed height, so restore scroll to wherever that position
      // now is rather than let it drift to an arbitrary pixel offset - the
      // same fraction down the same page, not merely that page's top
      const restored = target && container.querySelector(`[data-page="${target.page}"]`);
      if (restored) {
        container.scrollTop = pageTop(restored) + target.fraction * restored.getBoundingClientRect().height;
      }
    }

    async function flushResize() {
      rerendering = true;
      suppressTracking = true;
      while (pendingWidth !== null && Math.abs(pendingWidth - renderedWidth) >= 2) {
        const width = pendingWidth;
        pendingWidth = null;
        await rerenderAtWidth(width);
      }
      rerendering = false;
      // wait for the final scrollIntoView to actually paint before trusting
      // the observer again, so it doesn't fire on the mid-resize layout
      requestAnimationFrame(() => requestAnimationFrame(() => (suppressTracking = false)));
    }

    (async () => {
      pdfDoc = await pdfjs.getDocument(api.fileUrl(score.id)).promise;
      if (cancelled) return;
      pageCount = pdfDoc.numPages;

      await renderAllPages(computeWidth());
      if (cancelled) return;

      observer = new IntersectionObserver(
        (entries) => {
          if (suppressTracking) return; // mid-resize reflow, not a real page turn
          // a half-page turn routinely leaves two adjacent pages both past
          // the threshold; take whichever is most visible, not whichever
          // happens to be last in this batch, or the tracked page (and the
          // saved last_page) can land on the page being left instead of entered
          let best = null;
          for (const e of entries) {
            if (e.isIntersecting && (!best || e.intersectionRatio > best.intersectionRatio)) {
              best = e;
            }
          }
          if (best) {
            const seen = Number(best.target.dataset.page);
            // A turn this component performed is already recorded (see
            // intendedPage). Until that scroll settles, the frames it passes
            // through are not the reader going anywhere - taking them would
            // drag the indicator back onto the page being LEFT, which is the
            // same wrong answer by a different route. The arrival itself is
            // what hands tracking back to the reader.
            if (intendedPage !== null) {
              if (seen === intendedPage) intendedPage = null;
              return;
            }
            setPage(seen);
          }
        },
        { root: container, threshold: 0.4 },
      );
      container.querySelectorAll(".pdf-page").forEach((c) => observer.observe(c));

      if (score.last_page > 1 && !turnedBeforeRestore) {
        container
          .querySelector(`[data-page="${score.last_page}"]`)
          ?.scrollIntoView({ block: "start" });
      }

      // entering gig-mode fullscreen (or any viewport change) resizes the
      // scroller, and pages need to re-render at the new width or they sit
      // at the old windowed size with wide margins
      resizeObserver = new ResizeObserver(() => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => {
          if (cancelled) return;
          pendingWidth = computeWidth();
          if (!rerendering) flushResize();
        }, 200);
      });
      resizeObserver.observe(container);

      // A programmatic turn hands tracking back to the reader when its own
      // arrival is observed. If that never happens - the reader grabbed the
      // scrollbar mid-turn, or the smooth scroll was cut short - the scroll
      // going quiet is the other way it must be handed back, or the observer
      // would be ignored for as long as the score stayed open.
      container.addEventListener("scroll", onScroll, { passive: true });
    })();

    function onScroll() {
      clearTimeout(settleTimer);
      settleTimer = setTimeout(() => (intendedPage = null), 200);
    }

    return () => {
      cancelled = true;
      container?.removeEventListener("scroll", onScroll);
      clearTimeout(settleTimer);
      intendedPage = null;
      // both belong to the document this pass loaded; a re-run (a different
      // score) must not inherit a turn pressed against the old one, nor its
      // permission to skip the new one's last_page restore
      pendingPage = null;
      turnedBeforeRestore = false;
      requestedScroll = null;
      observer?.disconnect();
      resizeObserver?.disconnect();
      clearTimeout(saveTimer);
      clearTimeout(resizeTimer);
    };
  });

  // The one place the page indicator moves, so that a turn this component
  // performed and a scroll the reader performed are recorded identically -
  // including the debounced last_page write, which used to live only on the
  // observer's path and so was skipped for any turn the observer missed.
  function setPage(n) {
    currentPage = n;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => api.patch(score.id, { last_page: currentPage }).catch(() => {}), 1200);
  }

  // Where a page's top edge sits in the scroller's own coordinates. Read off
  // rects rather than offsetTop, which is measured against whichever
  // ancestor happens to be positioned - .wrap here, not the scroller - and
  // so would carry that element's own offset into the arithmetic.
  function pageTop(canvas) {
    return canvas.getBoundingClientRect().top - container.getBoundingClientRect().top + container.scrollTop;
  }

  // A scroll offset said as a page plus a fraction down it, which is the
  // only form that survives the pages being re-rendered at another height.
  // The gap between two pages is attributed to the one above it (a fraction
  // slightly over 1) so that every offset belongs to exactly one page and
  // the mapping stays monotonic.
  function positionAt(scrollTop) {
    for (let n = pageCount; n >= 1; n--) {
      const canvas = container.querySelector(`[data-page="${n}"]`);
      if (!canvas) continue;
      const top = pageTop(canvas);
      if (scrollTop >= top || n === 1) {
        const height = canvas.getBoundingClientRect().height;
        return { page: n, fraction: height ? (scrollTop - top) / height : 0 };
      }
    }
    return null;
  }

  // Records a scroll this component is about to perform, so a resize
  // re-render already in flight puts the reader where they just asked to be
  // rather than back where they were when it started.
  function requestScroll(position) {
    if (!position) return;
    requestedScroll = position;
    scrollRequestSeq += 1;
  }

  function goto(page) {
    // pageCount is 0 until the document's metadata has parsed. Clamping
    // against it then would fold every early turn onto page 1 - which is
    // where the reader already is - so only clamp once there is a count to
    // clamp to; renderAllPages() does the rest (see pendingPage above).
    const target = pageCount ? Math.max(1, Math.min(pageCount, page)) : Math.max(1, page);
    const canvas = container.querySelector(`[data-page="${target}"]`);
    if (canvas) {
      pendingPage = null;
      // Recorded BEFORE the scroll is asked for, not as a consequence of it
      // being observed. Everything downstream of here - the observer, the
      // resize re-render's scroll restore - now reads the page the reader
      // asked for rather than the last one that happened to be seen.
      intendedPage = target;
      setPage(target);
      // The top of that page, which is what scrollIntoView below asks for.
      requestScroll({ page: target, fraction: 0 });
      canvas.scrollIntoView({ block: "start", behavior: "smooth" });
    } else {
      pendingPage = target;
    }
  }

  function turn(dir) {
    // The half-page step needs the current page's rendered height to measure
    // against; before that canvas exists there is nothing to measure and
    // nothing to scroll, so this falls through to goto() - which can at
    // least remember the turn - instead of scrolling an empty container by a
    // viewport height and losing the press (#168).
    const current = container.querySelector(`[data-page="${currentPage}"]`);
    if (gigMode && halfPage && current) {
      // step off the current page's actual rendered height (plus its share
      // of the gap) rather than the viewport, so repeated half-turns track
      // bar lines instead of drifting against page/container padding
      const step = dir * (current.getBoundingClientRect().height + 18) * 0.5;
      // Where that leaves the reader, recorded before the scroll is asked
      // for. Same reason goto() records its own target: a resize re-render
      // in flight will otherwise put them back where this step started, and
      // a pedal tap has nothing to retry it with.
      requestScroll(positionAt(container.scrollTop + step));
      container.scrollBy({ top: step, behavior: "smooth" });
    } else {
      goto(currentPage + dir);
    }
  }

  function onKey(e) {
    if (!active) return;
    const tag = e.target?.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || e.target?.isContentEditable) return;
    if (e.key === "ArrowRight" || e.key === "PageDown" || e.key === " ") {
      e.preventDefault();
      turn(1);
    } else if (e.key === "ArrowLeft" || e.key === "PageUp") {
      e.preventDefault();
      turn(-1);
    }
  }

  // gig-mode tap-to-turn: listened for on the scroller itself (not an
  // overlay sibling) so wheel/touch-drag scrolling and the native scrollbar
  // are never blocked - a "tap" is only recognized after the fact, from how
  // little the pointer moved, so a real drag/scroll always falls through
  const TAP_MAX_MOVE = 10;
  const TAP_MAX_TIME = 500;
  let tapStart = null;

  function onZonePointerDown(e) {
    if (!gigMode || (e.pointerType === "mouse" && e.button !== 0)) return;
    tapStart = { x: e.clientX, y: e.clientY, time: Date.now() };
  }

  function onZonePointerUp(e) {
    if (!gigMode || !tapStart) return;
    const { x, y, time } = tapStart;
    tapStart = null;
    if (Math.hypot(e.clientX - x, e.clientY - y) > TAP_MAX_MOVE || Date.now() - time > TAP_MAX_TIME) return;
    const rect = container.getBoundingClientRect();
    const frac = (e.clientX - rect.left) / rect.width;
    if (frac < 1 / 3) turn(-1);
    else if (frac > 2 / 3) turn(1);
  }
</script>

<svelte:window onkeydown={onKey} />

<div class="wrap">
  <!-- svelte-ignore a11y_no_static_element_interactions -- pointer handlers
       only distinguish a tap from a scroll/drag on this scroll region, they
       don't turn it into a control; the ‹ › HUD buttons are the real ones -->
  <div
    class="pages"
    class:dark={darkMode}
    bind:this={container}
    onpointerdown={onZonePointerDown}
    onpointerup={onZonePointerUp}
  ></div>
  <div class="hud">
    <button onclick={() => turn(-1)} title="Previous page">‹</button>
    <span>{currentPage} / {pageCount || "…"}</span>
    <button onclick={() => turn(1)} title="Next page">›</button>
    <button class:on={darkMode} onclick={() => (darkMode = !darkMode)} title="Invert for practice in the dark">
      ◐
    </button>
    {#if gigMode}
      {#if practiceLabel}
        <button class="practice-indicator" onclick={onStopPractice} title="Stop practice timer">
          ● {practiceLabel}
        </button>
      {/if}
      <button class:on={halfPage} onclick={() => (halfPage = !halfPage)} title="Half-page turns">½</button>
      <button onclick={onToggleGig} title="Exit gig mode (Esc)">⤢</button>
    {/if}
  </div>
</div>

<style>
  .wrap {
    position: relative;
    flex: 1;
    min-height: 0;
    display: flex;
  }

  .pages {
    flex: 1;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 18px;
    padding: 24px 16px 80px;
  }

  .pages :global(.pdf-page) {
    box-shadow: 0 4px 24px rgba(0, 0, 0, 0.55);
    border-radius: 3px;
    background: white;
  }

  .pages.dark :global(.pdf-page) {
    filter: invert(0.92) hue-rotate(180deg);
    box-shadow: 0 4px 24px rgba(0, 0, 0, 0.8);
  }

  .hud {
    position: absolute;
    z-index: 2;
    bottom: 18px;
    left: 50%;
    transform: translateX(-50%);
    display: flex;
    align-items: center;
    gap: 10px;
    background: rgba(32, 27, 19, 0.92);
    border: 1px solid var(--line);
    border-radius: 99px;
    padding: 6px 12px;
    backdrop-filter: blur(6px);
  }

  .hud span {
    font-size: 13px;
    color: var(--ink-dim);
    min-width: 60px;
    text-align: center;
  }

  .hud button {
    border: none;
    background: none;
    font-size: 16px;
    padding: 4px 8px;
  }

  .hud button.on {
    color: var(--brass-bright);
  }

  .practice-indicator {
    font-size: 13px;
    font-variant-numeric: tabular-nums;
    color: var(--brass-bright);
    white-space: nowrap;
  }
</style>
