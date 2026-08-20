/** Index of the segment the playhead is inside: the last segment whose start_ms <= t.
 *  Returns -1 before the first segment. Binary search — called every animation frame. */
export function findActiveIndex(startsMs: number[], tMs: number): number {
  let lo = 0;
  let hi = startsMs.length - 1;
  let ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (startsMs[mid] <= tMs) {
      ans = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return ans;
}
