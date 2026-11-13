// chat.newLocal (mod+o, t3code/keybindings.json) starts its thread in the project of
// the thread or draft on screen; only on a page without one does T3 Code fall back to
// the window's project order, which bin/t3-window-scope puts the window's project
// first in. So on a thread or draft the press is held, the page replaced by /new
// (no project of its own; TanStack Router follows history.pushState/replaceState,
// hence the MAIN world), and the press let through once that page shows.
const NO_PROJECT_PATH = "/new";
// The text /new shows without a prompt (apps/web/src/routes/_chat.new.tsx).
const NO_PROJECT_TEXT = "Could not open the link";
const WAIT_MS = 2000;
const RETRIES = 8;
const RETRY_MS = 250;
const APPLE = /^(Mac|iP)/.test(navigator.platform);

const isNewLocal = (event) =>
  event.code === "KeyO" &&
  !event.shiftKey &&
  !event.altKey &&
  (APPLE ? event.metaKey && !event.ctrlKey : event.ctrlKey && !event.metaKey);

const onThreadOrDraft = () => /^\/(?!settings\/)[^/]+\/[^/]+\/?$/.test(location.pathname);

const showsNoProjectPage = () =>
  new Promise((resolve) => {
    const shown = () => location.pathname === NO_PROJECT_PATH && document.body.innerText.includes(NO_PROJECT_TEXT);
    if (shown()) return resolve(true);
    const observer = new MutationObserver(() => shown() && done(true));
    const timer = setTimeout(() => done(false), WAIT_MS);
    const done = (ok) => {
      observer.disconnect();
      clearTimeout(timer);
      resolve(ok);
    };
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  });

addEventListener(
  "keydown",
  (event) => {
    if (
      !event.isTrusted ||
      event.repeat ||
      !isNewLocal(event) ||
      !onThreadOrDraft() ||
      document.activeElement?.closest(".xterm") ||
      document.querySelector('[role="dialog"]:not([data-closed])')
    ) {
      return;
    }
    event.preventDefault();
    event.stopImmediatePropagation();
    const from = { state: history.state, url: location.href };
    const key = Math.random().toString(36).slice(2);
    history.replaceState({ ...history.state, key, __TSR_key: key }, "", NO_PROJECT_PATH);
    const init = {
      key: event.key,
      code: event.code,
      metaKey: event.metaKey,
      ctrlKey: event.ctrlKey,
      bubbles: true,
      cancelable: true,
    };
    showsNoProjectPage().then(async (shown) => {
      // T3 Code can pass over a press while a closing dialog still counts as open.
      for (let tries = shown ? RETRIES : 0; tries > 0 && location.pathname === NO_PROJECT_PATH; tries--) {
        document.body.dispatchEvent(new KeyboardEvent("keydown", init));
        await new Promise((resolve) => setTimeout(resolve, RETRY_MS));
      }
      if (location.pathname === NO_PROJECT_PATH) history.replaceState(from.state, "", from.url);
    });
  },
  true,
);
