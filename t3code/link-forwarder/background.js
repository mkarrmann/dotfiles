// Every http(s) tab that opens here (window.open, even with noopener;
// target=_blank; Cmd-click) goes to Google Chrome through
// bin-macos/t3-open-in-chrome, except T3 Code's own origins. A link the host
// fails to open stays here rather than vanishing.
const HOST = "com.mkarrmann.t3_open_in_chrome";

// A tab a page opens often has no URL yet when created; its first one decides.
const awaitingUrl = new Set();

const forwarded = (url) => {
  const { protocol, hostname } = new URL(url);
  return (protocol === "http:" || protocol === "https:") && !hostname.endsWith(".t3.localhost");
};

const forward = async (tabId, url) => {
  if (!forwarded(url)) return;
  try {
    const reply = await chrome.runtime.sendNativeMessage(HOST, { url });
    if (reply?.ok) await chrome.tabs.remove(tabId);
    else console.error("t3-open-in-chrome refused", url, reply);
  } catch (error) {
    console.error("t3-open-in-chrome failed", url, error);
  }
};

chrome.tabs.onCreated.addListener((tab) => {
  const url = tab.pendingUrl || tab.url;
  if (url && url !== "about:blank") forward(tab.id, url);
  else awaitingUrl.add(tab.id);
});

chrome.tabs.onUpdated.addListener((tabId, { url }) => {
  if (!url || url === "about:blank" || !awaitingUrl.delete(tabId)) return;
  forward(tabId, url);
});

chrome.tabs.onRemoved.addListener((tabId) => awaitingUrl.delete(tabId));
