// Carries page.js's links, which it cannot send itself from the page's world,
// to background.js.
document.addEventListener("t3-open-in-chrome", (event) => {
  if (typeof event.detail === "string") chrome.runtime.sendMessage({ url: event.detail });
});
