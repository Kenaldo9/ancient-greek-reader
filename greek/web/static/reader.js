// Reader interactions: line selection, word details (their dictionary entry
// and the word a role names), interlinear glosses, passage tabs, sentence
// structure in Grammar, a phone's study sheet, the reading position and the
// search field. The server renders every state from saved data; this file
// only decides which fragment to ask for.
//
// All listeners are delegated on `document`, so they keep working after HTMX
// swaps the text pane, the word strip or the passage box.
(function () {
  "use strict";
  if (window.__greekReader) return; // HTMX may re-run this script on history restore
  window.__greekReader = true;

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  // Keep these breakpoints in sync with the tablet and laptop layouts in style.css.
  var compact = window.matchMedia("(max-width: 1500px)");
  var laptop = window.matchMedia("(max-width: 2100px)");
  // iPad and phone: one slim bar; Go to lives in the drawer
  var slimBar = window.matchMedia("(max-width: 1500px)");
  // Phone: Interlinear lives in the drawer too
  var phone = window.matchMedia("(max-width: 700px)");
  // Phone, and a short screen with the study panel under the text (a phone
  // in landscape): the study panel is a sheet, open while a word or lines
  // are tapped
  var sheet = window.matchMedia("(max-width: 700px), (max-width: 1100px) and (max-height: 500px)");
  var coarse = window.matchMedia("(pointer: coarse)");
  var reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  var pointerType = null;
  var drawerFocus = null;

  var state = {
    textId: null,
    anchor: null, // line where the selection started; Shift+click extends from here
    start: null,
    end: null,
    refs: {}, // line_number → reference, for lines seen while selecting
    word: null, // { line, i } of the word shown in the word strip
    touchAnchor: null, // first tap; the next tap completes the range
    studyTab: "fluent",
    dictOpen: false, // the full LSJ entry, outside the tablet's Dictionary tab
    passageExpanded: false, // the passage box fills a laptop's study column
    sheetOpen: false, // a phone's study panel
    sheetFrom: null, // the word or line number that opened it, for focus
  };

  function init() {
    var reader = $("#reader");
    if (!reader) return;
    state.textId = reader.dataset.textId;
    if (resumeReading()) return;
    state.touchAnchor = null;
    closeNav(false);
    placeHeaderTools();
    var box = $("#passage-box");
    if (box && box.dataset.start) {
      state.start = state.anchor = +box.dataset.start;
      state.end = +box.dataset.end;
      state.refs[state.start] = box.dataset.refStart;
      state.refs[state.end] = box.dataset.refEnd;
    } else {
      state.start = state.end = state.anchor = null;
    }
    try { state.passageExpanded = localStorage.getItem("greek.passageExpanded") === "1"; } catch (e) { /* private mode */ }
    // a phone's sheet shows lines the page was opened with
    state.sheetOpen = state.start !== null;
    state.sheetFrom = null;
    paintSheet();
    paintSelection();
    paintWord();
    restoreTab();
    showOpened();
    resetPosition();
    // a Go to the server couldn't follow: show its message
    if (slimBar.matches && $("#goto [aria-invalid='true']")) openGoto();
  }

  // ------------------------------------------------------------- helpers

  function setCookie(name, value) {
    document.cookie = name + "=" + encodeURIComponent(value) + "; path=/; max-age=31536000; samesite=lax";
  }

  function isTyping(el) {
    return el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName);
  }

  function refRange(a, b) {
    return a === b ? a : a + "–" + b;
  }

  // ----------------------------------------------------------- selection

  // b is the line clicked
  function select(a, b) {
    state.start = Math.min(a, b);
    state.end = Math.max(a, b);
    // A new selection shows its passage, which the laptop's open LSJ entry hides
    if (laptop.matches && !compact.matches) state.dictOpen = false;
    paintSelection();
    updateUrl();
    if (compact.matches && /^(word|dictionary)$/.test(state.studyTab)) state.studyTab = "fluent";
    syncStudyPanel();
    openSheet($('#text-lines .line[data-line="' + b + '"] .line-ref'));
    loadPassage();
  }

  // On a phone nothing stays selected without the sheet: it closes
  function clearSelection() {
    if (sheet.matches && state.sheetOpen) return closeSheet(false);
    unselect();
  }

  function unselect() {
    state.start = state.end = state.anchor = null;
    state.touchAnchor = null;
    paintSelection();
    updateUrl();
    loadPassage();
  }

  function onLineClick(line, extend, touch) {
    var n = +line.dataset.line;
    state.refs[n] = line.dataset.ref;
    if (touch) {
      if (state.touchAnchor !== null) {
        var anchor = state.touchAnchor;
        state.touchAnchor = null;
        if (n === anchor) return clearSelection();
        state.anchor = anchor;
        return select(anchor, n);
      }
      state.touchAnchor = state.anchor = n;
      return select(n, n);
    }
    state.touchAnchor = null;
    if (extend && state.anchor !== null) {
      select(state.anchor, n);
    } else if (state.start === n && state.end === n) {
      clearSelection(); // clicking the only selected line again deselects it
    } else {
      state.anchor = n;
      select(n, n);
    }
  }

  function paintSelection() {
    var has = state.start !== null;
    $$("#text-lines .line").forEach(function (el) {
      var n = +el.dataset.line;
      var on = has && n >= state.start && n <= state.end;
      el.classList.toggle("is-selected", on);
      var btn = $(".line-ref", el);
      if (btn) btn.setAttribute("aria-pressed", on ? "true" : "false");
    });
    var chip = $("#selection-chip");
    if (chip) {
      chip.hidden = !has;
      if (has) $("#selection-refs").textContent = refRange(state.refs[state.start] || state.start, state.refs[state.end] || state.end);
    }
    var hint = $("#selection-hint");
    if (hint) {
      hint.hidden = state.touchAnchor === null;
      if (!hint.hidden) hint.textContent = "Start: " + (state.refs[state.touchAnchor] || state.touchAnchor) + ". Tap the last line number to finish the range, or tap the start again to clear.";
    }
    paintColumn();
  }

  function updateUrl() {
    var url = new URL(window.location.href);
    if (state.start !== null) {
      url.searchParams.set("lines", (state.refs[state.start] || state.start) + "-" + (state.refs[state.end] || state.end));
    } else {
      url.searchParams.delete("lines");
    }
    window.history.replaceState(window.history.state, "", url);
  }

  function loadPassage() {
    var params = new URLSearchParams();
    if (state.start !== null) params.set("lines", state.start + "-" + state.end);
    load("passage", "/texts/" + state.textId + "/passage?" + params, "#passage-box", "outerHTML");
  }

  // A pane's GET goes out at once, from a hidden element of its own: htmx
  // queues requests from one element, and a queued request later swapped
  // into a pane the earlier response had replaced (htmx:swapError), after
  // which nothing loaded. Only the pane's newest response is swapped in, so
  // a slow older one can't overwrite it.
  var newest = {}; // pane → its newest request
  var panes = new WeakMap(); // request → pane

  function load(name, url, selector, swap) {
    var target = $(selector);
    if (!target) return;
    var source = document.createElement("span");
    source.hidden = true;
    document.body.appendChild(source);
    source.addEventListener("htmx:beforeSend", function (e) {
      newest[name] = e.detail.xhr;
      panes.set(e.detail.xhr, name);
    });
    // beforeSwap fires on the target, which may be out of the page by then
    target.addEventListener("htmx:beforeSwap", keepNewest);
    var done = function () { source.remove(); };
    htmx.ajax("GET", url, { source: source, target: target, swap: swap }).then(done, done);
  }

  function keepNewest(e) {
    var name = panes.get(e.detail.xhr);
    if (!name) return;
    if (newest[name] !== e.detail.xhr || !document.body.contains(e.detail.target)) e.detail.shouldSwap = false;
  }

  // ---------------------------------------------------------------- words

  function showWord(line, i) {
    state.word = { line: line, i: i };
    paintWord();
    if (state.studyTab !== "dictionary") state.studyTab = "word"; // a tablet keeps the Dictionary tab
    syncStudyPanel();
    openSheet($('#text-lines .line[data-line="' + line + '"] .w[data-i="' + i + '"]'));
    var params = new URLSearchParams({ line: line, i: i });
    load("word", "/texts/" + state.textId + "/word?" + params, "#word-strip", "outerHTML");
  }

  function paintWord() {
    $$(".w.is-active, .wt-row.is-active, .st-item.is-active").forEach(function (el) { el.classList.remove("is-active"); });
    paintHead();
    if (!state.word) return;
    var sel = '[data-line="' + state.word.line + '"][data-i="' + state.word.i + '"]';
    var word = $('#text-lines .line[data-line="' + state.word.line + '"] .w[data-i="' + state.word.i + '"]');
    if (word) word.classList.add("is-active");
    // its row in Grammar, in order and in the structure; the one shown scrolls into view
    var row = $(".wt-row" + sel);
    var item = $(".st-item" + sel);
    if (row) row.classList.add("is-active");
    if (item) item.classList.add("is-active");
    [row, item && $(".st-row", item)].forEach(function (el) {
      if (el && el.getClientRects().length) el.scrollIntoView({ block: "nearest" });
    });
  }

  // ---------------------------------------------------------------- syntax

  // A word's Syntax block names the word its role points to (.syn-ref). On a
  // monitor the block is under the parsing; on a laptop or tablet Parsing
  // and Syntax are tabs, and every word opens on Parsing. The word it names
  // is outlined in the text while the block is shown (not behind the
  // Parsing tab, a tablet's other study tabs or a laptop's open LSJ entry),
  // filled while its name is hovered or focused, and clicking the name
  // brings it into view.
  function syntaxBlock() {
    var strip = $("#word-strip");
    var parse = $("#word-strip .ws-parse.has-syntax");
    // the strip of the word last clicked, not one on its way out
    if (!parse || !state.word || +strip.dataset.line !== state.word.line || +strip.dataset.i !== state.word.i) return null;
    return parse;
  }

  function headOf(ref) {
    return ref ? $('#text-lines .line[data-line="' + ref.dataset.line + '"] .w[data-i="' + ref.dataset.i + '"]') : null;
  }

  function paintHead() {
    $$("#text-lines .w.is-head, #text-lines .w.is-pointed").forEach(function (el) { el.classList.remove("is-head", "is-pointed"); });
    var parse = syntaxBlock();
    var block = parse && $("#ws-spanel", parse);
    if (!block || !block.getClientRects().length) return;
    var head = headOf($(".syn-ref", block));
    if (head) head.classList.add("is-head");
  }

  function paintParseTabs() {
    var parse = $("#word-strip .ws-parse.has-syntax");
    if (!parse) return;
    var syntax = parse.classList.contains("show-syntax");
    $$(".ws-ptab", parse).forEach(function (t) {
      var on = (t.dataset.ptab === "syntax") === syntax;
      t.setAttribute("aria-selected", on ? "true" : "false");
      t.tabIndex = on ? 0 : -1;
    });
    // tab panels only where they are tabs
    [["#ws-ppanel", "ws-ptab-parsing"], ["#ws-spanel", "ws-ptab-syntax"]].forEach(function (p) {
      var panel = $(p[0], parse);
      if (!panel) return;
      if (laptop.matches) {
        panel.setAttribute("role", "tabpanel");
        panel.setAttribute("aria-labelledby", p[1]);
      } else {
        panel.removeAttribute("role");
        panel.removeAttribute("aria-labelledby");
      }
    });
  }

  function showParseTab(tab, focus) {
    var parse = $("#word-strip .ws-parse.has-syntax");
    if (!parse) return;
    parse.classList.toggle("show-syntax", tab === "syntax");
    paintParseTabs();
    paintHead();
    if (focus) $('.ws-ptab[data-ptab="' + tab + '"]', parse).focus();
  }

  // The text pane scrolls only when the word is off screen
  function showHead(ref) {
    var head = headOf(ref);
    var scroller = $("#text-pane .text-scroll");
    if (!head || !scroller) return;
    var r = head.getBoundingClientRect(), s = scroller.getBoundingClientRect();
    if (r.top < s.top || r.bottom > s.bottom) head.closest(".line").scrollIntoView({ block: "center" });
  }

  function pointHead(e, on) {
    var ref = e.target.closest && e.target.closest("#word-strip .syn-ref");
    var head = headOf(ref);
    if (head) head.classList.toggle("is-pointed", on);
  }

  function moveWordFocus(from, step) {
    var words = $$("#text-lines .w");
    var next = words[words.indexOf(from) + step];
    if (next) next.focus();
  }

  // ----------------------------------------------------------- dictionary

  // The word's full LSJ entry. On a monitor it always shows, and opening it
  // makes the word pane taller; on a laptop it fills the study column and the
  // passage box folds into a bar; on a tablet it is the Dictionary tab.
  function hasLsj() {
    return !!$("#word-strip .ws-lsj");
  }

  function dictShown() {
    if (!hasLsj()) return false;
    return compact.matches ? state.studyTab === "dictionary" : state.dictOpen;
  }

  // The open LSJ entry and the expanded passage box: which box fills the
  // study column (on a monitor the entry makes the word pane taller)
  function paintColumn() {
    var reader = $("#reader");
    if (!reader) return;
    var on = dictShown();
    reader.classList.toggle("dict-open", on);
    $$("#word-strip .ws-lsj-toggle").forEach(function (b) { b.setAttribute("aria-expanded", on ? "true" : "false"); });
    var refs = $("#passage-bar-refs");
    if (refs) refs.textContent = state.start === null ? "" : refRange(state.refs[state.start] || state.start, state.refs[state.end] || state.end);
    var wide = passageExpanded();
    reader.classList.toggle("passage-expanded", wide);
    $$("#passage-box .pb-expand").forEach(function (b) { b.setAttribute("aria-expanded", wide ? "true" : "false"); });
    paintHead(); // the Syntax block may have been covered or uncovered
    restoreGrammarPlace(); // and the Grammar tab hidden or shown
  }

  function toggleDict() {
    if (compact.matches) return showStudyTab("dictionary", true);
    state.dictOpen = !state.dictOpen;
    paintColumn();
  }

  function closeDict() {
    state.dictOpen = false;
    paintColumn();
    var toggle = $("#word-strip .ws-lsj-toggle");
    if (toggle && toggle.getClientRects().length) toggle.focus();
  }

  // ----------------------------------------------------- expanded passage

  // On a laptop the passage box can fill the study column instead: the word
  // folds into a line or two above it (its parsing as a summary) and stays
  // folded as other words are clicked, until Show word or Collapse. The
  // choice is remembered, like the tab. It needs selected lines, and an open
  // LSJ entry covers it.
  function passageExpanded() {
    return state.passageExpanded && laptop.matches && !compact.matches && !!$("#passage-box[data-start]") && !dictShown();
  }

  function expandPassage(on) {
    state.passageExpanded = on;
    try { localStorage.setItem("greek.passageExpanded", on ? "1" : "0"); } catch (e) { /* private mode */ }
    paintColumn();
  }

  function showWordPane() {
    expandPassage(false);
    var toggle = $("#passage-box .pb-expand");
    if (toggle && toggle.getClientRects().length) toggle.focus();
  }

  // --------------------------------------------------------------- search

  // Bring the line the page was opened at (?at=, the start of a ?lines=
  // selection, Go to) to the top of the text pane. A line opened
  // from a search result (.is-hit) is centered instead.
  var opened = { top: null };

  function showOpened() {
    opened.top = null;
    var hit = $("#text-lines .line.is-hit");
    if (hit) return hit.scrollIntoView({ block: "center" });
    var lines = $("#text-lines");
    var scroller = $("#text-pane .text-scroll");
    var at = lines && $('#text-lines .line[data-line="' + lines.dataset.at + '"]');
    if (!at || !scroller) return;
    // The line's top at the pane's top edge, so no cut-off line shows above
    // it; the page's first line keeps the padding above it
    if (at === $("#text-lines .line")) scroller.scrollTop = 0;
    else scroller.scrollTop += at.getBoundingClientRect().top - scroller.getBoundingClientRect().top;
    opened.top = scroller.scrollTop;
  }

  // Web fonts change line heights after the first layout; place the line
  // again unless the reader has scrolled since.
  if (document.fonts) {
    document.fonts.ready.then(function () {
      var scroller = $("#text-pane .text-scroll");
      if (scroller && opened.top !== null && scroller.scrollTop === opened.top) showOpened();
    });
  }

  // ------------------------------------------------------------- position

  // The reading position follows the scroll. The toolbar names the line at
  // the top of the text; once scrolling has stopped for a second, the URL's
  // at= (for a reload, a copied link or Back), the search link, the Go to
  // box and the saved position move to it. The position is saved in this
  // browser only (positions.js): opening a book saves its first line shown,
  // and leaving the page saves it once more.
  var position = { line: null, ref: null, saved: null, timer: null, frame: 0 };

  // A text opened without a place (/texts/12/read: Go to "Odyssey", a
  // typed address) continues where this browser last read it
  function resumeReading() {
    var url = new URL(window.location.href);
    if (["at", "lines", "q", "hit", "page"].some(function (k) { return url.searchParams.has(k); })) return false;
    var saved = window.greekPositions && window.greekPositions.get(state.textId);
    if (!saved) return false;
    url.searchParams.set("at", saved.ref);
    window.location.replace(url.pathname + url.search);
    return true;
  }

  function resetPosition() {
    clearTimeout(position.timer);
    var lines = $("#text-lines");
    var at = lines && $('#text-lines .line[data-line="' + lines.dataset.at + '"]');
    position.line = at ? +at.dataset.line : null;
    position.ref = at ? at.dataset.ref : null;
    position.saved = null;
    var field = $("#goto input[name='q']");
    if (field) field.dataset.shown = field.value;
    trackTop();
    storePosition();
  }

  // The first line that is at least half on screen
  function topLine() {
    var scroller = $("#text-pane .text-scroll");
    var lines = $("#text-lines");
    var rows = lines ? lines.children : [];
    if (!scroller || !rows.length) return null;
    var edge = scroller.getBoundingClientRect().top;
    var lo = 0, hi = rows.length - 1;
    while (lo < hi) {
      var mid = (lo + hi) >> 1;
      var r = rows[mid].getBoundingClientRect();
      if ((r.top + r.bottom) / 2 > edge) hi = mid;
      else lo = mid + 1;
    }
    return rows[lo].classList.contains("line") ? rows[lo] : null;
  }

  function trackTop() {
    var line = topLine();
    if (!line) return;
    position.line = +line.dataset.line;
    position.ref = line.dataset.ref;
    var range = $("#pane-range");
    if (range) range.textContent = position.ref + " / " + range.dataset.end;
  }

  function savePosition() {
    clearTimeout(position.timer);
    if (position.line === null || !state.textId) return;
    followPosition();
    storePosition();
  }

  function storePosition() {
    if (position.line === null || position.line === position.saved || !state.textId || !window.greekPositions) return;
    position.saved = position.line;
    window.greekPositions.set(state.textId, position.line, position.ref);
  }

  // The URL, the search link and the Go to box name the line at the top
  function followPosition() {
    if (position.ref === null) return;
    var url = new URL(window.location.href);
    if (url.searchParams.get("at") !== position.ref || url.searchParams.has("hit") || url.searchParams.has("page")) {
      url.searchParams.set("at", position.ref);
      url.searchParams.delete("hit"); // the search hit is no longer where you are
      url.searchParams.delete("page");
      window.history.replaceState(window.history.state, "", url);
    }
    $$("[data-follows-position]").forEach(function (a) {
      var link = new URL(a.href);
      link.searchParams.set("at", position.ref);
      a.setAttribute("href", link.pathname + link.search);
    });
    var at = $("#goto input[name='at']");
    if (at) at.value = position.ref;
    // the Go to box, unless something else has been typed into it
    var field = $("#goto input[name='q']");
    if (field && field.dataset.title && field !== document.activeElement && field.value === field.dataset.shown) {
      field.value = field.dataset.shown = field.dataset.title + " " + position.ref;
    }
  }

  // Scroll events don't bubble; catch the text pane's (and Grammar's) on the way down
  document.addEventListener("scroll", function (e) {
    var t = e.target;
    if (t.id === "panel-words") return keepGrammarPlace(t);
    if (!t.classList || !t.classList.contains("text-scroll") || !$("#text-lines")) return;
    if (!position.frame) {
      position.frame = requestAnimationFrame(function () {
        position.frame = 0;
        trackTop();
      });
    }
    clearTimeout(position.timer);
    position.timer = setTimeout(savePosition, 1000);
  }, true);

  // Opening another book (or search): the URL being left keeps the line you
  // were at, for Back. Back itself leaves the pending save behind.
  document.body.addEventListener("htmx:beforeHistoryUpdate", function () {
    clearTimeout(position.timer);
    if (!$("#text-lines")) return;
    trackTop();
    followPosition();
  });
  window.addEventListener("popstate", function () { clearTimeout(position.timer); });

  window.addEventListener("pagehide", storePosition);

  // htmx reads a link's URL when the page loads; one that follows the
  // position asks for the line at the top now, even before it is saved
  document.body.addEventListener("htmx:configRequest", function (e) {
    var elt = e.detail.elt;
    if (!elt || !elt.hasAttribute || !elt.hasAttribute("data-follows-position")) return;
    if ($("#text-lines")) {
      trackTop();
      followPosition();
    }
    e.detail.path = elt.getAttribute("href");
  });

  // Page snapshots htmx kept before the reader opted out of them
  // (hx-history="false" in reader.html): Back must not bring one back
  try { localStorage.removeItem("htmx-history-cache"); } catch (e) { /* storage blocked */ }

  function clearSearch(field) {
    field.value = "";
    field.focus();
  }

  // ----------------------------------------------------------------- tabs

  // Whether the passage box has this tab and it is enabled: Literal needs
  // the study data's translations; Grammar the treebank's words. The
  // Dictionary tab needs a word with an LSJ entry.
  function tabAvailable(tab) {
    if (tab === "dictionary") return hasLsj();
    var button = $('#passage-box .pb-tab[data-tab="' + tab + '"]');
    return !!button && !button.disabled;
  }

  // Tablet tabs live outside the HTMX fragments, so Word and the passage can
  // share a single panel without duplicating saved content or polling jobs.
  function syncStudyPanel() {
    var box = $("#passage-box");
    var word = $("#panel-word");
    if (!box || !word) return;
    var tabbed = box.classList.contains("has-tabs");
    if (state.studyTab === "dictionary" && !tabAvailable("dictionary")) state.studyTab = "word";
    if (/^(literal|words)$/.test(state.studyTab) && !tabAvailable(state.studyTab)) state.studyTab = "fluent";
    var wordOn = compact.matches && /^(word|dictionary)$/.test(state.studyTab);
    word.hidden = compact.matches && !wordOn;
    box.hidden = wordOn;
    if (compact.matches) {
      word.setAttribute("role", "tabpanel");
      word.setAttribute("aria-labelledby", "reader-tab-" + (state.studyTab === "dictionary" ? "dictionary" : "word"));
      word.tabIndex = 0;
    } else {
      word.removeAttribute("role");
      word.removeAttribute("aria-labelledby");
      word.removeAttribute("tabindex");
    }
    if (compact.matches && !tabbed) {
      box.setAttribute("role", "tabpanel");
      box.setAttribute("aria-labelledby", "reader-tab-fluent");
    } else {
      box.removeAttribute("role");
      box.removeAttribute("aria-labelledby");
    }
    $$(".reader-tabs [data-reader-tab]").forEach(function (t) {
      var tab = t.dataset.readerTab;
      t.disabled = /^(literal|words|dictionary)$/.test(tab) && !tabAvailable(tab);
      var on = tab === state.studyTab;
      t.setAttribute("aria-selected", on ? "true" : "false");
      t.tabIndex = on ? 0 : -1;
      if (tab === "fluent") t.setAttribute("aria-controls", tabbed ? "panel-fluent" : "passage-box");
      if (tab === "words") {
        var original = $("#tab-words");
        t.textContent = original ? original.textContent : "Grammar";
      }
    });
    $$("#passage-box .pb-tab").forEach(function (t) {
      t.tabIndex = !compact.matches && t.getAttribute("aria-selected") === "true" ? 0 : -1;
      var panel = document.getElementById(t.getAttribute("aria-controls"));
      if (panel) panel.setAttribute("aria-labelledby", compact.matches ? "reader-tab-" + t.dataset.tab : t.id);
    });
    paintColumn();
  }

  function showStudyTab(tab, focus) {
    var was = state.studyTab;
    if (tab !== "word" && tab !== "dictionary") showTab(tab, false);
    state.studyTab = tab;
    syncStudyPanel();
    // Word and Dictionary share a box: the other one starts at its top (a
    // phone scrolls down to "Full LSJ entry")
    var wordBox = /^(word|dictionary)$/;
    if (was !== tab && wordBox.test(was) && wordBox.test(tab)) $("#panel-word").scrollTop = 0;
    if (focus) $("#reader-tab-" + tab).focus();
  }

  function showTab(tab, focus) {
    var tabs = $$("#passage-box .pb-tab");
    if (!tabs.length) return syncStudyPanel();
    var current = tabs.filter(function (t) { return t.dataset.tab === tab && !t.disabled; })[0] || tabs[0];
    tabs.forEach(function (t) {
      var on = t === current;
      t.setAttribute("aria-selected", on ? "true" : "false");
      t.tabIndex = on ? 0 : -1;
      var panel = document.getElementById(t.getAttribute("aria-controls"));
      if (panel) panel.hidden = !on;
    });
    if (focus) current.focus();
    // Remember the choice, but not a fallback (Literal without a translation)
    if (current.dataset.tab === tab) {
      try { localStorage.setItem("greek.passageTab", tab); } catch (e) { /* private mode */ }
    }
    if (!/^(word|dictionary)$/.test(state.studyTab)) state.studyTab = current.dataset.tab;
    syncStudyPanel();
    paintWord();
  }

  function restoreTab() {
    var tab = null;
    try { tab = localStorage.getItem("greek.passageTab"); } catch (e) { /* private mode */ }
    showTab(tab || "fluent", false);
    restoreWordsView();
  }

  // ---------------------------------------------------- sentence structure

  // Grammar shows the words in order (the table, with each word's role) or
  // their structure (each treebank sentence as an outline: a tree whose rows
  // open their word, and fold when three rows or more hang under them).
  // The choice is remembered, like the tab. Hovering or focusing a row
  // lights its word and the words under it in the text.
  var structure = { hovered: null };

  // Each view keeps its place (how far it is scrolled) while the same lines
  // stay selected. Hiding the tab (the Word tab, another passage tab, a
  // laptop's LSJ entry) may lose a box's scroll, and both views
  // scroll the same box, so the place is put back when the tab or the view
  // shows again. A new selection starts at the top.
  var grammar = { lines: null, places: {}, shown: null };

  function grammarPlaces() {
    var box = $("#passage-box[data-start]");
    var lines = box ? box.dataset.start + "-" + box.dataset.end : null;
    if (lines !== grammar.lines) {
      grammar.lines = lines;
      grammar.places = {};
    }
    return grammar.places;
  }

  function wordsView() {
    var on = $("#passage-box [data-words-view][aria-checked='true']");
    return on ? on.dataset.wordsView : "order";
  }

  function keepGrammarPlace(panel) {
    if (!panel.getClientRects().length) return; // hidden: its scroll may be gone
    grammarPlaces()[wordsView()] = panel.scrollTop;
    grammar.shown = panel;
  }

  // After anything that may show or hide the tab
  function restoreGrammarPlace() {
    var panel = $("#panel-words");
    var shown = panel && panel.getClientRects().length ? panel : null;
    if (shown && grammar.shown !== shown) shown.scrollTop = grammarPlaces()[wordsView()] || 0;
    grammar.shown = shown;
  }

  function showWordsView(view, focus) {
    var radios = $$("#passage-box [data-words-view]");
    if (!radios.length) return;
    var current = radios.filter(function (r) { return r.dataset.wordsView === view; })[0] || radios[0];
    var panel = $("#panel-words");
    var was = wordsView();
    var shown = !!panel && grammar.shown === panel;
    if (shown) grammarPlaces()[was] = panel.scrollTop;
    radios.forEach(function (r) {
      var on = r === current;
      r.setAttribute("aria-checked", on ? "true" : "false");
      r.tabIndex = on ? 0 : -1;
    });
    $$("#panel-words [data-view]").forEach(function (el) { el.hidden = el.dataset.view !== current.dataset.wordsView; });
    if (shown && was !== current.dataset.wordsView) panel.scrollTop = grammarPlaces()[current.dataset.wordsView] || 0;
    if (focus) current.focus();
    try { localStorage.setItem("greek.wordsView", current.dataset.wordsView); } catch (e) { /* private mode */ }
    paintWord();
  }

  function restoreWordsView() {
    var view = null;
    try { view = localStorage.getItem("greek.wordsView"); } catch (e) { /* private mode */ }
    showWordsView(view || "order", false);
  }

  function treeOf(item) {
    return item.closest("[role='tree']");
  }

  // The rows shown: not under a folded row
  function shownItems(tree) {
    return $$("[role='treeitem']", tree).filter(function (item) {
      var folded = item.parentElement.closest("[aria-expanded='false']");
      return !folded || !tree.contains(folded);
    });
  }

  function focusItem(item) {
    if (item) item.focus(); // focusin moves the tree's tab stop to it
  }

  function foldItem(item, fold) {
    if (item.hasAttribute("aria-expanded")) item.setAttribute("aria-expanded", fold ? "false" : "true");
  }

  function openItem(item) {
    if (item.dataset.line) showWord(+item.dataset.line, +item.dataset.i);
  }

  function lightWords(item) {
    $$("#text-lines .w.st-own, #text-lines .w.st-dep").forEach(function (el) { el.classList.remove("st-own", "st-dep"); });
    if (!item || !item.isConnected) return;
    var mark = function (el, cls) {
      (el.dataset.words || "").split(" ").forEach(function (place) {
        var at = place.split(":");
        var w = at.length === 2 && $('#text-lines .line[data-line="' + at[0] + '"] .w[data-i="' + at[1] + '"]');
        if (w) w.classList.add(cls);
      });
    };
    $$("[role='treeitem']", item).forEach(function (el) { mark(el, "st-dep"); });
    mark(item, "st-own");
  }

  function focusedItem() {
    var el = document.activeElement;
    return el && el.matches && el.matches("#passage-box [role='treeitem']") ? el : null;
  }

  function treeKey(item, key) {
    var items = shownItems(treeOf(item));
    var at = items.indexOf(item);
    if (key === "Enter" || key === " ") return openItem(item);
    if (key === "ArrowDown") return focusItem(items[at + 1]);
    if (key === "ArrowUp") return focusItem(items[at - 1]);
    if (key === "Home") return focusItem(items[0]);
    if (key === "End") return focusItem(items[items.length - 1]);
    var expanded = item.getAttribute("aria-expanded");
    if (key === "ArrowRight") {
      if (expanded === "false") return foldItem(item, false);
      return focusItem($(":scope > [role='group'] > [role='treeitem']", item));
    }
    if (expanded === "true") return foldItem(item, true);
    focusItem(item.parentElement.closest("[role='treeitem']"));
  }

  function toggleSentenceText(button) {
    var on = button.closest(".st-sentence").classList.toggle("shows-text");
    button.setAttribute("aria-expanded", on ? "true" : "false");
    button.textContent = on ? "Hide the sentence" : "Show the sentence";
  }

  // ----------------------------------------------------------- the sheet

  // On a phone (and a phone in landscape) the text takes the screen and the
  // study panel is a sheet under it: tapping a word or a line number opens
  // it, and the text above it gets shorter, so the word or line tapped is
  // brought up out from under it. Its handle, a swipe down or Escape closes
  // it, and what was tapped is let go: the word is no longer outlined and
  // the lines no longer selected.
  function openSheet(from) {
    if (from) state.sheetFrom = from;
    var opening = !state.sheetOpen;
    state.sheetOpen = true;
    if (opening) paintSheet();
    if (!sheet.matches) return;
    keepInView(from);
    // it slides up; a page opened with lines selected has it there already
    var side = $("#reader-side");
    if (opening && side.animate && !reducedMotion.matches) {
      side.animate([{ transform: "translateY(100%)" }, { transform: "none" }], { duration: 200, easing: "ease-out" });
    }
  }

  function closeSheet(returnFocus) {
    var from = state.sheetFrom;
    state.sheetOpen = false;
    state.sheetFrom = null;
    paintSheet();
    state.word = null;
    paintWord();
    if (state.start !== null || state.touchAnchor !== null) unselect();
    if (returnFocus && from && from.isConnected) from.focus({ preventScroll: true });
  }

  function paintSheet() {
    var reader = $("#reader");
    if (!reader) return;
    reader.classList.toggle("sheet-open", state.sheetOpen);
    paintColumn(); // the Syntax block and Grammar's place show or hide with it
  }

  // The word or line number tapped (or a word's row in Grammar) stays in
  // view in the text, above the sheet and the range hint over its bottom
  function keepInView(el) {
    var scroller = $("#text-pane .text-scroll");
    if (!el || !scroller) return;
    var r = el.getBoundingClientRect(), s = scroller.getBoundingClientRect();
    var hint = $("#selection-hint");
    var bottom = hint && !hint.hidden ? Math.min(s.bottom, hint.getBoundingClientRect().top) : s.bottom;
    var margin = Math.min(48, (bottom - s.top) / 4); // a line's room or so
    if (r.bottom > bottom) scroller.scrollTop += Math.min(r.bottom - bottom + margin, r.top - s.top);
    else if (r.top < s.top) scroller.scrollTop -= s.top - r.top + margin;
  }

  // A swipe down on the handle or the tabs closes the sheet; the sheet
  // follows the finger (or mouse) until it is let go
  var drag = null;
  var dragged = false; // the click a swipe ends with is not a tap on a tab

  document.addEventListener("pointerdown", function (e) {
    dragged = false;
    if (!sheet.matches || !state.sheetOpen || !e.isPrimary || e.button > 0) return;
    if (!e.target.closest || !e.target.closest("#reader-side .sheet-grip, #reader-side .reader-tabs")) return;
    drag = { id: e.pointerId, y: e.clientY, t: e.timeStamp, dy: 0 };
  });

  document.addEventListener("pointermove", function (e) {
    if (!drag || e.pointerId !== drag.id) return;
    drag.dy = Math.max(0, e.clientY - drag.y);
    var side = $("#reader-side");
    side.style.transition = "none";
    side.style.transform = drag.dy ? "translateY(" + drag.dy + "px)" : "";
  });

  function endDrag(e) {
    if (!drag || e.pointerId !== drag.id) return;
    var d = drag;
    drag = null;
    var side = $("#reader-side");
    side.style.transition = side.style.transform = "";
    if (d.dy > 8) dragged = true;
    // far enough, or a flick
    if (e.type === "pointerup" && (d.dy > 72 || d.dy > 24 && d.dy / (e.timeStamp - d.t) > 0.4)) closeSheet(true);
  }

  document.addEventListener("pointerup", endDrag);
  document.addEventListener("pointercancel", endDrag);
  document.addEventListener("click", function (e) {
    if (!dragged) return;
    dragged = false;
    e.preventDefault();
    e.stopPropagation();
  }, true);

  // Coming down to a phone's size: the sheet is open for what is tapped
  sheet.addEventListener("change", function () {
    if (sheet.matches && state.start === null && !state.word) state.sheetOpen = false;
    paintSheet();
  });

  // ----------------------------------------------------- navigation drawer

  function closeNav(returnFocus) {
    var reader = $("#reader");
    var nav = $("#reader-nav");
    var menu = $("#reader-menu");
    if (!reader || !nav || !menu) return;
    var wasOpen = reader.classList.contains("nav-open");
    reader.classList.remove("nav-open");
    $(".drawer-backdrop").hidden = true;
    menu.setAttribute("aria-expanded", "false");
    nav.removeAttribute("role");
    nav.removeAttribute("aria-modal");
    $$(".app-header, #text-pane, .reader-side").forEach(function (el) { el.inert = false; });
    if (wasOpen && returnFocus) (drawerFocus && drawerFocus.isConnected ? drawerFocus : menu).focus();
    drawerFocus = null;
  }

  function openNav() {
    if (!compact.matches) return;
    drawerFocus = document.activeElement;
    $("#reader").classList.add("nav-open");
    $(".drawer-backdrop").hidden = false;
    $("#reader-menu").setAttribute("aria-expanded", "true");
    var nav = $("#reader-nav");
    nav.setAttribute("role", "dialog");
    nav.setAttribute("aria-modal", "true");
    $$(".app-header, #text-pane, .reader-side").forEach(function (el) { el.inert = true; });
    $("[data-action='close-nav']", nav).focus();
  }

  compact.addEventListener("change", function () {
    closeNav(false);
    syncStudyPanel();
  });

  laptop.addEventListener("change", function () {
    paintParseTabs();
    paintColumn();
  });

  // The slim bar has room for the drawer button, the text toolbar and
  // Interlinear (on a phone, for the first two); Go to moves into the drawer
  // and back, and so does a phone's Interlinear. They keep their ids, so the
  // delegated listeners and the pane links' out-of-band #goto and
  // #interlinear-control swaps find them in either place.
  function placeHeaderTools() {
    var header = $(".app-header");
    var gotoSlot = $("#reader-nav [data-slot='goto']");
    var gotoForm = $("#goto");
    if (!header || !gotoSlot || !gotoForm) return;
    if (slimBar.matches) {
      gotoSlot.appendChild(gotoForm);
    } else if (!header.contains(gotoForm)) {
      $("#reader-menu").after(gotoForm);
    }
    var control = $("#interlinear-control");
    var controlSlot = $("#reader-nav [data-slot='interlinear']");
    if (!control || !controlSlot) return;
    if (phone.matches) {
      controlSlot.appendChild(control);
    } else if (!header.contains(control)) {
      $(".app-header .main-nav").after(control);
    }
  }

  slimBar.addEventListener("change", placeHeaderTools);
  phone.addEventListener("change", placeHeaderTools);

  // The range in the text toolbar opens Go to: in the drawer on an iPad
  function openGoto() {
    if (slimBar.matches) openNav();
    var field = $("#goto input[name='q']");
    if (!field) return;
    field.focus();
    field.select();
  }

  // --------------------------------------------------------------- events

  document.addEventListener("pointerdown", function (e) { pointerType = e.pointerType; });

  document.addEventListener("click", function (e) {
    var t = e.target;
    if (t.closest("#reader-menu")) return openNav();
    if (t.closest("[data-action='close-nav']")) return closeNav(true);
    if (t.closest("[data-action='close-sheet']")) return closeSheet(true);
    var resume = t.closest("a[data-resume]");
    if (resume && window.greekPositions) {
      storePosition(); // this text's link then opens where you are now
      window.greekPositions.resumeLinks(resume.parentNode);
    }
    if (t.closest("#reader-nav a")) closeNav(true);
    if (t.closest("#pane-range")) return openGoto();
    if (t.closest("[data-action='toggle-dict']")) return toggleDict();
    if (t.closest("[data-action='close-dict']")) return closeDict();
    if (t.closest("[data-action='expand-passage']")) return expandPassage(!passageExpanded());
    if (t.closest("[data-action='show-word']")) return showWordPane();

    var lineRef = t.closest(".line-ref");
    if (lineRef) {
      var touch = e.detail > 0 && (e.pointerType === "touch" || pointerType === "touch" || !pointerType && coarse.matches);
      pointerType = null;
      return onLineClick(lineRef.closest(".line"), e.shiftKey, touch);
    }

    var word = t.closest("#text-lines .w");
    if (word) return showWord(+word.closest(".line").dataset.line, +word.dataset.i);

    var row = t.closest(".wt-row[data-line]");
    if (row) return showWord(+row.dataset.line, +row.dataset.i);

    var toggle = t.closest("#passage-box .st-toggle");
    if (toggle) {
      var folding = toggle.closest("[role='treeitem']");
      return foldItem(folding, folding.getAttribute("aria-expanded") === "true");
    }
    var stRow = t.closest("#passage-box .st-row");
    if (stRow) return openItem(stRow.parentElement);
    var wordsView = t.closest("#passage-box [data-words-view]");
    if (wordsView) return showWordsView(wordsView.dataset.wordsView, false);
    var textToggle = t.closest("[data-action='toggle-sentence-text']");
    if (textToggle) return toggleSentenceText(textToggle);

    var parseTab = t.closest("#word-strip .ws-ptab");
    if (parseTab) return showParseTab(parseTab.dataset.ptab, false);

    var synRef = t.closest("#word-strip .syn-ref");
    if (synRef) return showHead(synRef);

    var studyTab = t.closest("[data-reader-tab]");
    if (studyTab && !studyTab.disabled) return showStudyTab(studyTab.dataset.readerTab, false);

    var tab = t.closest("#passage-box .pb-tab");
    if (tab) return showTab(tab.dataset.tab, false);

    if (t.closest("#selection-clear")) return clearSelection();
    if (t.closest("[data-action='clear-search']")) return clearSearch($(".search-form input[name='q']"));
  });

  // The word a role names fills while its name is hovered or focused
  document.addEventListener("mouseover", function (e) { pointHead(e, true); });
  document.addEventListener("mouseout", function (e) { pointHead(e, false); });
  document.addEventListener("focusin", function (e) { pointHead(e, true); });
  document.addEventListener("focusout", function (e) { pointHead(e, false); });

  // An outline row lights its words while hovered or focused; the focused
  // row is the tree's tab stop
  document.addEventListener("mouseover", function (e) {
    var row = e.target.closest && e.target.closest("#passage-box .st-row");
    if (!row || row.parentElement === structure.hovered) return;
    structure.hovered = row.parentElement;
    lightWords(structure.hovered);
  });
  document.addEventListener("mouseout", function (e) {
    var row = e.target.closest && e.target.closest("#passage-box .st-row");
    if (!row || row.contains(e.relatedTarget)) return;
    structure.hovered = null;
    lightWords(focusedItem());
  });
  document.addEventListener("focusin", function (e) {
    var item = e.target.closest && e.target.closest("#passage-box [role='treeitem']");
    if (!item || e.target !== item) return;
    $$("[role='treeitem']", treeOf(item)).forEach(function (el) { el.tabIndex = el === item ? 0 : -1; });
    if (!structure.hovered) lightWords(item);
  });
  document.addEventListener("focusout", function (e) {
    if (e.target.matches && e.target.matches("#passage-box [role='treeitem']") && !structure.hovered) lightWords(null);
  });

  document.addEventListener("change", function (e) {
    if (e.target.id === "interlinear-toggle") {
      $("#reader").classList.toggle("interlinear-on", e.target.checked);
      setCookie("interlinear", e.target.checked ? "1" : "0");
    }
  });

  document.addEventListener("keydown", function (e) {
    var t = e.target;
    if ($("#reader").classList.contains("nav-open")) {
      if (e.key === "Escape") {
        e.preventDefault();
        return closeNav(true);
      }
      if (e.key === "Tab") {
        var items = $$("#reader-nav a[href], #reader-nav button, #reader-nav input:not([type='hidden']), #reader-nav select").filter(function (el) { return !el.disabled && el.getClientRects().length; });
        var first = items[0], last = items[items.length - 1];
        if (e.shiftKey && (t === first || t.id === "reader-nav")) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && t === last) { e.preventDefault(); first.focus(); }
      }
      return;
    }
    // Escape in the search field clears it, and on an empty field goes back to the text
    if (e.key === "Escape" && t.closest && t.closest(".search-form")) {
      e.preventDefault();
      if (t.value) return clearSearch(t);
      var back = $("#search-back");
      if (back) back.click();
      return;
    }
    if (e.key === "Escape" && !isTyping(t) && sheet.matches && state.sheetOpen) return closeSheet(true);
    if (e.key === "Escape" && !isTyping(t) && !compact.matches && state.dictOpen && hasLsj()) return closeDict();
    if (e.key === "Escape" && !isTyping(t) && passageExpanded()) return showWordPane();
    if (e.key === "Escape" && !isTyping(t) && state.start !== null) return clearSelection();

    var item = t.matches && t.matches("#passage-box [role='treeitem']") ? t : null;
    if (item && /^(Arrow(Up|Down|Left|Right)|Home|End|Enter| )$/.test(e.key)) {
      e.preventDefault();
      return treeKey(item, e.key);
    }

    var radio = t.closest && t.closest("#passage-box [data-words-view]");
    if (radio && /^Arrow(Up|Down|Left|Right)$/.test(e.key)) {
      e.preventDefault();
      var radios = $$("#passage-box [data-words-view]");
      var step = /Right|Down/.test(e.key) ? 1 : -1;
      return showWordsView(radios[(radios.indexOf(radio) + step + radios.length) % radios.length].dataset.wordsView, true);
    }

    var word = t.closest && t.closest("#text-lines .w");
    if (word) {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        showWord(+word.closest(".line").dataset.line, +word.dataset.i);
      } else if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        e.preventDefault();
        moveWordFocus(word, e.key === "ArrowRight" ? 1 : -1);
      }
      return;
    }

    var lineRef = t.closest && t.closest("#text-lines .line-ref");
    if (lineRef && /^Arrow(Up|Down|Right)$/.test(e.key)) {
      e.preventDefault();
      var line = lineRef.closest(".line");
      if (e.key === "ArrowRight") {
        var first = $(".w", line);
        if (first) first.focus();
      } else {
        var sibling = e.key === "ArrowDown" ? line.nextElementSibling : line.previousElementSibling;
        var target = sibling && $(".line-ref", sibling);
        if (target) target.focus();
      }
      return;
    }

    var parseTab = t.closest && t.closest("#word-strip .ws-ptab");
    if (parseTab && /^(ArrowLeft|ArrowRight|Home|End)$/.test(e.key)) {
      e.preventDefault();
      var parseTabs = $$("#word-strip .ws-ptab");
      var from = parseTabs.indexOf(parseTab);
      var step = e.key === "Home" ? -from : e.key === "End" ? parseTabs.length - 1 - from : e.key === "ArrowRight" ? 1 : -1;
      return showParseTab(parseTabs[(from + step + parseTabs.length) % parseTabs.length].dataset.ptab, true);
    }

    var studyTab = t.closest && t.closest("[data-reader-tab]");
    if (studyTab && /^(ArrowLeft|ArrowRight|Home|End)$/.test(e.key)) {
      e.preventDefault();
      var studyTabs = $$(".reader-tabs [data-reader-tab]").filter(function (el) { return !el.disabled; });
      var at = studyTabs.indexOf(studyTab);
      var to = e.key === "Home" ? 0 : e.key === "End" ? studyTabs.length - 1 : (at + (e.key === "ArrowRight" ? 1 : -1) + studyTabs.length) % studyTabs.length;
      return showStudyTab(studyTabs[to].dataset.readerTab, true);
    }

    var tab = t.closest && t.closest("#passage-box .pb-tab");
    if (tab && /^(ArrowLeft|ArrowRight|Home|End)$/.test(e.key)) {
      e.preventDefault();
      var tabs = $$("#passage-box .pb-tab");
      var i = tabs.indexOf(tab);
      var next = e.key === "Home" ? 0 : e.key === "End" ? tabs.length - 1 : (i + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
      showTab(tabs[next].dataset.tab, true);
    }
  });

  document.body.addEventListener("htmx:afterSettle", function (e) {
    var target = e.detail.target;
    if (!target) return;
    var id = target.id;
    // An outerHTML swap reports the old, detached text pane as its target
    if (id === "text-pane" || $("#text-lines") && target.contains($("#text-lines"))) {
      syncInterlinear();
      paintSelection();
      paintWord();
      showOpened();
      resetPosition();
    }
    if (id === "passage-box") {
      structure.hovered = null;
      lightWords(null);
      restoreTab();
    }
    if (id === "word-strip") {
      paintParseTabs();
      paintWord();
      syncStudyPanel();
    }
  });

  // A pane link brings its book's Interlinear control (out of band), off in
  // a book without glosses. The server sets it from the cookie; keep it as
  // this page has it (a URL's interlinear= may say otherwise).
  function syncInterlinear() {
    var toggle = $("#interlinear-toggle");
    if (toggle && !toggle.disabled) toggle.checked = $("#reader").classList.contains("interlinear-on");
  }

  // A pane link pushes a new URL without the selection; put it back
  document.body.addEventListener("htmx:pushedIntoHistory", updateUrl);
  // Back/forward restores an older snapshot of the page; re-read its state
  document.body.addEventListener("htmx:historyRestore", init);

  init();
})();
