// Runs in each T3 Code page, in the page's own JavaScript world and before the
// app: an outside http(s) link the page opens (window.open, or a click on a
// link nothing else handled) goes to relay.js and on to Google Chrome without
// this browser ever opening a tab for it. background.js still forwards a tab
// that gets past this, such as one opened from another site's frame.
(() => {
  const outside = (raw) => {
    let url;
    try {
      url = new URL(raw, location.href);
    } catch {
      return null;
    }
    const web = url.protocol === "http:" || url.protocol === "https:";
    return web && !url.hostname.endsWith(".t3.localhost") ? url.href : null;
  };
  const send = (url) => document.dispatchEvent(new CustomEvent("t3-open-in-chrome", { detail: url }));

  const open = window.open;
  window.open = function (url, ...rest) {
    const target = url == null ? null : outside(String(url));
    if (target === null) return open.call(this, url, ...rest);
    send(target);
    return null;
  };

  // Bubbling to window, this runs after the app's own handlers; one that took
  // the click prevented its default and, opening the link, came through
  // window.open above.
  const onClick = (event) => {
    if (event.defaultPrevented) return;
    if (event.type === "click" ? event.button !== 0 : event.button !== 1) return;
    const link = event.composedPath().find((node) => node instanceof HTMLAnchorElement);
    if (!link || !link.href || link.hasAttribute("download")) return;
    const target = outside(link.href);
    if (target === null) return;
    event.preventDefault();
    send(target);
  };
  window.addEventListener("click", onClick);
  window.addEventListener("auxclick", onClick);
})();
