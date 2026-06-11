/* Connect Laundry — Admin Operations Center UI
 * Injected on every Unfold admin page via UNFOLD["SCRIPTS"].
 * Provides a ⌘K global command palette (search) and a notification bell
 * (unread badge + dropdown + mark read), calling /api/v1/admin/* with the
 * admin session cookie. No framework — vanilla JS.
 */
(function () {
  "use strict";
  if (window.__aopsLoaded) return;
  window.__aopsLoaded = true;

  var API = "/api/v1/admin";
  var POLL_MS = 30000;
  var DEBOUNCE_MS = 300;

  var GROUPS = [
    { key: "users", label: "Users", icon: "person" },
    { key: "orders", label: "Orders", icon: "shopping_cart" },
    { key: "laundries", label: "Laundries", icon: "store" },
    { key: "payments", label: "Payments", icon: "payments" },
    { key: "reviews", label: "Reviews", icon: "star" },
    { key: "coupons", label: "Coupons", icon: "sell" },
  ];

  // ---- helpers ----
  function cookie(name) {
    var m = document.cookie.match("(^|;)\\s*" + name + "\\s*=\\s*([^;]+)");
    return m ? decodeURIComponent(m.pop()) : "";
  }
  function el(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function api(path, opts) {
    opts = opts || {};
    var headers = { "Accept": "application/json" };
    if (opts.method && opts.method !== "GET") headers["X-CSRFToken"] = cookie("csrftoken");
    return fetch(API + path, {
      method: opts.method || "GET",
      headers: headers,
      credentials: "same-origin",
    }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }
  function icon(name) {
    return '<span class="material-symbols-outlined">' + name + "</span>";
  }

  // ====================================================================== //
  // Header widget (search trigger + bell)
  // ====================================================================== //
  var header = el("div"); header.id = "aops-header";

  var searchBtn = el("button", "aops-btn");
  searchBtn.type = "button";
  searchBtn.innerHTML = icon("search") +
    '<span class="aops-label">Search</span><span class="aops-kbd">⌘K</span>';
  searchBtn.title = "Search records (Ctrl/Cmd+K)";

  var bellBtn = el("button", "aops-btn");
  bellBtn.type = "button";
  bellBtn.innerHTML = icon("notifications") + '<span class="aops-badge" id="aops-badge">0</span>';
  bellBtn.title = "Notifications";

  header.appendChild(searchBtn);
  header.appendChild(bellBtn);

  // ---- notification panel ----
  var panel = el("div"); panel.id = "aops-bell-panel";
  var panelHead = el("div", "aops-panel-head",
    "<span>Notifications</span>");
  var markAll = el("span", "aops-link", "Mark all read");
  panelHead.appendChild(markAll);
  var list = el("div", "aops-list"); list.id = "aops-bell-list";
  panel.appendChild(panelHead);
  panel.appendChild(list);

  // ---- command palette ----
  var overlay = el("div"); overlay.id = "aops-search-overlay";
  var modal = el("div"); modal.id = "aops-search-modal";
  var inputWrap = el("div", "aops-search-inputwrap", icon("search"));
  var input = el("input"); input.id = "aops-search-input";
  input.type = "text"; input.placeholder = "Search users, orders, laundries, payments, reviews, coupons…";
  input.setAttribute("autocomplete", "off");
  inputWrap.appendChild(input);
  var results = el("div", "aops-results"); results.id = "aops-results";
  var foot = el("div", "aops-search-foot",
    "<span><b>↑↓</b> navigate</span><span><b>↵</b> open</span><span><b>esc</b> close</span>");
  modal.appendChild(inputWrap);
  modal.appendChild(results);
  modal.appendChild(foot);
  overlay.appendChild(modal);

  function mount() {
    document.body.appendChild(header);
    document.body.appendChild(panel);
    document.body.appendChild(overlay);
  }

  // ====================================================================== //
  // Notifications
  // ====================================================================== //
  var badge = null;
  function setBadge(n) {
    badge = badge || document.getElementById("aops-badge");
    if (!badge) return;
    badge.textContent = n > 99 ? "99+" : String(n);
    badge.classList.toggle("show", n > 0);
  }
  function pollUnread() {
    api("/notifications/unread-count/")
      .then(function (j) { setBadge((j.data && j.data.unread) || 0); })
      .catch(function () {});
  }
  function renderNotifs(items) {
    list.innerHTML = "";
    if (!items || !items.length) {
      list.appendChild(el("div", "aops-empty", "You're all caught up 🎉"));
      return;
    }
    items.forEach(function (n) {
      var item = el("div", "aops-item" + (n.is_read ? "" : " unread"));
      var pri = (n.priority || "NORMAL");
      item.innerHTML =
        '<span class="aops-dot"></span>' +
        '<div class="aops-item-body">' +
          '<div class="aops-item-title">' + esc(n.title) + "</div>" +
          '<div class="aops-item-text">' + esc(n.body) + "</div>" +
          '<div class="aops-item-meta">' +
            '<span class="aops-pri aops-pri-' + esc(pri) + '">' + esc(pri) + "</span>" +
            "<span>" + esc((n.category || "").replace(/_/g, " ")) + "</span>" +
          "</div>" +
        "</div>";
      item.addEventListener("click", function () {
        var go = function () { if (n.action_url) window.location.href = n.action_url; };
        if (!n.is_read) {
          api("/notifications/" + n.id + "/read/", { method: "POST" })
            .then(function () { pollUnread(); }).finally(go);
        } else { go(); }
      });
      list.appendChild(item);
    });
  }
  function loadNotifs() {
    list.innerHTML = '<div class="aops-empty">Loading…</div>';
    api("/notifications/?limit=15")
      .then(function (j) {
        renderNotifs(j.data && j.data.results);
        setBadge((j.data && j.data.unread) || 0);
      })
      .catch(function () { list.innerHTML = '<div class="aops-empty">Failed to load.</div>'; });
  }
  function toggleBell() {
    var show = !panel.classList.contains("show");
    panel.classList.toggle("show", show);
    if (show) loadNotifs();
  }
  bellBtn.addEventListener("click", function (e) { e.stopPropagation(); toggleBell(); });
  markAll.addEventListener("click", function (e) {
    e.stopPropagation();
    api("/notifications/read-all/", { method: "POST" })
      .then(function () { loadNotifs(); pollUnread(); }).catch(function () {});
  });
  document.addEventListener("click", function (e) {
    if (panel.classList.contains("show") && !panel.contains(e.target) && e.target !== bellBtn) {
      panel.classList.remove("show");
    }
  });

  // ====================================================================== //
  // Command palette / search
  // ====================================================================== //
  var flat = [];        // flat list of {action_url} for keyboard nav
  var activeIdx = -1;
  var debounceTimer = null;
  var lastReqId = 0;

  function openSearch() {
    overlay.classList.add("show");
    input.value = "";
    results.innerHTML = "";
    flat = []; activeIdx = -1;
    setTimeout(function () { input.focus(); }, 10);
  }
  function closeSearch() { overlay.classList.remove("show"); }

  function setActive(i) {
    var nodes = results.querySelectorAll(".aops-res");
    if (!nodes.length) return;
    if (i < 0) i = nodes.length - 1;
    if (i >= nodes.length) i = 0;
    nodes.forEach(function (n) { n.classList.remove("active"); });
    nodes[i].classList.add("active");
    activeIdx = i;
    nodes[i].scrollIntoView({ block: "nearest" });
  }
  function navigate(url) { if (url) window.location.href = url; }

  function renderResults(data) {
    results.innerHTML = "";
    flat = []; activeIdx = -1;
    var total = data.total || 0;
    if (!total) {
      results.appendChild(el("div", "aops-empty", "No matches found."));
      return;
    }
    GROUPS.forEach(function (g) {
      var items = data[g.key] || [];
      if (!items.length) return;
      results.appendChild(el("div", "aops-group-label", g.label + " (" + items.length + ")"));
      items.forEach(function (it) {
        var row = el("div", "aops-res");
        row.innerHTML = icon(g.icon) +
          '<div class="aops-res-main">' +
            '<div class="aops-res-label">' + esc(it.label) + "</div>" +
            '<div class="aops-res-sub">' + esc(it.sublabel || "") + "</div>" +
          "</div>";
        row.addEventListener("click", function () { navigate(it.action_url); });
        var idx = flat.length;
        row.addEventListener("mouseenter", function () { setActive(idx); });
        results.appendChild(row);
        flat.push({ url: it.action_url });
      });
    });
    if (flat.length) setActive(0);
  }

  function runSearch(q) {
    if (q.trim().length < 2) { results.innerHTML = ""; flat = []; return; }
    var reqId = ++lastReqId;
    results.innerHTML = '<div class="aops-empty">Searching…</div>';
    api("/search/?q=" + encodeURIComponent(q))
      .then(function (j) {
        if (reqId !== lastReqId) return; // stale response
        renderResults(j.data || {});
      })
      .catch(function () {
        if (reqId !== lastReqId) return;
        results.innerHTML = '<div class="aops-empty">Search failed.</div>';
      });
  }

  input.addEventListener("input", function () {
    clearTimeout(debounceTimer);
    var q = input.value;
    debounceTimer = setTimeout(function () { runSearch(q); }, DEBOUNCE_MS);
  });
  input.addEventListener("keydown", function (e) {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive(activeIdx + 1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive(activeIdx - 1); }
    else if (e.key === "Enter") {
      e.preventDefault();
      if (activeIdx >= 0 && flat[activeIdx]) navigate(flat[activeIdx].url);
    } else if (e.key === "Escape") { e.preventDefault(); closeSearch(); }
  });
  searchBtn.addEventListener("click", openSearch);
  overlay.addEventListener("click", function (e) { if (e.target === overlay) closeSearch(); });

  // Global ⌘K / Ctrl+K (capture phase so we win over other handlers).
  document.addEventListener("keydown", function (e) {
    if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K")) {
      e.preventDefault(); e.stopPropagation();
      if (overlay.classList.contains("show")) closeSearch(); else openSearch();
    } else if (e.key === "Escape" && overlay.classList.contains("show")) {
      closeSearch();
    }
  }, true);

  // ====================================================================== //
  // Boot
  // ====================================================================== //
  function boot() {
    mount();
    pollUnread();
    setInterval(pollUnread, POLL_MS);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
