<script>
  import * as pdfjs from "pdfjs-dist";
  import { api } from "./api.js";

  pdfjs.GlobalWorkerOptions.workerSrc = new URL(
    "pdfjs-dist/build/pdf.worker.min.mjs",
    import.meta.url,
  ).toString();

  let { score } = $props();

  let container;
  let darkMode = $state(true);
  let pageCount = $state(0);
  let currentPage = $state(1);
  let saveTimer;

  $effect(() => {
    let cancelled = false;
    let observer;

    (async () => {
      const doc = await pdfjs.getDocument(api.fileUrl(score.id)).promise;
      if (cancelled) return;
      pageCount = doc.numPages;
      const width = Math.min(container.clientWidth - 32, 1100);
      const dpr = window.devicePixelRatio || 1;

      for (let n = 1; n <= doc.numPages; n++) {
        const page = await doc.getPage(n);
        if (cancelled) return;
        const base = page.getViewport({ scale: 1 });
        const scale = width / base.width;
        const viewport = page.getViewport({ scale: scale * dpr });

        const canvas = document.createElement("canvas");
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        canvas.style.width = `${width}px`;
        canvas.className = "pdf-page";
        canvas.dataset.page = n;
        container.appendChild(canvas);
        await page.render({ canvasContext: canvas.getContext("2d"), viewport }).promise;
      }

      observer = new IntersectionObserver(
        (entries) => {
          for (const e of entries) {
            if (e.isIntersecting) {
              currentPage = Number(e.target.dataset.page);
              clearTimeout(saveTimer);
              saveTimer = setTimeout(
                () => api.patch(score.id, { last_page: currentPage }).catch(() => {}),
                1200,
              );
            }
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
    })();

    return () => {
      cancelled = true;
      observer?.disconnect();
      clearTimeout(saveTimer);
    };
  });

  function goto(page) {
    const target = Math.max(1, Math.min(pageCount, page));
    container
      .querySelector(`[data-page="${target}"]`)
      ?.scrollIntoView({ block: "start", behavior: "smooth" });
  }

  function onKey(e) {
    if (e.key === "ArrowRight" || e.key === "PageDown" || e.key === " ") {
      e.preventDefault();
      goto(currentPage + 1);
    } else if (e.key === "ArrowLeft" || e.key === "PageUp") {
      e.preventDefault();
      goto(currentPage - 1);
    }
  }
</script>

<svelte:window onkeydown={onKey} />

<div class="wrap">
  <div class="pages" class:dark={darkMode} bind:this={container}></div>
  <div class="hud">
    <button onclick={() => goto(currentPage - 1)} title="Previous page">‹</button>
    <span>{currentPage} / {pageCount || "…"}</span>
    <button onclick={() => goto(currentPage + 1)} title="Next page">›</button>
    <button class:on={darkMode} onclick={() => (darkMode = !darkMode)} title="Invert for practice in the dark">
      ◐
    </button>
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
</style>
