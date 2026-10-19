/* Latest-wins guard for async work in the dashboard (no dependencies).
 *
 * A page load or a validation request can be slower than the next one the user starts. Without a guard the slow
 * response arrives last and overwrites the newer view. Take a token before awaiting; apply the result only if
 * `isCurrent(token)` is still true afterwards.
 *
 *     const guard = AgLatest.latest();
 *     const token = guard.begin();
 *     const data = await fetchSomething();
 *     if (!guard.isCurrent(token)) return;   // a newer request started meanwhile: drop this result
 */
(function (root) {
  "use strict";

  function latest() {
    let current = 0;
    return {
      begin() { return ++current; },
      isCurrent(token) { return token === current; },
    };
  }

  const api = { latest };
  root.AgLatest = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
