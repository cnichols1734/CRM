/**
 * transitions-dev orchestration for AgentFlow chrome.
 * Timing always reads from CSS custom properties.
 */
(function (global) {
  'use strict';

  var root = document.documentElement;

  function cssMs(name, fallback) {
    var v = parseFloat(getComputedStyle(root).getPropertyValue(name));
    return Number.isFinite(v) ? v : fallback;
  }

  function prefersReducedMotion() {
    return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }

  function openDropdown(el) {
    if (!el) return;
    el.hidden = false;
    el.classList.remove('hidden', 'is-closing', 'is-open');
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        el.classList.add('is-open');
      });
    });
  }

  function closeDropdown(el, after) {
    if (!el) return;
    el.classList.remove('is-open');
    el.classList.add('is-closing');
    var ms = prefersReducedMotion() ? 0 : cssMs('--dropdown-close-dur', 150);
    setTimeout(function () {
      el.classList.remove('is-closing');
      if (after) after();
    }, ms);
  }

  function isDropdownOpen(el) {
    return !!(el && el.classList.contains('is-open'));
  }

  function openModal(el) {
    if (!el) return;
    el.classList.remove('hidden', 'is-closing', 'is-open');
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        el.classList.add('is-open');
      });
    });
  }

  function closeModal(el, after) {
    if (!el) return;
    el.classList.remove('is-open');
    el.classList.add('is-closing');
    var ms = prefersReducedMotion() ? 0 : cssMs('--modal-close-dur', 150);
    setTimeout(function () {
      el.classList.remove('is-closing');
      if (after) after();
    }, ms);
  }

  function openToast(el) {
    if (!el) return;
    el.classList.add('t-toast');
    requestAnimationFrame(function () {
      el.classList.add('is-open');
    });
  }

  function closeToast(el, after) {
    if (!el) return;
    el.classList.remove('is-open');
    var ms = prefersReducedMotion() ? 0 : cssMs('--toast-close', 250);
    setTimeout(function () {
      if (after) after();
    }, ms);
  }

  function setDigits(group, str) {
    if (!group) return;
    group.classList.remove('is-animating');
    group.replaceChildren();
    var chars = String(str).split('');
    chars.forEach(function (ch, i) {
      var span = document.createElement('span');
      span.className = 't-digit';
      span.textContent = ch;
      if (i === chars.length - 2) span.dataset.stagger = '1';
      else if (i === chars.length - 1) span.dataset.stagger = '2';
      group.appendChild(span);
    });
    void group.offsetHeight;
    group.classList.add('is-animating');
  }

  function fillDigitGroups(scope) {
    (scope || document).querySelectorAll('.t-digit-group[data-t-digits]').forEach(function (group) {
      var skel = group.closest('.t-skel');
      if (skel && !skel.classList.contains('is-revealed')) return;
      setDigits(group, group.getAttribute('data-t-digits') || '');
    });
  }

  function showPage(slider, n) {
    if (!slider) return;
    slider.setAttribute('data-page', String(n));
    syncContactsPageHeight(slider);
  }

  function syncContactsPageHeight(slider) {
    if (!slider) return;
    var n = slider.getAttribute('data-page') || '1';
    var page = slider.querySelector('.t-page[data-page-id="' + n + '"]');
    var body = page && page.querySelector('.crm-contacts-page-body');
    if (!body) return;
    slider.style.height = body.scrollHeight + 'px';
  }

  function moveTabsTo(bar, tab, animate) {
    if (!bar || !tab) return;
    var pill = bar.querySelector('.t-tabs-pill');
    if (!pill) return;
    var place = function () {
      pill.style.transform = 'translateX(' + tab.offsetLeft + 'px)';
      pill.style.width = tab.offsetWidth + 'px';
      pill.style.height = tab.offsetHeight + 'px';
      pill.style.top = tab.offsetTop + 'px';
      pill.setAttribute('data-ready', 'true');
    };
    if (!animate) {
      var prev = pill.style.transition;
      pill.style.transition = 'none';
      place();
      void pill.offsetWidth;
      pill.style.transition = prev;
    } else {
      place();
    }
  }

  function activeTab(bar) {
    var tabs = bar.querySelectorAll('.t-tab');
    for (var i = 0; i < tabs.length; i++) {
      if (tabs[i].getAttribute('aria-selected') === 'true' || tabs[i].classList.contains('is-active')) {
        return tabs[i];
      }
    }
    return tabs[0] || null;
  }

  function initTabs(bar) {
    if (!bar || bar._tTabsBound) return;
    bar._tTabsBound = true;
    if (!bar.classList.contains('t-tabs')) bar.classList.add('t-tabs');
    if (!bar.querySelector('.t-tabs-pill')) {
      var pill = document.createElement('span');
      pill.className = 't-tabs-pill';
      pill.setAttribute('aria-hidden', 'true');
      bar.insertBefore(pill, bar.firstChild);
    }
    var tabs = Array.prototype.slice.call(bar.querySelectorAll('.crm-segment__item, .timeline-filter-btn, .t-tab'));
    tabs.forEach(function (tab) {
      tab.classList.add('t-tab');
      if (!tab.getAttribute('role')) tab.setAttribute('role', 'tab');
      var selected = tab.classList.contains('is-active') || tab.getAttribute('aria-selected') === 'true' || tab.getAttribute('aria-current') === 'page';
      tab.setAttribute('aria-selected', selected ? 'true' : 'false');
      tab.addEventListener('click', function () {
        tabs.forEach(function (t) {
          t.setAttribute('aria-selected', t === tab ? 'true' : 'false');
        });
        moveTabsTo(bar, tab, true);
      });
    });
    requestAnimationFrame(function () {
      moveTabsTo(bar, activeTab(bar), false);
    });
    window.addEventListener('resize', function () {
      moveTabsTo(bar, activeTab(bar), false);
    });
  }

  function syncTabs(bar) {
    if (!bar) return;
    var tabs = bar.querySelectorAll('.t-tab');
    tabs.forEach(function (tab) {
      var selected = tab.classList.contains('is-active') || tab.getAttribute('aria-current') === 'page';
      tab.setAttribute('aria-selected', selected ? 'true' : 'false');
    });
    moveTabsTo(bar, activeTab(bar), true);
  }

  function bezier(str) {
    var m = String(str).match(/cubic-bezier\(([-\d.]+),\s*([-\d.]+),\s*([-\d.]+),\s*([-\d.]+)\)/);
    if (!m) return function (t) { return t; };
    var x1 = parseFloat(m[1]), y1 = parseFloat(m[2]), x2 = parseFloat(m[3]), y2 = parseFloat(m[4]);
    var cx = 3 * x1, bx = 3 * (x2 - x1) - cx, ax = 1 - cx - bx;
    var cy = 3 * y1, by = 3 * (y2 - y1) - cy, ay = 1 - cy - by;
    return function (t) {
      if (t <= 0) return 0;
      if (t >= 1) return 1;
      var s = t;
      for (var i = 0; i < 8; i++) {
        var dx = ((ax * s + bx) * s + cx) * s - t;
        var d = (3 * ax * s + 2 * bx) * s + cx;
        if (Math.abs(dx) < 1e-6 || d === 0) break;
        s -= dx / d;
      }
      return ((ay * s + by) * s + cy) * s;
    };
  }

  function initClear(wrap) {
    if (!wrap || wrap._tClearBound) return;
    wrap._tClearBound = true;
    var input = wrap.querySelector('input');
    var mirror = wrap.querySelector('.t-clear-mirror');
    var phold = wrap.querySelector('.t-clear-placeholder');
    var glow = wrap.querySelector('.t-clear-glow');
    var btn = wrap.querySelector('.t-clear-btn');
    if (!input || !mirror || !phold || !glow || !btn) return;

    var canvas = document.createElement('canvas').getContext('2d');
    var clearing = false;

    var sync = function () {
      var has = input.value.length > 0;
      wrap.classList.toggle('has-value', has);
      if (has) mirror.textContent = input.value.replace(/ /g, '\u00a0');
    };

    function buildGlow(text) {
      canvas.font = getComputedStyle(input).font;
      var isDark = root.getAttribute('data-theme') === 'dark';
      var rgb = isDark ? '255,255,255' : '0,0,0';
      var w = wrap.clientWidth || 280;
      var padLeft = parseFloat(getComputedStyle(input).paddingLeft) || 12;
      var spread = cssMs('--glow-spread', 1.5);
      var layers = [];
      var x = 0;
      String(text).split(/(\s+)/).forEach(function (seg) {
        var segW = canvas.measureText(seg).width;
        if (seg.trim()) {
          var cx = padLeft + x + segW / 2;
          var hw = Math.max(segW * 0.45, 8) * spread;
          [[0, 0.8, 7, 0.22], [hw * 0.45, 0.55, 8, 0.18],
           [-hw * 0.4, 0.65, 6, 0.16], [hw * 0.15, 0.9, 5, 0.14]]
            .forEach(function (row) {
              var dx = row[0], rwm = row[1], rh = row[2], a = row[3];
              var lx = (((cx + dx) / w) * 100).toFixed(2);
              layers.push(
                'radial-gradient(ellipse ' + Math.max(hw * rwm, 2).toFixed(1) + 'px ' + rh + 'px at ' + lx + '% 100%, rgba(' + rgb + ',' + a + '), transparent)'
              );
            });
        }
        x += segW;
      });
      return layers.join(', ');
    }

    function clearWithAnimation() {
      if (clearing || !input.value) return;
      clearing = true;
      var keepFocus = document.activeElement === input;
      mirror.textContent = input.value.replace(/ /g, '\u00a0');

      var total = cssMs('--clear-dur', 400);
      var outDur = cssMs('--clear-out-dur', 400);
      var inDur = cssMs('--clear-in-dur', 400);
      var outFly = cssMs('--clear-out-fly', 12);
      var inFly = cssMs('--clear-in-fly', 12);
      var blur = cssMs('--clear-blur', 2);
      var delay = cssMs('--glow-delay', 50);
      var peakAt = cssMs('--glow-peak-at', 0.15);
      var gOp = cssMs('--glow-opacity', 0.42);
      var easeOut = bezier(getComputedStyle(root).getPropertyValue('--clear-out-ease'));
      var easeIn = bezier(getComputedStyle(root).getPropertyValue('--clear-in-ease'));

      input.value = '';
      wrap.classList.remove('has-value');
      wrap.classList.add('is-clearing');
      glow.style.background = buildGlow(mirror.textContent);
      glow.style.opacity = '0';
      phold.style.transform = 'translateY(-' + inFly + 'px)';
      phold.style.opacity = '0.9';
      phold.style.filter = 'blur(' + blur + 'px)';

      var t0 = performance.now();
      (function tick(now) {
        var el = now - t0;
        var eo = easeOut(Math.min(1, el / outDur));
        mirror.style.transform = 'translateY(' + (eo * outFly).toFixed(1) + 'px)';
        mirror.style.opacity = (1 - eo).toFixed(3);
        mirror.style.filter = 'blur(' + (eo * blur).toFixed(1) + 'px)';

        var ei = easeIn(Math.min(1, el / inDur));
        phold.style.transform = 'translateY(' + (-inFly + ei * inFly).toFixed(1) + 'px)';
        phold.style.opacity = (0.9 + ei * 0.1).toFixed(3);
        phold.style.filter = 'blur(' + (blur - ei * blur).toFixed(1) + 'px)';

        var g = 0;
        if (el > delay) {
          var gp = Math.min(1, (el - delay) / Math.max(1, total - delay));
          g = gp < peakAt ? gp / peakAt : 1 - (gp - peakAt) / (1 - peakAt);
        }
        glow.style.opacity = (g * gOp).toFixed(3);

        if (el < total && !prefersReducedMotion()) {
          requestAnimationFrame(tick);
        } else {
          wrap.classList.remove('is-clearing');
          [mirror, phold].forEach(function (node) { node.style.cssText = ''; });
          mirror.textContent = '';
          glow.style.opacity = '0';
          glow.style.background = '';
          glow.style.filter = '';
          glow.style.visibility = 'hidden';
          clearing = false;
          input.dispatchEvent(new Event('input', { bubbles: true }));
          wrap.dispatchEvent(new CustomEvent('t:cleared', { bubbles: true }));
          if (keepFocus) requestAnimationFrame(function () { input.focus({ preventScroll: true }); });
        }
      })(performance.now());
    }

    var keep = function (e) { if (document.activeElement === input) e.preventDefault(); };
    btn.addEventListener('pointerdown', keep);
    btn.addEventListener('mousedown', keep);
    btn.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopImmediatePropagation();
      clearWithAnimation();
    });
    input.addEventListener('input', sync);
    glow.style.opacity = '0';
    glow.style.background = '';
    glow.style.visibility = 'hidden';
    sync();
  }

  function calibrateCheck(el) {
    if (!el) return;
    var path = el.querySelector('svg path');
    if (!path || typeof path.getTotalLength !== 'function') return;
    var len = Math.ceil(path.getTotalLength()) + 1;
    el.style.setProperty('--check-len', String(len));
  }

  function setChecked(el, on) {
    if (!el) return;
    el.setAttribute('aria-checked', on ? 'true' : 'false');
    var native = el.parentElement && el.parentElement.querySelector('.t-check-native, .todo-checkbox');
    if (native) native.checked = !!on;
  }

  function initCheck(el) {
    if (!el || el._tCheckBound) return;
    el._tCheckBound = true;
    calibrateCheck(el);
    var native = el.parentElement && el.parentElement.querySelector('.t-check-native, input[type="checkbox"]');
    if (native) {
      el.setAttribute('aria-checked', native.checked ? 'true' : 'false');
      native.addEventListener('change', function () {
        el.setAttribute('aria-checked', native.checked ? 'true' : 'false');
      });
      el.addEventListener('click', function (e) {
        if (e.target === native) return;
        e.preventDefault();
        native.checked = !native.checked;
        native.dispatchEvent(new Event('change', { bubbles: true }));
      });
    }
  }

  function revealSkeleton(skel) {
    if (!skel) return;
    skel.classList.add('is-revealed');
  }

  function resetSkeleton(skel) {
    if (!skel) return;
    var skeleton = skel.querySelector('.t-skel-skeleton');
    skel.classList.add('is-resetting');
    skel.classList.remove('is-revealed');
    if (skeleton) skeleton.classList.remove('is-pulsing');
    void (skeleton ? skeleton.offsetWidth : skel.offsetWidth);
    skel.classList.remove('is-resetting');
    if (skeleton) skeleton.classList.add('is-pulsing');
  }

  function swapText(el, next) {
    if (!el) return;
    var dur = prefersReducedMotion() ? 0 : cssMs('--text-swap-dur', 150);
    el.classList.add('is-exit');
    setTimeout(function () {
      el.textContent = next;
      el.classList.remove('is-exit');
      el.classList.add('is-enter-start');
      void el.offsetHeight;
      el.classList.remove('is-enter-start');
    }, dur);
  }

  function showError(wrap) {
    if (!wrap) return;
    var input = wrap.querySelector('.t-input');
    if (!input) return;
    wrap.classList.add('is-error');
    input.classList.add('is-error');
    input.classList.remove('is-shaking');
    void input.offsetWidth;
    input.classList.add('is-shaking');
    var shakeMs = cssMs('--shake-dur-a', 80) * 2 + cssMs('--shake-dur-b', 60) * 2;
    setTimeout(function () { input.classList.remove('is-shaking'); }, shakeMs + 20);
    if (wrap._revertTimer) clearTimeout(wrap._revertTimer);
    var hold = cssMs('--revert-hold', 3000);
    wrap._revertTimer = setTimeout(function () {
      wrap._revertTimer = null;
      wrap.classList.remove('is-error');
      input.classList.remove('is-error');
    }, shakeMs + hold);
  }

  function bindErrorInputs(scope) {
    (scope || document).querySelectorAll('.t-input-wrap').forEach(function (wrap) {
      if (wrap._tErrorBound) return;
      wrap._tErrorBound = true;
      var field = wrap.querySelector('input, textarea');
      if (!field) return;
      field.addEventListener('input', function () {
        if (wrap._revertTimer) {
          clearTimeout(wrap._revertTimer);
          wrap._revertTimer = null;
        }
        wrap.classList.remove('is-error');
        var box = wrap.querySelector('.t-input');
        if (box) box.classList.remove('is-error');
      });
      field.addEventListener('invalid', function (e) {
        e.preventDefault();
        showError(wrap);
      });
    });
  }

  function extractContactsBody(html) {
    var doc = new DOMParser().parseFromString(html, 'text/html');
    return doc.querySelector('.crm-contacts-page-body');
  }

  function isContactViewUrl(href) {
    try {
      var url = new URL(href, window.location.origin);
      return /^\/contact\/\d+\/?$/.test(url.pathname);
    } catch (e) {
      return false;
    }
  }

  function isContactsListUrl(href) {
    try {
      var url = new URL(href, window.location.origin);
      return url.pathname === '/contacts';
    } catch (e) {
      return false;
    }
  }

  function initContactsPageSlide() {
    var slider = document.getElementById('crmContactsPages');
    if (!slider) return;

    slider.style.setProperty('--page-exit-enabled', '0');
    syncContactsPageHeight(slider);
    requestAnimationFrame(function () {
      slider.style.setProperty('--page-exit-enabled', '1');
    });

    window.addEventListener('resize', function () {
      syncContactsPageHeight(slider);
    });

    document.addEventListener('click', function (event) {
      var link = event.target.closest('a');
      if (!link || link.target === '_blank' || event.metaKey || event.ctrlKey || event.shiftKey) return;
      var href = link.getAttribute('href');
      if (!href) return;

      var current = slider.getAttribute('data-page') || '1';
      var next = null;
      if (current === '1' && isContactViewUrl(href)) next = 2;
      if (current === '2' && isContactsListUrl(href) && link.classList.contains('crm-back')) next = 1;
      if (!next) return;

      event.preventDefault();
      fetch(href, { credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } })
        .then(function (res) { return res.ok ? res.text() : Promise.reject(res); })
        .then(function (html) {
          var incoming = extractContactsBody(html);
          var targetPage = slider.querySelector('.t-page[data-page-id="' + next + '"]');
          var slot = targetPage && targetPage.querySelector('.crm-contacts-page-body');
          if (!incoming || !slot) {
            window.location.assign(href);
            return;
          }
          slot.replaceWith(incoming);
          showPage(slider, next);
          history.pushState({ contactsPage: next }, '', href);
          var wait = prefersReducedMotion() ? 0 : cssMs('--page-slide-dur', 250);
          setTimeout(function () {
            window.location.assign(href);
          }, wait + 40);
        })
        .catch(function () {
          window.location.assign(href);
        });
    });
  }

  function setThink(box, next) {
    if (!box || next == null) return;
    var live = box.querySelector('.t-think-text:not(.is-exit)');
    if (!live) {
      live = document.createElement('span');
      live.className = 't-think-text';
      box.appendChild(live);
    }
    if (live.textContent === next && live.getAttribute('data-text') === next) return;
    var swap = prefersReducedMotion() ? 0 : cssMs('--think-swap', 150);
    var gap = prefersReducedMotion() ? 0 : cssMs('--think-gap', 50);
    live.classList.add('is-exit');
    var incoming = document.createElement('span');
    incoming.className = 't-think-text is-enter-start';
    if (live.classList.contains('t-shimmer') || box.classList.contains('t-shimmer')) {
      incoming.classList.add('t-shimmer');
    }
    incoming.textContent = next;
    incoming.setAttribute('data-text', next);
    box.appendChild(incoming);
    var release = function () {
      void incoming.offsetWidth;
      incoming.classList.remove('is-enter-start');
    };
    if (gap > 0) setTimeout(release, gap);
    else release();
    setTimeout(function () { live.remove(); }, swap + gap);
  }

  function showText(block) {
    if (!block) return;
    block.classList.remove('is-hiding');
    block.classList.remove('is-shown');
    void block.offsetHeight;
    block.classList.add('is-shown');
  }

  function hideText(block) {
    if (!block) return;
    block.classList.add('is-hiding');
    block.classList.remove('is-shown');
    setTimeout(function () { block.classList.remove('is-hiding'); }, 200);
  }

  function initToggle(el) {
    if (!el || el._tToggleBound) return;
    el._tToggleBound = true;
    if (!el.classList.contains('t-toggle')) el.classList.add('t-toggle');
    var thumb = el.querySelector('.crm-toggle__thumb, .t-toggle-thumb');
    if (thumb && !thumb.classList.contains('t-toggle-thumb')) thumb.classList.add('t-toggle-thumb');
    var input = el.closest('label') && el.closest('label').querySelector('input[type="checkbox"]');
    if (!input && el.parentElement) input = el.parentElement.querySelector('input[type="checkbox"]');
    var sync = function (init) {
      var on = input ? !!input.checked : el.getAttribute('data-on') === 'true';
      el.setAttribute('data-on', on ? 'true' : 'false');
      if (!init) el.classList.add('is-init');
    };
    sync(true);
    if (input) {
      input.addEventListener('change', function () { sync(false); });
    }
  }

  function calibrateSuccessCheck(el) {
    if (!el) return;
    var path = el.querySelector('svg path');
    if (!path || typeof path.getTotalLength !== 'function') return;
    var len = Math.ceil(path.getTotalLength()) + 1;
    path.style.strokeDasharray = String(len);
    path.style.strokeDashoffset = String(len);
  }

  function playSuccessCheck(el) {
    if (!el) return;
    calibrateSuccessCheck(el);
    el.setAttribute('data-state', 'out');
    void el.offsetWidth;
    el.setAttribute('data-state', 'in');
  }

  function ensureSaveChrome(btn) {
    if (!btn) return null;
    var check = btn.querySelector('.t-success-check');
    if (check) return check;
    check = document.createElement('span');
    check.className = 't-success-check';
    check.setAttribute('data-state', 'out');
    check.setAttribute('aria-hidden', 'true');
    check.innerHTML = '<svg viewBox="0 0 48 48" fill="none"><path d="M10 24.5L20 34.5L38 14" stroke="currentColor" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    btn.appendChild(check);
    return check;
  }

  function setSaveState(btn, state) {
    if (!btn) return;
    var swap = btn.querySelector('.t-icon-swap');
    var check = ensureSaveChrome(btn);
    if (state === 'busy') {
      btn.classList.add('is-saving');
      btn.disabled = true;
      if (swap) swap.setAttribute('data-state', 'b');
      if (check) check.setAttribute('data-state', 'out');
      return;
    }
    if (state === 'success') {
      btn.classList.remove('is-saving');
      btn.disabled = false;
      if (swap) swap.setAttribute('data-state', 'a');
      playSuccessCheck(check);
      return;
    }
    btn.classList.remove('is-saving');
    btn.disabled = false;
    if (swap) swap.setAttribute('data-state', 'a');
    if (check) check.setAttribute('data-state', 'out');
  }

  function readConfettiNum(el, name, fallback) {
    var v = parseFloat(getComputedStyle(el).getPropertyValue(name));
    return Number.isFinite(v) ? v : fallback;
  }

  function ensureConfettiOverlay() {
    var overlay = document.getElementById('t-confetti-overlay');
    if (overlay) return overlay;
    overlay = document.createElement('div');
    overlay.id = 't-confetti-overlay';
    overlay.className = 't-confetti-overlay';
    overlay.setAttribute('aria-hidden', 'true');
    var canvas = document.createElement('canvas');
    canvas.id = 't-confetti-canvas';
    overlay.appendChild(canvas);
    document.body.appendChild(overlay);
    return overlay;
  }

  var confettiWaiters = [];

  function settleConfetti() {
    var waiters = confettiWaiters;
    confettiWaiters = [];
    for (var w = 0; w < waiters.length; w++) waiters[w]();
  }

  function whenConfettiSettled(after) {
    if (!after) return;
    var overlay = document.getElementById('t-confetti-overlay');
    if (!overlay || !overlay.classList.contains('is-running')) {
      after();
      return;
    }
    confettiWaiters.push(after);
  }

  function burstConfetti(anchor, opts) {
    if (!anchor || prefersReducedMotion()) {
      settleConfetti();
      return;
    }
    opts = opts || {};
    var useLocalOrigin = !!opts.localOrigin;
    var overlay = ensureConfettiOverlay();
    var canvas = overlay.querySelector('canvas');
    if (!canvas) return;
    var ctx = canvas.getContext('2d');
    if (!ctx) return;

    var COLORS = [
      '#ff4d67', '#ffb020', '#3b82f6', '#22c55e',
      '#a855f7', '#f97316', '#06b6d4', '#f43f5e'
    ];
    var particles = [];
    var running = true;
    var lastT = performance.now();
    var burstEnd = 0;
    var fadeStart = null;
    var stageW = 0;
    var stageH = 0;

    function sizeCanvas() {
      var r = overlay.getBoundingClientRect();
      var dpr = window.devicePixelRatio || 1;
      stageW = r.width;
      stageH = r.height;
      canvas.width = Math.round(r.width * dpr);
      canvas.height = Math.round(r.height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function buttonRect() {
      var s = overlay.getBoundingClientRect();
      var b = anchor.getBoundingClientRect();
      return {
        left: b.left - s.left,
        top: b.top - s.top,
        right: b.right - s.left,
        bottom: b.bottom - s.top
      };
    }

    function buttonSurface(x, b) {
      if (x < b.left || x > b.right) return null;
      var r = (b.bottom - b.top) / 2;
      var lc = b.left + r;
      var rc = b.right - r;
      if (x >= lc && x <= rc) return { y: b.top, slope: 0 };
      var cx = x < lc ? lc : rc;
      var dx = x - cx;
      var root = Math.sqrt(Math.max(r * r - dx * dx, 0));
      return { y: b.top + (r - root), slope: dx / Math.max(root, 0.001) };
    }

    sizeCanvas();
    var now = performance.now();
    var count = Math.round(readConfettiNum(overlay, '--pv1o', 120));
    var size = readConfettiNum(overlay, '--pv22', 4);
    var spawnWindow = 500;
    var origin = buttonRect();
    var originX = (origin.left + origin.right) / 2;
    var originY = origin.top;
    var spreadX = Math.max(origin.right - origin.left, 16) * 5;
    for (var i = 0; i < count; i++) {
      particles.push({
        start: now + Math.random() * spawnWindow,
        x: useLocalOrigin ? originX + (Math.random() - 0.5) * spreadX : Math.random() * stageW,
        y: useLocalOrigin ? originY - 10 - Math.random() * 36 : -12 - Math.random() * 30,
        py: useLocalOrigin ? originY - 12 : -12,
        vx: (Math.random() - 0.5) * 60,
        vy: 40 + Math.random() * 120,
        w: size * (0.7 + Math.random() * 0.6),
        h: size * (0.5 + Math.random() * 0.5),
        maxFall: 420 + Math.random() * 280,
        rot: Math.random() * Math.PI,
        vr: (Math.random() - 0.5) * 7,
        tumble: Math.random() * Math.PI * 2,
        tumbleSpeed: 4 + Math.random() * 8,
        squish: 1,
        phase: Math.random() * Math.PI * 2,
        swayFreq: 2 + Math.random() * 3,
        swayScale: 0.5 + Math.random(),
        color: COLORS[Math.floor(Math.random() * COLORS.length)],
        bounces: 0,
        resting: false,
        dead: false
      });
    }
    burstEnd = now + spawnWindow + 100;

    function step(dt, tnow) {
      var g = readConfettiNum(overlay, '--pv15', 1300);
      var sway = readConfettiNum(overlay, '--pv23', 16);
      var restitution = readConfettiNum(overlay, '--pv1b', 0.6);
      var b = buttonRect();
      for (var j = 0; j < particles.length; j++) {
        var p = particles[j];
        if (p.resting || p.dead || tnow < p.start) continue;
        p.py = p.y;
        p.vy += g * dt;
        if (p.vy > p.maxFall) p.vy = p.maxFall;
        p.phase += p.swayFreq * dt;
        p.x += (p.vx + Math.cos(p.phase) * sway * p.swayScale) * dt;
        p.y += p.vy * dt;
        p.rot += p.vr * dt;
        p.tumble += p.tumbleSpeed * dt;
        p.squish = 0.25 + 0.75 * Math.abs(Math.cos(p.tumble));
        var half = p.h / 2;
        if (p.vy > 0) {
          var s = buttonSurface(p.x, b);
          if (s && p.y + half >= s.y && p.py + half <= s.y + 2) {
            if (Math.abs(s.slope) > 0.85) {
              var dir = p.x < (b.left + b.right) / 2 ? -1 : 1;
              p.vx = dir * Math.max(Math.abs(p.vx), 50 + Math.random() * 50);
              p.vy *= 0.35;
              p.y = s.y - half;
            } else if (p.vy > 150 && p.bounces < 2) {
              p.bounces++;
              p.vy = -p.vy * restitution * (0.6 + Math.random() * 0.5);
              p.vx = p.vx * 0.7 + s.slope * 40 + (Math.random() - 0.5) * 40;
              p.y = s.y - half;
            } else {
              p.resting = true;
              p.y = s.y - half - 0.5;
              p.vx = 0;
              p.vy = 0;
            }
          }
        }
        if (!p.resting && p.y + half >= stageH - 1) {
          if (p.vy > 170 && p.bounces < 2) {
            p.bounces++;
            p.vy = -p.vy * restitution * (0.5 + Math.random() * 0.4);
            p.vx *= 0.7;
            p.y = stageH - 1 - half;
          } else {
            p.resting = true;
            p.y = stageH - 1 - half;
            p.vx = 0;
            p.vy = 0;
          }
        }
        if (p.x < -30 || p.x > stageW + 30 || p.y > stageH + 30) {
          p.dead = true;
        }
        if (useLocalOrigin && !p.resting && p.y > b.bottom + 96) {
          p.dead = true;
        }
      }
    }

    function draw(alpha) {
      ctx.clearRect(0, 0, stageW, stageH);
      ctx.globalAlpha = alpha;
      var tnow = performance.now();
      for (var k = 0; k < particles.length; k++) {
        var p = particles[k];
        if (p.dead || tnow < p.start) continue;
        ctx.save();
        ctx.translate(p.x, p.y);
        ctx.rotate(p.rot);
        ctx.scale(1, p.squish);
        ctx.fillStyle = p.color;
        ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h);
        ctx.restore();
      }
      ctx.globalAlpha = 1;
    }

    function frame(tnow) {
      if (!running) return;
      var remaining = Math.min((tnow - lastT) / 1000, 0.25);
      lastT = tnow;
      while (remaining > 0) {
        var dt = Math.min(remaining, 1 / 60);
        step(dt, tnow);
        remaining -= dt;
      }
      var settled = tnow > burstEnd;
      if (settled) {
        for (var n = 0; n < particles.length; n++) {
          if (!particles[n].resting && !particles[n].dead) { settled = false; break; }
        }
      }
      if (settled && fadeStart === null) {
        fadeStart = tnow + readConfettiNum(overlay, '--pv20', 600);
      }
      var alpha = 1;
      if (fadeStart !== null && tnow >= fadeStart) {
        var fade = Math.max(readConfettiNum(overlay, '--pv1z', 300), 1);
        alpha = 1 - (tnow - fadeStart) / fade;
        if (alpha <= 0) {
          running = false;
          particles = [];
          ctx.clearRect(0, 0, stageW, stageH);
          overlay.classList.remove('is-running');
          settleConfetti();
          return;
        }
      }
      draw(alpha);
      requestAnimationFrame(frame);
    }

    overlay.classList.add('is-running');
    requestAnimationFrame(frame);
  }

  function initThemeIcons() {
    var theme = root.getAttribute('data-theme') === 'light' ? 'b' : 'a';
    document.querySelectorAll('[data-t-theme-swap]').forEach(function (el) {
      el.setAttribute('data-state', theme);
    });
    var expanded = root.classList.contains('sidebar-expanded');
    document.querySelectorAll('[data-t-sidebar-swap]').forEach(function (el) {
      el.setAttribute('data-state', expanded ? 'b' : 'a');
    });
  }

  function boot() {
    document.querySelectorAll('.t-clear').forEach(initClear);
    document.querySelectorAll('.t-check').forEach(initCheck);
    document.querySelectorAll('.crm-segment, .crm-activity-tabs').forEach(initTabs);
    document.querySelectorAll('.t-toast').forEach(function (toast) {
      if (!toast.classList.contains('is-open')) openToast(toast);
    });
    fillDigitGroups(document);
    document.querySelectorAll('.t-skel[data-reveal-on="load"]').forEach(function (skel) {
      var total = cssMs('--pulse-dur', 1000) * cssMs('--pulse-count', 1);
      setTimeout(function () {
        revealSkeleton(skel);
        fillDigitGroups(skel);
      }, prefersReducedMotion() ? 0 : total);
    });
    bindErrorInputs(document);
    document.querySelectorAll('.t-input-wrap.is-error').forEach(function (wrap) {
      var input = wrap.querySelector('.t-input');
      if (input) {
        input.classList.add('is-error');
        input.classList.remove('is-shaking');
        void input.offsetWidth;
        input.classList.add('is-shaking');
      }
    });
    initThemeIcons();
    initContactsPageSlide();
    document.querySelectorAll('.crm-toggle__track, .t-toggle').forEach(initToggle);
    document.querySelectorAll('.t-stagger').forEach(showText);
    document.querySelectorAll('.t-success-check').forEach(calibrateSuccessCheck);
    if (document.querySelector('.crm-toast--success')) {
      document.querySelectorAll('.t-save .t-success-check, [data-t-save] .t-success-check').forEach(playSuccessCheck);
    }
  }

  global.TMotion = {
    cssMs: cssMs,
    openDropdown: openDropdown,
    closeDropdown: closeDropdown,
    isDropdownOpen: isDropdownOpen,
    openModal: openModal,
    closeModal: closeModal,
    openToast: openToast,
    closeToast: closeToast,
    setDigits: setDigits,
    showPage: showPage,
    initTabs: initTabs,
    syncTabs: syncTabs,
    initClear: initClear,
    initCheck: initCheck,
    setChecked: setChecked,
    revealSkeleton: revealSkeleton,
    resetSkeleton: resetSkeleton,
    swapText: swapText,
    showError: showError,
    initThemeIcons: initThemeIcons,
    syncContactsPageHeight: syncContactsPageHeight,
    setThink: setThink,
    showText: showText,
    hideText: hideText,
    initToggle: initToggle,
    playSuccessCheck: playSuccessCheck,
    setSaveState: setSaveState,
    burstConfetti: burstConfetti,
    whenConfettiSettled: whenConfettiSettled
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})(window);
