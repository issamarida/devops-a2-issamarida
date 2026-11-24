// Tests for ui/api.js, the script every page uses to reach the two services.
//
// Run with:  node --test "ui-tests/*.test.mjs"
//
// They need Node 22.12 or newer and nothing else: no package.json and no
// install. No test touches the network. fetch is replaced by a fake that
// answers from a list, so each test decides what "the service" says.

import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

// What a browser gives the script: the config, the tab's storage, the address bar.
const stored = new Map();
globalThis.window = {
  APP_CONFIG: { watchlistUrl: "http://watchlist.test/", alertsUrl: "http://alerts.test" },
};
globalThis.sessionStorage = {
  getItem: (key) => (stored.has(key) ? stored.get(key) : null),
  setItem: (key, value) => stored.set(key, String(value)),
  removeItem: (key) => stored.delete(key),
};
const visited = [];
globalThis.location = { assign: (url) => visited.push(url) };

const { ApiError, Unreachable, call, saveSignIn, settings } = await import("../ui/api.js");

// A JSON answer from a service.
const json = (status, body) => new Response(JSON.stringify(body), { status });
// What Azure answers for a container that is still starting: a 503 that is not ours.
const azureStarting = () => new Response("upstream connect error", { status: 503 });
const refused = () => {
  throw new TypeError("fetch failed");
};

let requests;

// Make fetch answer with these, one per request, and record what was asked.
function serviceAnswers(...answers) {
  requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url, ...options });
    const next = answers.shift();
    assert.ok(next, "the script asked more often than the test expected");
    return next();
  };
}

beforeEach(() => {
  stored.clear();
  visited.length = 0;
  settings.retryWaitsMs = [0, 0, 0];
});

test("a GET goes to the address from config.js and returns the JSON", async () => {
  serviceAnswers(() => json(200, { items: [] }));

  assert.deepEqual(await call("watchlist", "/watchlist"), { items: [] });

  assert.equal(requests[0].url, "http://watchlist.test/watchlist");
  assert.equal(requests[0].method, "GET");
});

test("each service has its own address", async () => {
  serviceAnswers(() => json(200, { rules: [] }));

  await call("alerts", "/alerts");

  assert.equal(requests[0].url, "http://alerts.test/alerts");
});

test("the sign-in token is sent once there is one", async () => {
  serviceAnswers(() => json(200, {}), () => json(200, {}));

  await call("watchlist", "/watchlist");
  saveSignIn("the-token", "alex");
  await call("watchlist", "/watchlist");

  assert.equal(requests[0].headers.Authorization, undefined);
  assert.equal(requests[1].headers.Authorization, "Bearer the-token");
});

test("a body is sent as JSON", async () => {
  serviceAnswers(() => json(201, { id: 7 }));

  await call("alerts", "/alerts", { method: "POST", body: { ticker: "AAPL" } });

  assert.equal(requests[0].body, '{"ticker":"AAPL"}');
  assert.equal(requests[0].headers["Content-Type"], "application/json");
});

test("every request carries a timeout", async () => {
  serviceAnswers(() => json(200, {}));

  await call("watchlist", "/watchlist");

  assert.ok(requests[0].signal instanceof AbortSignal);
});

test("a 204 with no body returns null", async () => {
  serviceAnswers(() => new Response(null, { status: 204 }));

  assert.equal(await call("alerts", "/alerts/3", { method: "DELETE" }), null);
});

test("an error from the service is thrown with the service's own words", async () => {
  serviceAnswers(() => json(409, { error: "AAPL is already on the watchlist." }));

  await assert.rejects(call("watchlist", "/watchlist", { method: "POST", body: {} }), (error) => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.status, 409);
    assert.equal(error.message, "AAPL is already on the watchlist.");
    return true;
  });
});

// --- A service that is asleep (brief section 7.5) ---------------------------

test("a GET that finds the service starting is tried again until it answers", async () => {
  const waited = [];
  serviceAnswers(azureStarting, refused, () => json(200, { items: ["AAPL"] }));

  const body = await call("watchlist", "/watchlist", { onWaiting: (service) => waited.push(service) });

  assert.deepEqual(body, { items: ["AAPL"] });
  assert.equal(requests.length, 3);
  // The page was told before each new try, so it can say "starting up".
  assert.deepEqual(waited, ["watchlist", "watchlist"]);
});

test("a service that never answers ends in Unreachable, not in a hung page", async () => {
  serviceAnswers(refused, refused, refused, refused);

  await assert.rejects(call("alerts", "/alerts"), (error) => {
    assert.ok(error instanceof Unreachable);
    assert.equal(error.service, "alerts");
    return true;
  });
  // The first try and one more for each wait in settings.retryWaitsMs.
  assert.equal(requests.length, 4);
});

test("a request that timed out counts as no answer", async () => {
  serviceAnswers(() => {
    throw new DOMException("The operation timed out.", "TimeoutError");
  });

  await assert.rejects(call("watchlist", "/api/quotes", { retry: false }), Unreachable);
});

test("a POST is sent once only, because sending it twice could save twice", async () => {
  serviceAnswers(azureStarting);

  await assert.rejects(call("watchlist", "/watchlist", { method: "POST", body: {} }), Unreachable);
  assert.equal(requests.length, 1);
});

test("a POST is tried again when the caller says it is safe", async () => {
  serviceAnswers(azureStarting, () => json(200, { token: "t" }));

  const body = await call("watchlist", "/auth/login", { method: "POST", body: {}, retry: true });

  assert.deepEqual(body, { token: "t" });
});

test("a GET with retry off is asked once, for the loops that ask again anyway", async () => {
  serviceAnswers(refused);

  await assert.rejects(call("watchlist", "/api/quotes", { retry: false }), Unreachable);
  assert.equal(requests.length, 1);
});

// --- Partial failure (brief section 3) --------------------------------------

test("a 503 that the service wrote itself is shown, not retried", async () => {
  // The alerts service answers this when the watchlist service is down.
  const message = "The watchlist can't be reached right now, so the rule was not saved. Try again in a moment.";
  serviceAnswers(() => json(503, { error: message }));

  await assert.rejects(call("alerts", "/alerts"), (error) => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.status, 503);
    assert.equal(error.message, message);
    return true;
  });
  assert.equal(requests.length, 1);
});

test("a 503 from /health means the service is not ready yet", async () => {
  // /health answers 503 with a status and no "error" while its database is away.
  serviceAnswers(
    () => json(503, { status: "error", db: "error", version: "abc" }),
    () => json(200, { status: "ok", db: "ok", version: "abc" }),
  );

  assert.equal((await call("watchlist", "/health")).status, "ok");
  assert.equal(requests.length, 2);
});

// --- Sign in -----------------------------------------------------------------

test("a 401 with a token forgets the token and goes to the sign-in page", async () => {
  saveSignIn("old-token", "alex");
  serviceAnswers(() => json(401, { error: "Sign in to continue." }));

  await assert.rejects(call("alerts", "/alerts"), ApiError);

  assert.equal(sessionStorage.getItem("token"), null);
  assert.deepEqual(visited, ["signin.html?expired"]);
});

test("a 401 without a token is a wrong password and stays on the page", async () => {
  serviceAnswers(() => json(401, { error: "Incorrect username or password." }));

  await assert.rejects(call("watchlist", "/auth/login", { method: "POST", body: {} }), {
    message: "Incorrect username or password.",
  });

  assert.deepEqual(visited, []);
});
