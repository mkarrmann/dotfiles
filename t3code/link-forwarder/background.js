// Every outside http(s) link a T3 Code window opens goes to Google Chrome
// through bin-macos/t3-open-in-chrome. page.js sends most before any tab
// exists; a tab that opens here anyway (from another site's frame, or a window
// opened blank and pointed at a link later) is forwarded and closed. A link the
// host fails to open stays here rather than vanishing.
const HOST = "com.mkarrmann.t3_open_in_chrome";

// A tab a page opens often has no URL yet when created; its first one decides.
const awaitingUrl = new Set();

const forwarded = (url) => {
  const { protocol, hostname } = new URL(url);
  return (protocol === "http:" || protocol === "https:") && !hostname.endsWith(".t3.localhost");
};

const openInChrome = async (url) => {
  try {
    const reply = await chrome.runtime.sendNativeMessage(HOST, { url });
    if (reply?.ok) return true;
    console.error("t3-open-in-chrome refused", url, reply);
  } catch (error) {
    console.error("t3-open-in-chrome failed", url, error);
  }
  return false;
};

const forward = async (tabId, url) => {
  if (forwarded(url) && (await openInChrome(url))) await chrome.tabs.remove(tabId);
};

chrome.runtime.onMessage.addListener((message, sender) => {
  const url = message?.url;
  if (sender.tab && typeof url === "string" && forwarded(url)) openInChrome(url);
});

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
