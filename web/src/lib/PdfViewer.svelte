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
  // Whether the pane is still TRAVELLING to the position above, rather than
  // sitting at it. Both turn paths scroll with `behavior: "smooth"`, so for
  // as long as that animation runs, container.scrollTop is a frame of it -
  // a place the reader is passing through, not one they asked for.
  //
  // Everything that reads this trusts it to mean "the reader's own place is
  // not on the scroller right now", which is a claim with a short life and
  // several ways to end. A flag left set past its scroll is not a smaller
  // bug than the one it was added for; it is the same bug pointing the
  // other way - a re-render would then discard the reader's real scrolling
  // in favour of a turn that is over, or was never delivered at all. So the
  // ways it ends are enumerated, and none of them assume a scroll happens:
  //
  //   - NEVER SET, when the pane is already at what was asked for. A turn
  //     onto the page already shown scrolls nothing, so no scroll event
  //     follows, so nothing below could ever run. See requestScroll.
  //   - arrived, checked on each scroll event (onScroll).
  //   - taken over, when the pane RETREATS from its closest approach by more
  //     than the pixel of slack an arrival is allowed - a smooth scroll only
  //     ever closes on its target, so moving back off it is a reader with
  //     their hand on the pane (onScroll) - or when one of the input events
  //     a takeover arrives on is seen at all (see the wheel/pointer/touch
  //     listeners, which say it a frame sooner and without arithmetic).
  //   - delivered by the restore itself, which assigns the destination.
  //   - quiet, as the backstop for a scroll that does none of the above:
  //     200ms with nothing moving, armed BOTH when the scroll is asked for
  //     and on every scroll event, so a request that never moves the pane
  //     still expires.
  let scrollTravelling = false;
  // How close the pane has got to `requestedScroll` during the current
  // travel, in pixels; null until its first scroll event. Only a retreat
  // from this counts as the reader taking over - measuring against the
  // previous event alone would call the first event of any travel a retreat
  // whenever the pane starts out nearer the target than the reader's own
  // first move leaves it.
  let travelClosest = null;
  let travelTimer;
  // The slack, in pixels, on both "has it arrived" and "has it retreated".
  // Scroll offsets land on device pixels while these targets are computed in
  // CSS ones, so neither question can be asked exactly. The cost of the
  // second one is worth stating: a takeover that never backs off its closest
  // approach by more than a pixel is not recognized as one here, and falls
  // through to the quiet backstop above instead.
  const SCROLL_SLACK = 1;
  // The events a reader takes a scroll over WITH: a wheel or trackpad, a
  // finger, or a hand on the scrollbar. Listened for on the scroller itself
  // and passively, so none of them is intercepted or delayed - they are read
  // as evidence, never handled. See onReaderInput.
  const READER_INPUT = ["wheel", "touchstart", "pointerdown"];

  // Read-only test instrumentation, in the same spirit as the `data-page`
  // attribute each canvas already carries. Nothing in here reads either of
  // these back - they are written for the browser suite, which otherwise has
  // to guess at frames to know when this pane has finished with a press.
  //
  // There are TWO of them because there are two different claims to make, and
  // an earlier version of this made only one attribute carry both. That is
  // the bug worth recording here: a quiet-scroll timer and the re-render's
  // restore both stamped the same counter, so a barrier waiting on it could
  // be satisfied by the cheaper, earlier of the two. Traced on the
  // half-page-turn-across-a-re-render test, 8 of 8 runs: the TURN went quiet
  // at 1086, the re-render started at 1132 and restored at 1140 - so the
  // barrier returned 54ms before the thing it was waiting for.
  //
  //   data-render-settle-seq  a resize re-render HAS RESTORED THE SCROLL and
  //                           painted it. Written in exactly one place, the
  //                           post-restore double-rAF in flushResize, and
  //                           never removed.
  //   data-settle-seq         the pane is AT REST and has nothing queued:
  //                           200ms with no scroll event, no re-render in
  //                           flight, and none pending either. Removed the
  //                           moment this component asks the pane to move.
  //
  // The "none pending" half is what the earlier version was missing, and is
  // what made a turn's own quiet stamp land in front of the re-render that
  // turn was racing: the resize had been observed and its debounce was
  // counting down, but nothing had started yet, so the pane looked idle.
  let settleSeq = 0;
  let stampTimer;
  function markUnsettled() {
    // Both the last stamp and any quiet stamp still counting down are stale
    // the moment this component asks the pane to move.
    clearTimeout(stampTimer);
    container?.removeAttribute("data-settle-seq");
  }
  function markSettled() {
    if (container) container.dataset.settleSeq = String(++settleSeq);
  }
  let renderSettleSeq = 0;
  function markRenderSettled() {
    if (container) container.dataset.renderSettleSeq = String(++renderSettleSeq);
  }

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
    // Which scroll request this flush's restore actually assigned, or -1 for
    // "this flush has not restored anything". Reset at the top of every
    // flush, so a stamp is never written off a previous flush's restore, and
    // compared against scrollRequestSeq at stamp time so a turn that landed
    // AFTER the restore - and is therefore still travelling - does not get
    // called a finished restore either. scrollRequestSeq only ever counts up
    // from 0, so -1 also serves as the "did not restore" guard.
    let restoredAtSeq = -1;
    // A re-render has been OBSERVED but not yet finished: set the moment the
    // ResizeObserver fires, cleared when the flush it leads to has handed
    // tracking back. `rerendering` alone is not this - it is false for the
    // whole 200ms debounce, during which a resize is certainly coming.
    let resizePending = false;
    // The pane has gone quiet: 200ms with no scroll event, and no re-render
    // in flight or pending - whose restore is about to move the pane again
    // and which stamps for itself when it has.
    function stampWhenQuiet() {
      clearTimeout(stampTimer);
      stampTimer = setTimeout(() => {
        if (!cancelled && !rerendering && !resizePending) markSettled();
      }, 200);
    }

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
      //
      // And read off the reader's OUTSTANDING REQUEST rather than off the
      // scroller whenever a turn is still travelling to it (#234). Both turn
      // paths scroll with `behavior: "smooth"`, so between the press and the
      // arrival container.scrollTop is a frame of an animation. Snapshotting
      // one of those frames as "where the reader is" and then restoring to it
      // does two wrong things at once: it throws away the half page they
      // asked for, and the restore's own assignment to scrollTop aborts the
      // animation that was going to deliver it - so they are left partway,
      // permanently, with nothing to retry it. Measured on a runner where
      // that animation takes frames rather than landing in one (this is the
      // whole of #234's 384/1100, and why it never appeared on a box where it
      // lands in one): press at scrollTop 0 on 608px pages, the animation
      // reaching 2, 12, 38, 96, 164, 208, 236 over seven frames, the
      // re-render capturing 236 and restoring to 24 + (236-24)/608 * 1100 =
      // 408 - 35% down a page the reader had asked to be 50% down, and the
      // pane genuinely at rest there.
      const before =
        scrollTravelling && requestedScroll ? requestedScroll : positionAt(container.scrollTop);
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
      const restored = offsetOf(target);
      if (restored !== null) {
        container.scrollTop = restored;
        restoredAtSeq = scrollRequestSeq;
        // Assigning scrollTop ends any smooth scroll that was still running,
        // and this assignment IS that scroll's destination - so nothing is
        // travelling any more, and the next thing to read the pane should
        // read it off the scroller.
        endTravel();
      }
    }

    async function flushResize() {
      rerendering = true;
      suppressTracking = true;
      // Nothing this flush has restored yet, so nothing for it to stamp.
      restoredAtSeq = -1;
      while (pendingWidth !== null && Math.abs(pendingWidth - renderedWidth) >= 2) {
        const width = pendingWidth;
        pendingWidth = null;
        await rerenderAtWidth(width);
      }
      rerendering = false;
      // wait for the final scrollIntoView to actually paint before trusting
      // the observer again, so it doesn't fire on the mid-resize layout
      requestAnimationFrame(() =>
        requestAnimationFrame(() => {
          // Not this flush's frame any more if another resize has started one
          // of its own in the two frames since: it would hand the observer
          // back in the middle of somebody else's re-render, and re-derive
          // the page off canvases that are being resized one at a time as it
          // reads them. That flush schedules its own frame and does both when
          // its own geometry is final.
          if (cancelled || rerendering) return;
          suppressTracking = false;
          // Handing the observer back is not enough on its own: the geometry
          // it was blanked THROUGH is the geometry now on screen, and an
          // IntersectionObserver only ever fires on a CHANGE. Every crossing
          // the resize caused was delivered while suppressed and thrown
          // away, and nothing re-delivers them - so the indicator kept
          // saying whatever it last said before the pane changed shape,
          // with no scroll left to come and correct it. Measured on a
          // 2-page score entering gig mode with a tap inside the 200ms
          // debounce: at the narrow pre-render geometry the tap put page two
          // at ratio 0.63 and the indicator at "2 / 2"; the re-render then
          // left the reader at scrollTop 547 - 47% down page ONE, page two
          // down to 0.11 - and the HUD read "2 / 2" for as long as the score
          // stayed open. Re-deriving it once, here, is what closes that:
          // there is no crossing left to wait for.
          const seen = visiblePage();
          if (seen !== null) {
            // Deliberately NOT deferred to a turn still marked in flight,
            // the way the observer's own crossings are (see trackVisible).
            // The restore above assigns scrollTop, which aborts any scroll
            // that was still running, so by this frame the pane is AT REST:
            // its geometry is the whole truth and there is no arrival left
            // to hand tracking back. Deferring here would leave the
            // indicator waiting on a scroll that was cancelled - which is
            // this bug again, one route further along - so the flag is
            // cleared rather than obeyed.
            //
            // In this suite's browser the two spellings cannot be told
            // apart, and that is a fact about the geometry rather than a
            // gap in the tests: the restore lands on the requested page's
            // TOP, where that page is by construction the most visible one
            // (it fills the pane from the top down, and a tie goes to the
            // lower page number), so `seen` always equals the turn's target
            // when a turn is in flight at all. It is a browser that animates
            // `behavior: "smooth"` - which this one does not, measured: a
            // 6910px scrollIntoView is complete in the frame it is issued -
            // where a turn can still be travelling when the flush ends, and
            // there the two differ.
            intendedPage = null;
            // Only when it has actually moved, and ONLY here. This is the
            // one caller that speaks after every re-render rather than on a
            // change - the observer fires on a crossing, which is a change
            // by definition, and every other caller is recording an intent
            // worth saving even when it names the page already showing (a
            // turn pressed before any canvas exists resolves to page one on
            // a score stored at page eight, and that write is the whole
            // point of it). Gating inside setPage instead swallowed that
            // one: measured, the reader sat on page one while the database
            // kept eight. Ungated here, a reader who never leaves page one
            // still wrote it once per resize - 10 writes for 10 resizes.
            if (seen !== currentPage) setPage(seen);
          }
          // Last, so the stamp means the indicator is final too: the restore
          // has been assigned and painted, and this frame's re-derivation has
          // run. Only when this flush restored at all, and nothing has been
          // asked for since it did - a turn that landed after the restore is
          // still travelling, and this flush has no business calling that
          // finished. There is nothing else to stamp it: the next re-render
          // is the next thing that writes here, which is the whole meaning of
          // the attribute.
          if (restoredAtSeq >= 0 && scrollRequestSeq === restoredAtSeq) markRenderSettled();
          // The re-render is done with the pane, so rest is a claim worth
          // making again. Armed explicitly rather than left to the restore's
          // own scroll event, which a restore that lands where the reader
          // already was does not fire at all.
          resizePending = false;
          stampWhenQuiet();
        }),
      );
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
          if (best) trackVisible(Number(best.target.dataset.page));
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
        // Said here rather than when the debounce fires: the pane is not idle
        // for those 200ms, it is waiting, and a turn that goes quiet inside
        // them must not be mistaken for the pane having finished.
        resizePending = true;
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
      for (const kind of READER_INPUT) {
        container.addEventListener(kind, onReaderInput, { passive: true });
      }
    })();

    function onScroll() {
      // Both endings that can only be seen from a scroll event, and they are
      // checked here rather than left to the quiet backstop because that
      // timer only fires when the SCROLLING stops - and a reader who takes
      // over with a wheel straight after a turn keeps it re-armed for as long
      // as they keep scrolling. A re-render landing in that window would
      // restore them to the turn instead of to where they had scrolled to,
      // which is the whole failure this flag exists to prevent, aimed the
      // other way.
      if (scrollTravelling) {
        const distance = travelDistance(requestedScroll);
        if (distance === null || distance <= SCROLL_SLACK) {
          // Arrived - or the page it was aimed at is gone, which is not a
          // travel any more either way.
          endTravel();
        } else if (travelClosest !== null && distance > travelClosest + SCROLL_SLACK) {
          // Retreating from its closest approach. A smooth scroll only ever
          // closes on its target, so this is a hand on the pane: the reader
          // has taken the scroll over, the animation is cancelled, and where
          // they are now is their own place rather than a frame of anything.
          endTravel();
        } else {
          travelClosest = travelClosest === null ? distance : Math.min(travelClosest, distance);
          armTravelBackstop();
        }
      }
      clearTimeout(settleTimer);
      settleTimer = setTimeout(() => (intendedPage = null), 200);
      stampWhenQuiet();
    }

    // The reader's hand, said directly rather than inferred from where the
    // pane went. These are the events a takeover of a scroll in flight
    // actually arrives on, and each one is a statement that the next scroll
    // belongs to the reader - so the flag ends here a frame before the
    // arithmetic in onScroll could reach the same conclusion, and in the
    // cases it cannot reach at all (a hand that stops the pane dead on the
    // target's own pixel, or one that grabs it and holds it still).
    //
    // Not keyboard scrolling: the scroller carries no tabindex, so it never
    // holds focus, and this component preventDefaults the keys that would
    // scroll it while it is the pane taking keys at all (see onKey). Nothing
    // reaches the pane by key that is not this component's own turn.
    //
    // A gig-mode tap is safe against this: it turns on POINTERUP (see
    // onZonePointerUp), so its own pointerdown has already ended whatever was
    // travelling before the turn it triggers records the next one.
    function onReaderInput() {
      if (scrollTravelling) endTravel();
    }

    return () => {
      cancelled = true;
      container?.removeEventListener("scroll", onScroll);
      for (const kind of READER_INPUT) container?.removeEventListener(kind, onReaderInput);
      clearTimeout(settleTimer);
      clearTimeout(stampTimer);
      intendedPage = null;
      // both belong to the document this pass loaded; a re-run (a different
      // score) must not inherit a turn pressed against the old one, nor its
      // permission to skip the new one's last_page restore
      pendingPage = null;
      turnedBeforeRestore = false;
      requestedScroll = null;
      endTravel();
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

  // Which page the pane is actually showing, read straight off the rects: of
  // the pages intersecting the scroller, the one showing the largest fraction
  // of ITSELF, which is what an intersection ratio measures. null when the
  // pane has nothing on screen (no canvases yet).
  //
  // Deliberately the same answer the observer's callback arrives at from its
  // entries - most visible of the intersecting ones - and the same answer
  // re-observing every canvas would deliver, since a fresh observation
  // reports every target at once. The 0.4 threshold decides WHEN the observer
  // is run, not which page it then picks, so leaving it out here is not a
  // second opinion about what "visible" means; it is the same opinion asked
  // at a moment when there is no crossing left to be delivered.
  //
  // Two places where "the same" is a statement about this pane rather than
  // about the two rules in general, both worth saying out loud:
  //
  //   - the ratio here is vertical overlap over height, where the observer's
  //     is an AREA ratio. They agree because a canvas is centred in the
  //     scroller and never wider than it (computeWidth subtracts the
  //     padding), so no page is ever clipped horizontally and the widths
  //     cancel. A page wider than the pane would need this to measure area.
  //   - an exact tie goes to the lower page number here (`>` keeps the first
  //     seen, and pages are walked in document order), where the observer's
  //     callback ties to the first such entry in a batch whose order is not
  //     defined. A tie means the pane is split precisely down the middle of
  //     the gap; the reader is arriving at the lower page's end rather than
  //     the upper page's start, so this is the better of the two answers
  //     anyway, and it is at least always the SAME answer.
  function visiblePage() {
    const view = container.getBoundingClientRect();
    let best = null;
    for (const canvas of container.querySelectorAll(".pdf-page")) {
      const rect = canvas.getBoundingClientRect();
      if (!rect.height) continue;
      const overlap = Math.min(rect.bottom, view.bottom) - Math.max(rect.top, view.top);
      if (overlap <= 0) continue;
      const ratio = overlap / rect.height;
      if (!best || ratio > best.ratio) best = { page: Number(canvas.dataset.page), ratio };
    }
    return best ? best.page : null;
  }

  // What the observer does with a crossing it has just been handed. Kept
  // apart from the callback so the rule can be stated once and read at the
  // one other place that has to reason about it - the re-derive above, which
  // deliberately does NOT follow it, and says why.
  function trackVisible(seen) {
    // A turn this component performed is already recorded (see intendedPage).
    // Until that scroll settles, the frames it passes through are not the
    // reader going anywhere - taking them would drag the indicator back onto
    // the page being LEFT, which is the same wrong answer by a different
    // route. The arrival itself is what hands tracking back to the reader.
    if (intendedPage !== null) {
      if (seen === intendedPage) intendedPage = null;
      return;
    }
    setPage(seen);
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

  // The inverse of positionAt: where a recorded position is on the scroller
  // NOW, at whatever height the pages are currently rendered. Both the
  // re-render's restore and the "has the pane got there yet" check in
  // onScroll ask this same question, so they ask it the same way.
  function offsetOf(position) {
    const canvas = position && container.querySelector(`[data-page="${position.page}"]`);
    if (!canvas) return null;
    return pageTop(canvas) + position.fraction * canvas.getBoundingClientRect().height;
  }

  // How far the pane still has to go to reach a recorded position, or null if
  // that position is not on the pane any more.
  //
  // Clamped to what the scroller can actually reach, because a request often
  // aims past an end: a half-page step off the last page, or - the case that
  // strands this - a whole-page turn back from page one, which asks for an
  // offset the scroller will never show because it is already showing the
  // nearest thing to it. Unclamped, those never "arrive" and the pane is
  // recorded as travelling to somewhere it cannot go.
  function travelDistance(position) {
    const want = offsetOf(position);
    if (want === null) return null;
    const max = Math.max(0, container.scrollHeight - container.clientHeight);
    return Math.abs(container.scrollTop - Math.min(Math.max(want, 0), max));
  }

  // The pane is the reader's again: whatever was asked for has landed, been
  // taken over, or expired. Every ending goes through here so none of them
  // can leave half of the state behind.
  function endTravel() {
    scrollTravelling = false;
    travelClosest = null;
    clearTimeout(travelTimer);
  }

  // The backstop, and the only ending that does not need the pane to move.
  // Armed when a scroll is ASKED FOR as well as on every scroll event, so a
  // request the browser has nothing to animate - which fires no scroll event
  // at all, and so reaches none of the other endings - still expires within
  // the same quiet interval as one that was merely never delivered.
  function armTravelBackstop() {
    clearTimeout(travelTimer);
    travelTimer = setTimeout(endTravel, 200);
  }

  // Records a scroll this component is about to perform, so a resize
  // re-render already in flight puts the reader where they just asked to be
  // rather than back where they were when it started.
  function requestScroll(position) {
    if (!position) return;
    requestedScroll = position;
    scrollRequestSeq += 1;
    // Travelling from here until it arrives, is taken over, or goes quiet -
    // see scrollTravelling's own comment for the full set, onScroll for two
    // of them, and rerenderAtWidth for what reads it (#234).
    //
    // But only if there is anywhere to travel TO. A turn onto the page
    // already shown - ArrowLeft on page one, which is an ordinary pedal tap
    // and does nothing by design - asks the scroller for the offset it is
    // already at, so the browser animates nothing and dispatches no scroll
    // event. Set here regardless, the flag would have had no event left to
    // clear it on and would have stood until the next scroll that produced
    // one: measured, a reader who taps back twice at the top of a piece and
    // then scrolls on is put back on page one's top by the next re-render,
    // however long afterwards it comes.
    const distance = travelDistance(position);
    if (distance !== null && distance > SCROLL_SLACK) {
      scrollTravelling = true;
      travelClosest = null;
      armTravelBackstop();
    } else {
      endTravel();
    }
    markUnsettled();
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
