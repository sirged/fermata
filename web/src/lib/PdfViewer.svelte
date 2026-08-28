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
      }
    }

    async function rerenderAtWidth(width) {
      renderedWidth = width;
      const dpr = window.devicePixelRatio || 1;
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
      // canvases changed height, so restore scroll to wherever the reader
      // was rather than let it drift to an arbitrary pixel offset
      container.querySelector(`[data-page="${currentPage}"]`)?.scrollIntoView({ block: "start" });
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
            currentPage = Number(best.target.dataset.page);
            clearTimeout(saveTimer);
            saveTimer = setTimeout(
              () => api.patch(score.id, { last_page: currentPage }).catch(() => {}),
              1200,
            );
          }
        },
        { root: container, threshold: 0.4 },
      );
      container.querySelectorAll(".pdf-page").forEach((c) => observer.observe(c));

      if (score.last_page > 1) {
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
    })();

    return () => {
      cancelled = true;
      observer?.disconnect();
      resizeObserver?.disconnect();
      clearTimeout(saveTimer);
      clearTimeout(resizeTimer);
    };
  });

  function goto(page) {
    const target = Math.max(1, Math.min(pageCount, page));
    container
      .querySelector(`[data-page="${target}"]`)
      ?.scrollIntoView({ block: "start", behavior: "smooth" });
  }

  function turn(dir) {
    if (gigMode && halfPage) {
      // step off the current page's actual rendered height (plus its share
      // of the gap) rather than the viewport, so repeated half-turns track
      // bar lines instead of drifting against page/container padding
      const current = container.querySelector(`[data-page="${currentPage}"]`);
      const step = current ? current.getBoundingClientRect().height + 18 : container.clientHeight;
      container.scrollBy({ top: dir * step * 0.5, behavior: "smooth" });
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
