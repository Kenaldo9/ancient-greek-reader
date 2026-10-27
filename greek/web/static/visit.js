// The public site's visitor counts (greek/web/visits.py): once a page has
// been in view for 3 seconds, or the reader clicks or taps on it, this asks
// the site for /visit once, naming the page and the site that linked to it
// (its host only). No cookie and no storage: the counts are daily totals and
// tell no visitor from another. Elsewhere /visit counts nothing.
(function () {
  "use strict";
  if (window.__greekVisit) return; // HTMX may re-run this script on history restore
  window.__greekVisit = true;
  var IN_VIEW = 3000;

  // As the page was opened: the reader moves at= in the URL as it scrolls
  var page = window.location.pathname + window.location.search;
  var from = "";
  try {
    from = document.referrer ? new URL(document.referrer).hostname : "";
  } catch (e) { /* not a URL */ }

  var sent = false;
  var timer = null;

  function send() {
    if (sent) return;
    sent = true;
    clearTimeout(timer);
    var query = "page=" + encodeURIComponent(page) + "&from=" + encodeURIComponent(from);
    // keepalive: it still goes when the click leaves the page
    fetch("/visit?" + query, { credentials: "omit", cache: "no-store", keepalive: true })
      .catch(function () { /* offline: not counted */ });
  }

  // 3 seconds in view at a stretch; a tab in the background waits
  function wait() {
    clearTimeout(timer);
    if (!sent && document.visibilityState === "visible") timer = setTimeout(send, IN_VIEW);
  }

  document.addEventListener("visibilitychange", wait);
  document.addEventListener("click", function (event) {
    if (event.isTrusted) send();
  }, true);
  wait();
})();
