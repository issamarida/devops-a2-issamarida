// The dashboard: the account's watchlist with live prices.
//
// The assets and prices come from the watchlist service. The two alert numbers
// come from the alerts service. If alerts does not answer, those two numbers
// go blank and the rest of the page keeps working.

import { call } from "./api.js";
import { errorText, flash, hideStarting, showStarting, startPage, watchAlerts } from "./page.js";
import { loadDetails, startQuotes } from "./quotes.js";

const rows = document.querySelector("[data-asset-rows]");
const template = document.querySelector("#asset-row");
const form = document.querySelector("[data-add-form]");
// Set in start(): asks the alerts service for its numbers again.
let refreshAlerts = async () => {};

function setStat(name, value) {
  document.querySelector(`[data-stat='${name}']`).textContent = value;
}

function drawAssets(items) {
  rows.replaceChildren();
  for (const item of items) {
    const row = template.content.firstElementChild.cloneNode(true);
    row.dataset.details = item.ticker;
    row.querySelector("[data-text='ticker']").textContent = item.ticker;
    row.querySelector("[data-text='name']").textContent = item.name;
    row.querySelector("[data-text='notes']").textContent = item.notes || "No note added";
    row.querySelector("[data-text='added']").textContent = "Added " + item.added_at.slice(0, 10);
    // quotes.js fills every cell that names a ticker.
    for (const cell of row.querySelectorAll("[data-field]")) cell.dataset.quote = item.ticker;
    const remove = row.querySelector("[data-remove]");
    remove.setAttribute("aria-label", "Remove " + item.ticker);
    remove.addEventListener("click", () => removeAsset(item.ticker));
    rows.append(row);
  }
  document.querySelector("[data-loading]").hidden = true;
  document.querySelector("[data-assets]").hidden = items.length === 0;
  document.querySelector("[data-no-assets]").hidden = items.length > 0;
  document.querySelector("[data-asset-badge]").textContent = items.length + " assets";
  setStat("assets", items.length);
}

// Ask the watchlist service for the list and draw it. On the first load this
// waits for a sleeping service and says so.
async function loadAssets() {
  const body = await call("watchlist", "/watchlist", { onWaiting: showStarting });
  hideStarting();
  drawAssets(body.items);
  loadDetails();
}

async function addAsset(event) {
  event.preventDefault();
  const button = form.querySelector("button");
  button.disabled = true;
  const data = new FormData(form);
  try {
    const item = await call("watchlist", "/watchlist", {
      method: "POST",
      body: { ticker: data.get("ticker"), name: data.get("name"), notes: data.get("notes") },
    });
    form.reset();
    flash("success", `${item.ticker} is on your watchlist.`);
    await loadAssets();
  } catch (error) {
    flash("error", errorText(error));
  }
  button.disabled = false;
}

async function removeAsset(ticker) {
  try {
    await call("watchlist", "/watchlist/" + encodeURIComponent(ticker), { method: "DELETE" });
    flash("success", `${ticker} was removed. Its alert rules are paused, not deleted.`);
    await loadAssets();
    // The count of active rules changed too. That number belongs to alerts.
    refreshAlerts().catch(() => {});
  } catch (error) {
    flash("error", errorText(error));
  }
}

// Ticker suggestions while typing. They come from the watchlist service and
// are optional: without them the form works the same.
function startSearch() {
  const ticker = form.elements.ticker;
  const name = form.elements.name;
  const options = document.querySelector("#ticker-options");
  let found = {};
  let wait = null;
  ticker.addEventListener("input", () => {
    const symbol = ticker.value.trim().toUpperCase();
    if (found[symbol] && !name.value) name.value = found[symbol];
    clearTimeout(wait);
    if (!symbol) return;
    wait = setTimeout(async () => {
      let results;
      try {
        results = (await call("watchlist", "/api/search?q=" + encodeURIComponent(symbol), { retry: false })).results;
      } catch {
        return;
      }
      found = {};
      options.replaceChildren();
      for (const result of results) {
        found[result.symbol] = result.name;
        const option = document.createElement("option");
        option.value = result.symbol;
        option.label = result.name;
        options.append(option);
      }
      if (found[symbol] && !name.value) name.value = found[symbol];
    }, 300);
  });
}

// The two numbers that belong to the alerts service.
function drawAlertNumbers(body, history) {
  setStat("active", body.rules.filter((rule) => rule.status === "active").length);
  setStat("events", history.count);
  document.querySelector("[data-alerts-down]").hidden = true;
}

function alertsDown() {
  setStat("active", "—");
  setStat("events", "—");
  document.querySelector("[data-alerts-down]").hidden = false;
}

async function start() {
  if (!startPage()) return;
  form.addEventListener("submit", addAsset);
  startSearch();
  // Runs by itself from here on, so a slow watchlist does not hold it back.
  refreshAlerts = watchAlerts(drawAlertNumbers, alertsDown);
  try {
    await loadAssets();
  } catch (error) {
    hideStarting();
    document.querySelector("[data-loading]").hidden = true;
    flash("error", errorText(error) + " Reload the page to try again.");
    return;
  }
  startQuotes();
}

start();
