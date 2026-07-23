// Theme management
//
// Two controls live in the header:
//  - #theme-toggle flips between the classic dark and light looks.
//  - #theme-cycle steps through the extra skins; after the last one it
//    wraps back to the original dark theme.
(function() {
  const CYCLE = ['dark', 'sakura', 'synthwave', 'matrix', 'ocean', 'sunset', 'coffee'];
  const NAMES = {
    dark: 'Dark', light: 'Light',
    sakura: 'Sakura', synthwave: 'Synthwave',
    matrix: 'Matrix', ocean: 'Ocean', sunset: 'Sunset', coffee: 'Coffee',
  };

  function apply(theme) {
    if (!NAMES[theme]) theme = 'dark';
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('median_theme', theme);
    const cycleBtn = document.getElementById('theme-cycle');
    if (cycleBtn) cycleBtn.title = `Cycle theme (current: ${NAMES[theme]})`;
  }

  apply(localStorage.getItem('median_theme') || 'dark');

  document.addEventListener('DOMContentLoaded', () => {
    apply(document.documentElement.getAttribute('data-theme'));

    const toggleBtn = document.getElementById('theme-toggle');
    if (toggleBtn) {
      toggleBtn.addEventListener('click', () => {
        const current = document.documentElement.getAttribute('data-theme');
        apply(current === 'dark' ? 'light' : 'dark');
      });
    }

    const cycleBtn = document.getElementById('theme-cycle');
    if (cycleBtn) {
      cycleBtn.addEventListener('click', () => {
        const current = document.documentElement.getAttribute('data-theme');
        // 'light' isn't part of the cycle: indexOf → -1 → next is CYCLE[0] (dark)
        const next = CYCLE[(CYCLE.indexOf(current) + 1) % CYCLE.length];
        apply(next);
      });
    }
  });
})();
