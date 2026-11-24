// The price alerts page: rules, their status and the trigger history.
//
// Rules and history come from the alerts service. The list of tickers to
// choose from and the live prices come from the watchlist service. When the
// watchlist does not answer, the rules and the history are still shown and
// only the parts that need it say so.

import { call } from "./api.js";
import { errorText, flash, hideStarting, keepFresh, money, showStarting, startPage, watchAlerts } from "./page.js";
import { startQuotes } from "./quotes.js";

const ruleRows = document.querySelector("[data-rule-rows]");
const eventRows = document.querySelector("[data-event-rows]");
const form = document.querySelector("[data-rule-form]");
const checkButton = document.querySelector("[data-check]");

// Set in start(): asks the alerts service for rules and history again.
let refreshAlerts = async () => {};
// What is on screen now, so a table is only rebuilt when something changed.
let shownAlerts = "";
let shownTickers = null;

const show = (selector, visible) => {
  document.querySelector(selector).hidden = !visible;
};

function setStat(name, value) {
  document.querySelector(`[data-stat='${name}']`).textContent = value;
}

function drawRules(rules) {
  const template = document.querySelector("#rule-row");
  ruleRows.replaceChildren();
  for (const rule of rules) {
    const row = template.content.firstElementChild.cloneNode(true);
    const threshold = money.format(rule.threshold);
    row.querySelector("[data-text='ticker']").textContent = rule.ticker;
    row.querySelector("[data-text='condition']").textContent = rule.condition;
    row.querySelector("[data-text='threshold']").textContent = threshold;
    // quotes.js fills the cells that name a ticker.
    row.querySelector("[data-field='price']").dataset.quote = rule.ticker;
    if (rule.status === "active") {
      const distance = row.querySelector("[data-distance]");
      distance.dataset.quote = rule.ticker;
      distance.dataset.field = "distance";
      distance.dataset.threshold = rule.threshold;
      distance.dataset.condition = rule.condition;
    }
    const status = row.querySelector("[data-status]");
    status.textContent = rule.status;
    status.classList.add(rule.status);
    const remove = row.querySelector("[data-delete]");
    remove.setAttribute("aria-label", `Delete ${rule.ticker} ${rule.condition} ${threshold} rule`);
    remove.addEventListener("click", () => deleteRule(rule.id));
    ruleRows.append(row);
  }
  show("[data-rules]", rules.length > 0);
  show("[data-no-rules]", rules.length === 0);
  document.querySelector("[data-rule-badge]").textContent = rules.length + " rules";
  setStat("active", rules.filter((rule) => rule.status === "active").length);
  setStat("dormant", rules.filter((rule) => rule.status === "dormant").length);
}

function drawHistory(history) {
  const template = document.querySelector("#event-row");
  eventRows.replaceChildren();
  for (const event of history.events) {
    const row = template.content.firstElementChild.cloneNode(true);
    row.querySelector("[data-text='ticker']").textContent = event.ticker;
    row.querySelector("[data-text='rule']").textContent = `${event.condition} ${money.format(event.threshold)}`;
    row.querySelector("[data-text='observed']").textContent = money.format(event.observed_price);
    // 2026-11-24T10:15:00+00:00 is shown as 2026-11-24 10:15
    row.querySelector("[data-text='when']").textContent = event.triggered_at.slice(0, 16).replace("T", " ");
    eventRows.append(row);
  }
  show("[data-events]", history.events.length > 0);
  show("[data-no-events]", history.events.length === 0);
  setStat("events", history.count);
}

// Called with every answer from the alerts service, every five seconds.
function drawAlerts(body, history) {
  hideStarting();
  show("[data-loading]", false);
  // The alerts service says here whether the watchlist answered it.
  show("[data-watchlist-down]", !body.watchlist_available);
  const answer = JSON.stringify([body, history]);
  if (answer === shownAlerts) return;
  shownAlerts = answer;
  drawRules(body.rules);
  drawHistory(history);
}

function alertsDown() {
  showStarting("alerts");
}

// The tickers to choose from are the account's watchlist, which belongs to the
// watchlist service. Asked again every 15 seconds, so the form comes back by
// itself after the watchlist was away.
async function loadTickers() {
  let items;
  try {
    items = (await call("watchlist", "/watchlist", { retry: false })).items;
  } catch (error) {
    show("[data-tickers-loading]", false);
    show("[data-rule-form]", false);
    show("[data-no-tickers]", false);
    show("[data-tickers-down]", true);
    shownTickers = null;
    throw error;
  }
  const tickers = items.map((item) => item.ticker).join(",");
  if (tickers === shownTickers) return;
  shownTickers = tickers;
  const select = form.elements.ticker;
  select.replaceChildren();
  for (const item of items) {
    const option = document.createElement("option");
    option.value = item.ticker;
    option.textContent = item.ticker;
    select.append(option);
  }
  show("[data-tickers-loading]", false);
  show("[data-tickers-down]", false);
  show("[data-rule-form]", items.length > 0);
  show("[data-no-tickers]", items.length === 0);
}

async function createRule(event) {
  event.preventDefault();
  const button = form.querySelector("button");
  button.disabled = true;
  const data = new FormData(form);
  try {
    const rule = await call("alerts", "/alerts", {
      method: "POST",
      body: {
        ticker: data.get("ticker"),
        condition: data.get("condition"),
        threshold: Number(data.get("threshold")),
      },
    });
    form.elements.threshold.value = "";
    flash("success", `Rule saved: ${rule.ticker} ${rule.condition} ${money.format(rule.threshold)}.`);
    await refreshAlerts().catch(() => {});
  } catch (error) {
    // With the watchlist down the alerts service answers 503 and says that
    // nothing was saved. That message is shown as it is.
    flash("error", errorText(error));
  }
  button.disabled = false;
}

async function deleteRule(id) {
  try {
    await call("alerts", "/alerts/" + id, { method: "DELETE" });
    flash("success", "Rule deleted. Its history stays.");
    await refreshAlerts().catch(() => {});
  } catch (error) {
    flash("error", errorText(error));
  }
}

async function checkNow() {
  checkButton.disabled = true;
  try {
    const result = await call("alerts", "/alerts/evaluate", { method: "POST" });
    const fired = result.fired.map((rule) => `${rule.ticker} ${rule.condition} ${money.format(rule.threshold)}`);
    let text = fired.length ? "Fired: " + fired.join(", ") + "." : "Checked. No rule fired.";
    if (result.unavailable_tickers.length) {
      text += ` No fresh price for ${result.unavailable_tickers.join(", ")}, so those rules stay active.`;
    }
    flash(fired.length ? "success" : "notice", text);
    await refreshAlerts().catch(() => {});
  } catch (error) {
    flash("error", errorText(error));
  }
  checkButton.disabled = false;
}

function start() {
  if (!startPage()) return;
  form.addEventListener("submit", createRule);
  checkButton.addEventListener("click", checkNow);
  refreshAlerts = watchAlerts(drawAlerts, alertsDown);
  keepFresh(loadTickers, 15000);
  startQuotes();
}

start();
