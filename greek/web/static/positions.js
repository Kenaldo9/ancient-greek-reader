// Reading positions, kept in this browser only: the line each text was last
// read at (localStorage "greek.position.<text id>"). The reader saves them
// (reader.js); the Library's Start reading buttons and the reader's text
// links open a text where this browser left it. Without storage (a private
// window, blocked site data) texts open at their start.
(function () {
  "use strict";
  var PREFIX = "greek.position.";

  function get(textId) {
    try {
      var saved = JSON.parse(localStorage.getItem(PREFIX + textId));
      return saved && saved.ref ? saved : null;
    } catch (e) {
      return null;
    }
  }

  function set(textId, line, ref) {
    try {
      localStorage.setItem(PREFIX + textId, JSON.stringify({ line: line, ref: ref }));
    } catch (e) { /* storage blocked or full */ }
  }

  // A link with data-resume="<text id>" opens its text at the saved place;
  // one with data-resume-label also says where ("Continue at 5.123")
  function resumeLinks(root) {
    Array.prototype.forEach.call((root || document).querySelectorAll("a[data-resume]"), function (a) {
      var saved = get(a.dataset.resume);
      if (!saved) return;
      a.setAttribute("href", "/texts/" + a.dataset.resume + "/read?at=" + encodeURIComponent(saved.ref));
      if (a.hasAttribute("data-resume-label")) a.textContent = "Continue at " + saved.ref;
    });
  }

  window.greekPositions = { get: get, set: set, resumeLinks: resumeLinks };
  resumeLinks();
})();
