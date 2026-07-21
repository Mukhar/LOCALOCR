/*
 * seek.js -- click any element with data-seek="<seconds>" to seek the
 * #source-video element and play from that timestamp.
 *
 * Delegated listener at the document root so it works for elements
 * added via HTMX swaps AFTER the initial page load. Ignores clicks
 * where no matching video is on the page (e.g. runs with no video).
 */
document.addEventListener("click", function (e) {
  var el = e.target.closest("[data-seek]");
  if (!el) return;
  var video = document.getElementById("source-video");
  if (!video) return;
  var seconds = parseFloat(el.dataset.seek);
  if (Number.isNaN(seconds)) return;
  e.preventDefault();  // stop <button>'s implicit form submit, if any
  video.currentTime = seconds;
  var p = video.play();
  // play() returns a Promise that rejects if autoplay is blocked --
  // swallow the rejection so the console stays clean.
  if (p && typeof p.catch === "function") p.catch(function () {});
  video.scrollIntoView({ behavior: "smooth", block: "center" });
});
