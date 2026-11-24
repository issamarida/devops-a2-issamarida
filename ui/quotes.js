// Live prices. Asks the watchlist service once a second and fills every cell
// marked with data-quote="<ticker>" and data-field="<what to show>".
//
// The browser never talks to Finnhub and never sees the API key. The prices
// come from GET /api/quotes on the watchlist service.

import { call } from "./api.js";
import { keepFresh, make, money } from "./page.js";

const SVG = "http://www.w3.org/2000/svg";
const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 });
const clock = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: "UTC",
});
const day = new Intl.DateTimeFormat("en-GB", {
  day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "UTC",
});
const labels = { live: "Live", connected: "Connected", connecting: "Connecting", offline: "Offline" };
const sessions = { "pre-market": "Pre-market", regular: "Open", "post-market": "After hours" };

// The last price seen for each ticker, to flash a cell when it changes.
const last = {};

// A quote from an earlier session shows its date, so a Friday close never looks current.
const stamp = (at) => (Date.now() / 1000 - at > 12 * 3600 ? day : clock).format(new Date(at * 1000));
const signed = (value, digits) => (value > 0 ? "+" : value < 0 ? "−" : "") + Math.abs(value).toFixed(digits);
const direction = (value) => (value > 0 ? "up" : value < 0 ? "down" : "flat");

// What the "To trigger" cell of a rule says. The comparison is strict, as in
// the alerts service: a price equal to the threshold does not fire the rule.
export function distanceText(price, threshold, condition) {
  const above = condition === "above";
  const met = above ? price > threshold : price < threshold;
  if (met) return "Met, fires on next check";
  const gap = (Math.abs(threshold - price) / price) * 100;
  return (above ? "▲ " : "▼ ") + gap.toFixed(2) + "% " + (above ? "rise" : "fall");
}

function setStatus(state, at) {
  const pill = document.querySelector("[data-live]");
  pill.dataset.state = state;
  pill.querySelector("[data-live-label]").textContent = labels[state] || "Connecting";
  pill.querySelector("[data-live-time]").textContent = at ? clock.format(new Date(at * 1000)) + " UTC" : "";
}

function setMarket(info) {
  const market = document.querySelector("[data-market]");
  const label = market.querySelector("[data-market-label]");
  if (!info) {
    market.dataset.state = "closed";
    label.textContent = "US market";
    return;
  }
  const name = info.open ? sessions[info.session] || "Open" : info.holiday ? "Closed for " + info.holiday : "Closed";
  market.dataset.state = info.open ? (info.session === "regular" ? "open" : "extended") : "closed";
  label.textContent = "US market · " + name;
}

// A line of recent trade prices.
function drawSpark(cell, quote) {
  const points = quote.spark || [];
  cell.replaceChildren();
  if (points.length < 2) {
    cell.append(make("span", "spark-empty", quote.source === "live" ? "Collecting trades" : "No trades streaming"));
    return;
  }
  let low = Math.min(...points);
  let high = Math.max(...points);
  const first = points[0];
  const latest = points[points.length - 1];
  const summary = `Last ${Math.round((points.length * 5) / 60)} min, ${money.format(first)} to ${money.format(latest)}`;
  const title = document.createElementNS(SVG, "title");
  title.textContent = `${summary} (low ${money.format(low)}, high ${money.format(high)})`;
  if (high === low) {
    high += 0.01;
    low -= 0.01;
  }
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", "0 0 100 30");
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("class", "spark " + direction(latest - first));
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", summary);
  const x = (i) => ((i / (points.length - 1)) * 100).toFixed(2);
  const y = (p) => (28 - ((p - low) / (high - low)) * 26).toFixed(2);
  const line = document.createElementNS(SVG, "polyline");
  line.setAttribute("points", points.map((p, i) => x(i) + "," + y(p)).join(" "));
  svg.append(title, line);
  cell.append(svg);
}

// A low to high bar with a marker at the current price.
function drawRange(cell, low, high, price, open) {
  if (!(low > 0 && high > 0 && high >= low)) {
    cell.textContent = "—";
    return;
  }
  let track = cell.querySelector(".range-track");
  if (!track) {
    cell.replaceChildren();
    track = make("div", "range-track");
    track.append(make("span", "range-open"), make("span", "range-mark"));
    const ends = make("div", "range-ends");
    ends.append(make("span"), make("span"));
    cell.append(track, ends);
  }
  const at = (value) => (high === low ? 50 : Math.min(100, Math.max(0, ((value - low) / (high - low)) * 100))) + "%";
  track.querySelector(".range-mark").style.left = at(price);
  const openMark = track.querySelector(".range-open");
  openMark.hidden = !(open > 0);
  if (open > 0) openMark.style.left = at(open);
  const [lowLabel, highLabel] = cell.querySelectorAll(".range-ends span");
  lowLabel.textContent = money.format(low);
  highLabel.textContent = money.format(high);
  cell.title = `Range ${money.format(low)} to ${money.format(high)}, now ${money.format(price)}`;
}

function drawPrice(cell, ticker, quote) {
  const value = cell.querySelector(".price") || make("span", "price");
  const source = cell.querySelector(".src") || make("span", "src");
  if (!value.isConnected) cell.replaceChildren(value, source);
  value.textContent = money.format(quote.price);
  source.textContent = (quote.source === "live" ? "Trade " : "Close ") + stamp(quote.at);
  const before = last[ticker];
  if (before !== undefined && before !== quote.price) {
    value.classList.remove("tick-up", "tick-down");
    void value.offsetWidth; // restart the flash
    value.classList.add(quote.price > before ? "tick-up" : "tick-down");
    setTimeout(() => value.classList.remove("tick-up", "tick-down"), 350);
  }
}

function drawChange(cell, quote) {
  cell.className = "num move";
  if (quote.change === null) {
    cell.textContent = "—";
    return;
  }
  const arrow = quote.change > 0 ? "▲ " : quote.change < 0 ? "▼ " : "";
  cell.replaceChildren(
    make("span", "pill-move", arrow + signed(quote.percent, 2) + "%"),
    make("span", "src", signed(quote.change, 2) + " today"),
  );
  cell.classList.add(direction(quote.change));
}

function drawCell(cell, quote) {
  const field = cell.dataset.field;
  if (!quote) {
    // No fresh price. The 52 week bar keeps what it last showed.
    if (field !== "year") cell.textContent = "—";
    return;
  }
  if (field === "price") drawPrice(cell, cell.dataset.quote, quote);
  else if (field === "change") drawChange(cell, quote);
  else if (field === "spark") drawSpark(cell, quote);
  else if (field === "range") drawRange(cell, quote.low, quote.high, quote.price, quote.open);
  else if (field === "year") drawRange(cell, Number(cell.dataset.low), Number(cell.dataset.high), quote.price, 0);
  else if (field === "distance") {
    cell.textContent = distanceText(quote.price, Number(cell.dataset.threshold), cell.dataset.condition);
  }
}

function draw(body) {
  let up = 0;
  let down = 0;
  for (const quote of Object.values(body.quotes)) {
    if (quote && quote.change > 0) up++;
    if (quote && quote.change < 0) down++;
  }
  for (const cell of document.querySelectorAll("[data-quote]")) {
    drawCell(cell, body.quotes[cell.dataset.quote]);
  }
  for (const [ticker, quote] of Object.entries(body.quotes)) {
    if (quote) last[ticker] = quote.price;
  }
  const total = Object.keys(body.quotes).length || 1;
  for (const el of document.querySelectorAll("[data-stat='up']")) el.textContent = up;
  for (const el of document.querySelectorAll("[data-stat='down']")) el.textContent = down;
  for (const el of document.querySelectorAll("[data-breadth='up']")) el.style.width = (up / total) * 100 + "%";
  for (const el of document.querySelectorAll("[data-breadth='down']")) el.style.width = (down / total) * 100 + "%";
  setMarket(body.market);
  // The stream can be up while the market is shut. Only call it Live when trades can flow.
  const closed = body.market && !body.market.open;
  setStatus(body.status === "live" && closed ? "connected" : body.status, body.server_time);
}

// Start asking for prices. While the watchlist service does not answer, the
// cells keep their last value and the pill at the top says "Connecting".
export function startQuotes() {
  keepFresh(async () => {
    try {
      draw(await call("watchlist", "/api/quotes", { retry: false }));
    } catch (error) {
      setStatus("connecting");
      throw error;
    }
  }, 1000);
}

// Company details and the 52 week range change slowly, so each row asks once.
// A row is marked with data-details="<ticker>". Details are optional: a row
// without them simply shows less.
export async function loadDetails() {
  for (const row of document.querySelectorAll("[data-details]")) {
    const ticker = row.dataset.details;
    let details;
    try {
      details = (await call("watchlist", "/api/details/" + encodeURIComponent(ticker), { retry: false })).details;
    } catch {
      continue;
    }
    if (!details) continue;
    const cap = details.market_cap ? "$" + compact.format(details.market_cap) + " cap" : "";
    row.querySelector("[data-meta]").textContent = [details.industry, cap].filter(Boolean).join(" · ");
    const year = row.querySelector("[data-field='year']");
    if (details.year_low && details.year_high) {
      year.dataset.low = details.year_low;
      year.dataset.high = details.year_high;
      if (last[ticker] !== undefined) drawRange(year, details.year_low, details.year_high, last[ticker], 0);
    }
  }
}
