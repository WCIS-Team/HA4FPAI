/* Shared iframe theme adapter. Loaded by ggradio and the local Klar proxy.
 * Only an explicitly embedded document accepts its immediate parent's theme.
 * Standalone preferences (including ggradio-theme and klar_theme) are untouched.
 */
(() => {
  'use strict';
  if (window.parent === window) return;
  const params = new URLSearchParams(window.location.search);
  if (params.get('console_embed') !== '1') return;

  let parentOrigin;
  try {
    const supplied = new URL(params.get('console_parent_origin'));
    if (!['http:', 'https:'].includes(supplied.protocol)) return;
    parentOrigin = supplied.origin;
    if (params.get('console_parent_origin') !== parentOrigin) return;
    if (document.referrer && new URL(document.referrer).origin !== parentOrigin) return;
  } catch (_) { return; }

  const root = document.documentElement;
  let theme = params.get('console_theme') === 'light' ? 'light' : 'dark';
  root.dataset.consoleEmbedded = '1';
  const style = document.createElement('style');
  style.textContent = `
    :root[data-console-embedded="1"] #themeToggle,
    :root[data-console-embedded="1"] #theme-toggle,
    :root[data-console-embedded="1"] .theme-toggle,
    :root[data-console-embedded="1"] .wizard-theme,
    :root[data-console-embedded="1"] [data-console-theme-control] {
      display: none !important;
    }`;
  (document.head || root).appendChild(style);

  // Klar's settings use a localized "This screen" toggle group, not an id.
  // These labels are from its shipped UI bundle (operatorChrome translations).
  const klarAppearanceLabels = new Set([
    'Acest ecran', 'An scáileán seo', 'An skrin ma', 'Aquesta pantalla',
    'Bu ekran', 'Cet écran', 'Den här skärmen', 'Denne skjermen', 'Denne skærm',
    'Die Oberflächi', 'Diese Oberfläche', 'Dit scherm', 'Dës Uewerfläch',
    'Esta pantalla', 'Esta tela', 'Este ecrã', 'Ez a képernyő', 'Hierdie skerm',
    'Layar ini', 'Màn hình này', 'Ovaj ekran', 'Ovaj zaslon', 'Pantaila hau',
    'Questa schermata', 'See ekraan', 'Skrin ini', 'Skrini hii', 'Ta zaslon',
    'Tato obrazovka', 'Ten ekran', 'This screen', 'Táto obrazovka', 'Tämä näyttö',
    'Y sgrin hon', 'Þessi skjár', 'Šis ekranas', 'Šis ekrāns', 'Αυτή η οθόνη',
    'Овај екран', 'Този екран', 'Цей екран', 'Энэ дэлгэц', 'Այս էկրանը',
    'המסך הזה', 'این صفحه', 'هذه الشاشة', 'یہ اسکرین', 'यह स्क्रीन',
    'यो स्क्रिन', 'ही स्क्रीन', 'এই পর্দা', 'ਇਹ ਸਕਰੀਨ', 'આ સ્ક્રીન',
    'இந்த திரை', 'ఈ స్క్రీన్', 'ಈ ಪರದೆ', 'ഈ സ്ക്രീൻ', 'หน้าจอนี้',
    'ეს ეკრანი', 'この画面', '呢個畫面', '这个界面', '這個畫面', '이 화면'
  ]);

  function apply() {
    // React's app shell and newly mounted dialogs can carry their own theme.
    for (const el of [root, ...document.querySelectorAll('[data-theme]')]) {
      if (el.dataset.theme !== theme) el.dataset.theme = theme;
    }
    root.classList.toggle('dark', theme === 'dark');
    root.style.colorScheme = theme;
    for (const group of document.querySelectorAll('[data-slot="toggle-group"][aria-label]')) {
      if (klarAppearanceLabels.has(group.getAttribute('aria-label')) &&
          !group.hasAttribute('data-console-theme-control')) {
        group.setAttribute('data-console-theme-control', '');
      }
    }
  }

  window.addEventListener('message', event => {
    if (event.source !== window.parent || event.origin !== parentOrigin) return;
    const data = event.data;
    if (!data || data.type !== 'ha4fpai:theme' ||
        !['light', 'dark'].includes(data.theme)) return;
    theme = data.theme;
    apply();
  });
  new MutationObserver(apply).observe(root, {
    subtree: true, childList: true, attributes: true, attributeFilter: ['data-theme']
  });
  apply();
  function ready() {
    window.parent.postMessage({type: 'ha4fpai:theme-ready'}, parentOrigin);
  }
  window.addEventListener('pageshow', ready);
  ready();
})();
