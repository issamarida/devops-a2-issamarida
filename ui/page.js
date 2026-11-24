// What the dashboard and the price alerts page share: the signed-in frame,
// messages, number formats and the loop that keeps a page up to date.

import { ApiError, call, getToken, getUsername, signOut } from "./api.js";

export const money = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

// The names the pages use for the two services in a message.
const NAMES = { watchlist: "watchlist", alerts: "price alerts" };

// A new element with a class and, if given, text. Text is never read as HTML.
export function make(tag, className = "", text = "") {
  const element = document.createElement(tag);
  element.className = className;
  element.textContent = text;
  return element;
}

// Send a visitor without a token to sign in, and fill in the account corner.
// Returns false when the page is about to leave.
export function startPage() {
  if (!getToken()) {
    signOut();
    return false;
  }
  const name = getUsername();
  document.querySelector("[data-account-name]").textContent = name;
  document.querySelector("[data-avatar]").textContent = name.slice(0, 1).toUpperCase();
  document.querySelector("[data-sign-out]").addEventListener("click", () => signOut());
  return true;
}

// Show one message at the top of the page. kind is success, error or notice.
export function flash(kind, text) {
  const box = document.querySelector("[data-flash]");
  box.className = "message " + kind;
  box.textContent = text;
  box.hidden = false;
  box.scrollIntoView({ block: "nearest" });
}

// The words for an error from call(): the service's own message, or ours when
// the service never answered.
export function errorText(error) {
  if (error instanceof ApiError) return error.message;
  if (!NAMES[error.service]) return "Something went wrong. Try again.";
  return `The ${NAMES[error.service]} service is not answering. It may be starting up. Try again in a moment.`;
}

// The notice shown while a page waits for a sleeping service.
export function showStarting(service) {
  const box = document.querySelector("[data-starting]");
  box.textContent =
    `The ${NAMES[service]} service is starting up. It sleeps when nobody is using it, ` +
    "and waking it can take up to a minute. Trying again…";
  box.hidden = false;
}

export function hideStarting() {
  document.querySelector("[data-starting]").hidden = true;
}

// A small box in the corner that goes away by itself.
export function toast(title, text) {
  const box = make("div", "toast");
  box.append(make("strong", "", title), document.createTextNode(text));
  document.querySelector("[data-toasts]").append(box);
  setTimeout(() => box.remove(), 8000);
}

// --- Keeping a page up to date ---------------------------------------------
//
// keepFresh runs a job again and again, but only while someone is looking: it
// stops when the tab is hidden and after five minutes without a key or pointer
// movement. On Azure every request keeps a container and its database awake,
// so a forgotten tab must not keep them running all night.

const IDLE_MS = 5 * 60 * 1000;
const SLOWEST_MS = 30000;
let lastActive = Date.now();
const loops = [];

const isIdle = () => Date.now() - lastActive > IDLE_MS;

// Say on the page that the numbers stopped moving, and why.
function showPaused(paused) {
  document.querySelector("[data-paused]").hidden = !paused;
}

function noteActivity() {
  const wasIdle = isIdle();
  lastActive = Date.now();
  if (wasIdle) {
    showPaused(false);
    for (const run of loops) run();
  }
}

for (const name of ["pointermove", "pointerdown", "keydown", "scroll"]) {
  addEventListener(name, noteActivity, { passive: true });
}

// Run job now and then every everyMs. A job that throws is tried again more
// and more slowly, up to every 30 seconds, until it works again.
export function keepFresh(job, everyMs) {
  let wait = everyMs;
  let timer = null;
  let busy = false;

  async function run() {
    clearTimeout(timer);
    if (busy || document.hidden) return;
    if (isIdle()) {
      showPaused(true);
      return;
    }
    busy = true;
    try {
      await job();
      wait = everyMs;
    } catch {
      wait = Math.min(wait * 2, SLOWEST_MS);
    }
    busy = false;
    timer = setTimeout(run, wait);
  }

  loops.push(run);
  document.addEventListener("visibilitychange", run);
  run();
}

// --- The alerts service, as both pages see it --------------------------------
//
// Asks the alerts service for the rules and the trigger history every five
// seconds and hands both to onAlerts. A rule can fire in the background, so a
// higher count than last time means something new happened and the page says
// so with a toast. onDown is called while the alerts service does not answer.
//
// Returns the function that asks, so a page can ask at once after a change.
export function watchAlerts(onAlerts, onDown) {
  let seen = null;

  async function refresh() {
    let rules;
    let history;
    try {
      [rules, history] = await Promise.all([
        call("alerts", "/alerts", { retry: false }),
        call("alerts", "/alerts/events", { retry: false }),
      ]);
    } catch (error) {
      onDown(error);
      throw error;
    }
    if (seen !== null && history.count > seen && history.events.length) {
      const event = history.events[0];
      toast(
        "Alert fired: " + event.ticker,
        ` Price ${event.condition} ${money.format(event.threshold)}, observed ${money.format(event.observed_price)}.`,
      );
    }
    seen = history.count;
    onAlerts(rules, history);
  }

  keepFresh(refresh, 5000);
  return refresh;
}
