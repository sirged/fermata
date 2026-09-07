// Real fret spacing (issue #287) - the one place a fret's x-position on the
// drawn neck is computed, so Neck.svelte's fret wires, inlay dots, tap
// targets and note labels can never disagree with each other about where a
// fret actually is.
//
// A real neck's frets shrink by the twelfth root of two per fret - the
// twelfth fret sits at half the (theoretical, unfretted) scale length, the
// 24th at a quarter, and so on. Unscaled (scale length = 1), fret n sits at
//
//   u(n) = 1 - 2^(-n/12)
//
// which climbs toward 1 as n grows but never reaches it - there is no fret
// at "the end of the string". Neck.svelte only ever draws a fixed number of
// frets (fretCount), and its layout - the component's outer width, the
// board rect, the margins - is unchanged by this issue: the LAST drawn fret
// must still land exactly where it always did, at the given pixel `width`.
// So u(n) is rescaled by whatever factor puts u(fretCount) at `width`:
//
//   x(n) = width * u(n) / u(fretCount)
//        = width * (1 - 2^(-n/12)) / (1 - 2^(-fretCount/12))
//
// x(0) = 0 and x(fretCount) = width exactly (up to floating point), so the
// nut and the last fret wire are pinned at the same places the old linear
// layout put them; every fret between narrows on the way there. `width` is
// the TOTAL board width for the given fret count, not a per-fret constant -
// Neck.svelte derives it once (fretCount * a fixed pixel-per-fret unit) and
// passes it in here, rather than multiplying a per-fret width by a fret
// number the way the old linear layout did.

/** A drawn fret's x-position, in the same units as `width`, with the nut at
 * 0 and `fretCount`'s own wire landing exactly at `width`. `fret` may be
 * fractional (used for marker centres, which sit midway between two fret
 * wires) and is not required to be an integer or in range; `fretCount` is
 * floored at 1 so a neck asked to draw zero frets still has a well-defined
 * (if degenerate) scale. */
export function fretX(fret, fretCount, width) {
  const count = Math.max(1, fretCount);
  if (fret <= 0) return 0;
  const unscaled = 1 - Math.pow(2, -fret / 12);
  const unscaledTotal = 1 - Math.pow(2, -count / 12);
  return (width * unscaled) / unscaledTotal;
}

/** The centre of the fret-N marker (the fingered position between wire N-1
 * and wire N) - the open string (fret 0) is not "between" anything and is
 * handled by the caller instead, exactly as it was under linear spacing. */
export function fretMarkerX(fret, fretCount, width) {
  return (fretX(fret - 1, fretCount, width) + fretX(fret, fretCount, width)) / 2;
}

// Neck.svelte's touch-target sizing, duplicated here (not imported) because
// this module has no dependency on the component - see the module comment
// on why fretX/fretMarkerX are the one place x-position lives. These two
// numbers MUST match Neck.svelte's own MARKER_R and its hit-circle padding
// (`MARKER_R + 8`) or the clamp below stops describing what is actually
// drawn.
const MARKER_R = 14;
const MAX_HIT_R = MARKER_R + 8;

/** The radius of a fret-N marker's invisible tap target, clamped so that
 * adjacent hit circles never overlap.
 *
 * Under real (non-linear) fret spacing, marker-centre spacing shrinks toward
 * the body - by fret 22 on a 24-fret neck it is well under the fixed
 * MAX_HIT_R * 2 the old linear layout could always afford, so a fixed radius
 * makes neighbouring hit circles overlap and steals taps for the wrong fret
 * (issue #287's regression, found in review). This clamps fret N's own
 * radius to just under half of fret N's own cell width - the distance
 * between the two fret wires the marker sits between - minus a 1-unit
 * safety margin. Because that cell's far wire is shared with the NEXT
 * fret's own cell, and that neighbour is clamped by the exact same rule
 * against the same shared wire, no two adjacent markers can ever be given
 * enough radius to reach each other: each one stops at least 1 unit short
 * of the wire between them, leaving a real (if small) gap rather than a
 * point of tangency.
 *
 * Not meant to be called for fret 0 (the open string marker, drawn off the
 * fretted board entirely in NUT_GAP space) - callers keep that position's
 * fixed radius, the same way positionX() special-cases it. */
export function hitRadius(fret, fretCount, width) {
  const cellWidth = fretX(fret, fretCount, width) - fretX(fret - 1, fretCount, width);
  return Math.min(MAX_HIT_R, cellWidth / 2 - 1);
}
