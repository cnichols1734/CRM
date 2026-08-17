/**
 * Site-wide toast notifications.
 * window.showCrmToast(message, type, options)
 * window.showToast(...) — same, for existing callers
 */
(function () {
    'use strict';

    var REGION_ID = 'crm-toast-region';
    var MAX_TOASTS = 3;
    var ENTER_MS = 20;
    var EXIT_MS = 180;

    var ICONS = {
        success: 'fa-check',
        error: 'fa-exclamation',
        warning: 'fa-exclamation',
        info: 'fa-info'
    };

    var DURATIONS = {
        success: 4000,
        error: 6000,
        warning: 5000,
        info: 4000
    };

    function normalizeType(type) {
        var t = String(type || 'success').toLowerCase();
        if (t === 'danger' || t === 'failed' || t === 'fail') return 'error';
        if (t === 'warn') return 'warning';
        if (t === 'message') return 'success';
        if (t === 'error' || t === 'warning' || t === 'info' || t === 'success') return t;
        return 'success';
    }

    function region() {
        var el = document.getElementById(REGION_ID);
        if (el) return el;
        el = document.createElement('div');
        el.id = REGION_ID;
        el.className = 'crm-toast-region';
        el.setAttribute('role', 'region');
        el.setAttribute('aria-label', 'Notifications');
        el.setAttribute('aria-live', 'polite');
        document.body.appendChild(el);
        bindRegion(el);
        return el;
    }

    function capStack(host) {
        var extras = host.querySelectorAll('.crm-toast');
        while (extras.length > MAX_TOASTS) {
            dismissToast(extras[0], true);
            extras = host.querySelectorAll('.crm-toast');
        }
    }

    function dismissToast(toast, immediate) {
        if (!toast || toast.dataset.leaving === '1') return;
        toast.dataset.leaving = '1';
        if (toast._hideTimer) {
            clearTimeout(toast._hideTimer);
            toast._hideTimer = null;
        }
        if (immediate) {
            toast.remove();
            return;
        }
        toast.classList.remove('is-in');
        toast.classList.add('is-out');
        setTimeout(function () {
            toast.remove();
        }, EXIT_MS);
    }

    function armTimer(toast, duration) {
        if (!duration || duration < 0) return;
        toast._hideTimer = setTimeout(function () {
            dismissToast(toast, false);
        }, duration);
    }

    function bindRegion(host) {
        if (!host || host._crmToastBound) return;
        host._crmToastBound = true;
        host.addEventListener('click', function (event) {
            var btn = event.target.closest('.crm-toast__close');
            if (!btn || !host.contains(btn)) return;
            var toast = btn.closest('.crm-toast');
            if (toast) dismissToast(toast, false);
        });
    }

    function bindToast(toast, duration) {
        toast.addEventListener('mouseenter', function () {
            if (toast._hideTimer) {
                clearTimeout(toast._hideTimer);
                toast._hideTimer = null;
            }
        });
        toast.addEventListener('mouseleave', function () {
            armTimer(toast, duration);
        });

        requestAnimationFrame(function () {
            toast.classList.add('is-in');
        });
        armTimer(toast, duration);
    }

    function showCrmToast(message, type, options) {
        if (message == null || message === '') return null;
        options = options || {};
        var kind = normalizeType(type);
        var duration = options.duration != null ? options.duration : DURATIONS[kind];
        var host = region();

        var toast = document.createElement('div');
        toast.className = 'crm-toast crm-toast--' + kind;
        toast.setAttribute('role', kind === 'error' ? 'alert' : 'status');

        var icon = document.createElement('span');
        icon.className = 'crm-toast__icon';
        icon.setAttribute('aria-hidden', 'true');
        icon.innerHTML = '<i class="fas ' + ICONS[kind] + '"></i>';

        var text = document.createElement('p');
        text.className = 'crm-toast__message';
        text.textContent = String(message);

        var closeBtn = document.createElement('button');
        closeBtn.type = 'button';
        closeBtn.className = 'crm-toast__close';
        closeBtn.setAttribute('aria-label', 'Dismiss');
        closeBtn.innerHTML = '<i class="fas fa-times" aria-hidden="true"></i>';

        toast.appendChild(icon);
        toast.appendChild(text);
        toast.appendChild(closeBtn);
        host.appendChild(toast);
        bindRegion(host);
        capStack(host);
        bindToast(toast, duration);
        return toast;
    }

    function hydrateServerToasts() {
        var host = document.getElementById(REGION_ID);
        if (!host) return;
        bindRegion(host);
        host.querySelectorAll('.crm-toast').forEach(function (toast) {
            var kind = 'success';
            toast.className.split(/\s+/).forEach(function (cls) {
                if (cls.indexOf('crm-toast--') === 0) kind = cls.slice('crm-toast--'.length);
            });
            var duration = DURATIONS[kind] || DURATIONS.success;
            setTimeout(function () {
                bindToast(toast, duration);
            }, ENTER_MS);
        });
    }

    window.showCrmToast = showCrmToast;
    window.showToast = showCrmToast;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', hydrateServerToasts);
    } else {
        hydrateServerToasts();
    }
})();
