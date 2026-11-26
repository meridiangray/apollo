// A button's label sits in <span class=bt> so the stylesheet can trim it to its cap height (text-box): the label is then centred on the
// button by its capital letters rather than by the font's line box, which sits a pixel high or low from one font to the next.
// The wrapping is automatic for every .btn and .rp, however the page draws it, so no template needs to know.
'use strict';
(() => {
  const SEL = '.btn,.rp';
  const wrap = el => {
    if (el.nodeType !== 1) return;
    for (const b of el.matches(SEL) ? [el] : el.querySelectorAll(SEL)) {
      for (const n of [...b.childNodes]) {
        if (n.nodeType !== 3 || !n.nodeValue.trim()) continue;
        const s = document.createElement('span');
        s.className = 'bt';
        b.replaceChild(s, n);
        s.append(n);
      }
    }
  };
  const scan = nodes => nodes.forEach(n => wrap(n.nodeType === 3 ? n.parentElement || n : n));
  new MutationObserver(ms => { for (const m of ms) scan(m.addedNodes); }).observe(document.documentElement, {childList: true, subtree: true});
  wrap(document.body);
})();
