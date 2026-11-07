/**
 * Open in-app page navigations in a new window (desktop pywebview or browser).
 * Same-window: logout, downloads, external links, hash/modal toggles, filters/reloads.
 */
(function () {
  'use strict';

  var SAME_WINDOW_PATHS = [
    '/auth/logout',
    '/auth/login',
    '/auth/change-password',
    '/auth/change_password',
  ];

  function absoluteUrl(url) {
    try {
      return new URL(url, window.location.href).href;
    } catch (_) {
      return String(url || '');
    }
  }

  function pathOnly(url) {
    try {
      return new URL(url, window.location.href).pathname;
    } catch (_) {
      return '';
    }
  }

  function titleFromLink(link, url) {
    var text = (link && (link.getAttribute('title') || link.textContent) || '').replace(/\s+/g, ' ').trim();
    if (text && text.length < 80) return text;
    var path = pathOnly(url);
    var parts = path.split('/').filter(Boolean);
    if (!parts.length) return 'Dashboard';
    return parts[0].charAt(0).toUpperCase() + parts[0].slice(1);
  }

  function shouldKeepSameWindow(link, url) {
    if (!link) return true;
    if (link.getAttribute('data-same-window') === '1') return true;
    if (link.classList.contains('nav-locked')) return true;
    if (link.hasAttribute('download')) return true;
    if (link.getAttribute('data-bs-toggle') || link.getAttribute('data-bs-target')) return true;
    if (link.getAttribute('data-clear-page-filters')) return true;

    var href = (link.getAttribute('href') || '').trim();
    if (!href || href === '#' || href.charAt(0) === '#') return true;
    if (/^(javascript:|mailto:|tel:)/i.test(href)) return true;

    var abs;
    try {
      abs = new URL(url, window.location.href);
    } catch (_) {
      return true;
    }
    if (abs.origin !== window.location.origin) return true;
    if (abs.pathname === window.location.pathname && abs.search === window.location.search && abs.hash) {
      return true;
    }

    var path = abs.pathname.replace(/\/+$/, '') || '/';
    for (var i = 0; i < SAME_WINDOW_PATHS.length; i++) {
      var p = SAME_WINDOW_PATHS[i].replace(/\/+$/, '') || '/';
      if (path === p || path.indexOf(p + '/') === 0) return true;
    }
    return false;
  }

  function openViaDesktop(url, title) {
    var api = window.pywebview && window.pywebview.api;
    if (!api || typeof api.open_app_window !== 'function') return false;
    try {
      var result = api.open_app_window(url, title || '');
      if (result && typeof result.then === 'function') {
        result.catch(function () {});
      }
      return true;
    } catch (_) {
      return false;
    }
  }

  function withChildFlag(url) {
    try {
      var u = new URL(url, window.location.href);
      if (u.searchParams.get('mbf_child') !== '1') {
        u.searchParams.set('mbf_child', '1');
      }
      return u.toString();
    } catch (_) {
      return url;
    }
  }

  function openViaBrowser(url) {
    var name = 'mbf_' + Date.now() + '_' + Math.floor(Math.random() * 1e6);
    var win = window.open(withChildFlag(url), name);
    if (win) {
      try {
        win.focus();
      } catch (_) {}
      return true;
    }
    return false;
  }

  /**
   * Open an app page in a new window. Falls back to same-window navigation if blocked.
   */
  window.openAppPage = function (url, title) {
    var abs = absoluteUrl(url);
    if (!abs) return false;
    if (openViaDesktop(abs, title || '')) return true;
    if (openViaBrowser(abs)) return true;
    window.location.href = abs;
    return false;
  };

  document.addEventListener(
    'click',
    function (e) {
      if (e.defaultPrevented) return;
      if (e.button !== 0) return;
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;

      var link = e.target && e.target.closest ? e.target.closest('a[href]') : null;
      if (!link) return;
      if (link.target && link.target !== '' && link.target !== '_self') {
        // External credit links etc. already use target=_blank — leave alone
        var hrefAttr = (link.getAttribute('href') || '').trim();
        try {
          var u = new URL(hrefAttr, window.location.href);
          if (u.origin !== window.location.origin) return;
        } catch (_) {
          return;
        }
      }

      var href = link.href || link.getAttribute('href');
      if (shouldKeepSameWindow(link, href)) return;

      e.preventDefault();
      e.stopPropagation();
      window.openAppPage(href, titleFromLink(link, href));
    },
    false
  );
})();
