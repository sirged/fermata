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
