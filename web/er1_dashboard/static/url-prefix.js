(() => {
  'use strict';

  const LEGACY_PREFIX = '/admin/game-master/proxy';
  const WEBSITE_PREFIX = '/admin/game-master';
  const API_ROOT = `/${'api'}`;

  function normalizePrefix(value) {
    const prefix = String(value || '').trim().replace(/\/+$/, '');
    if (!prefix || prefix === '/') return '';
    if (
      !prefix.startsWith('/')
      || prefix.startsWith('//')
      || prefix.includes('//')
      || /[?#\\\r\n\0]/.test(prefix)
      || prefix.split('/').some((segment) => segment === '.' || segment === '..')
    ) {
      return '';
    }
    return prefix;
  }

  function currentPrefix() {
    return normalizePrefix(document.body?.dataset?.urlPrefix || '');
  }

  function pathPart(value) {
    return String(value || '').split(/[?#]/, 1)[0];
  }

  function knownPrefixes() {
    return [...new Set([currentPrefix(), LEGACY_PREFIX, WEBSITE_PREFIX].filter(Boolean))]
      .sort((left, right) => right.length - left.length);
  }

  function hasPrefix(value, prefix) {
    const pathname = pathPart(value);
    return pathname === prefix || pathname.startsWith(`${prefix}/`);
  }

  function withPrefix(path) {
    const value = String(path || '');
    const prefix = currentPrefix();
    if (!prefix || !value.startsWith('/') || value.startsWith('//')) return value;
    if (knownPrefixes().some((item) => hasPrefix(value, item))) return value;
    return `${prefix}${value}`;
  }

  function canonicalApiPath(path) {
    const value = String(path || '');
    for (const prefix of knownPrefixes()) {
      if (!hasPrefix(value, prefix)) continue;
      const canonical = value.slice(prefix.length) || '/';
      if (canonical === API_ROOT || canonical.startsWith(`${API_ROOT}/`)) return canonical;
    }
    return value;
  }

  window.ER1Urls = Object.freeze({
    canonicalApiPath,
    currentPrefix,
    normalizePrefix,
    withPrefix,
  });
})();
