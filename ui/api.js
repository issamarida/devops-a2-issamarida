// The one place the UI talks to the two services.
//
// - The addresses come from config.js, never from this file.
// - Every request has a timeout, so a page never hangs.
// - On Azure a service with no visitors is stopped, and the first request finds
//   it asleep (brief section 7.5). A request that is safe to repeat is tried
//   again for about a minute while the page says the service is starting.
// - The sign-in token is kept for this browser tab only and sent with each request.

// Tests change these two to run without real waiting.
export const settings = {
  timeoutMs: 20000,
  // The waits between tries: 55 seconds in all, plus the time each try takes.
  retryWaitsMs: [1000, 2000, 4000, 8000, 10000, 10000, 10000, 10000],
};

// The service answered, and the answer is an error such as 400, 404 or 409.
export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

// The service did not answer at all: asleep, stopped or no network.
export class Unreachable extends Error {
  constructor(service) {
    super(`The ${service} service is not answering. It may be starting up. Try again in a moment.`);
    this.service = service;
  }
}

export function getToken() {
  return sessionStorage.getItem("token") || "";
}

export function getUsername() {
  return sessionStorage.getItem("username") || "";
}

export function saveSignIn(token, username) {
  sessionStorage.setItem("token", token);
  sessionStorage.setItem("username", username);
}

// Forget the token and go to the sign-in page. reason is shown there.
export function signOut(reason = "") {
  sessionStorage.removeItem("token");
  sessionStorage.removeItem("username");
  location.assign("signin.html" + (reason ? "?" + reason : ""));
}

function baseUrl(service) {
  const url = window.APP_CONFIG[service + "Url"];
  return url.replace(/\/+$/, "");
}

// One try. Answers { reached: false } when the service itself did not answer.
async function tryOnce(service, path, method, body) {
  const headers = {};
  if (getToken()) headers.Authorization = "Bearer " + getToken();
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let response;
  try {
    response = await fetch(baseUrl(service) + path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
      signal: AbortSignal.timeout(settings.timeoutMs),
    });
  } catch {
    // Refused, timed out or blocked. Nothing came back.
    return { reached: false };
  }
  let data = null;
  try {
    data = await response.json();
  } catch {
    // No JSON body: a 204, or an error page that is not from our code.
  }
  // Our services always send {"error": "..."} with an error. A 502, 503 or 504
  // without it comes from Azure in front of a service that is still starting.
  const gateway = response.status >= 502 && response.status <= 504;
  if (gateway && !(data && data.error)) return { reached: false };
  return { reached: true, status: response.status, data };
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Send one request to "watchlist" or "alerts" and return the JSON answer.
//
// options.method   GET unless given.
// options.body     Sent as JSON.
// options.retry    Try again while the service does not answer. On by default
//                  for GET only, because repeating a GET changes nothing.
// options.onWaiting(service)  Called before each new try, to tell the user.
//
// Throws ApiError when the service answers with an error, and Unreachable when
// it never answers.
export async function call(service, path, options = {}) {
  const method = options.method || "GET";
  const retry = options.retry ?? method === "GET";
  const waits = retry ? settings.retryWaitsMs : [];
  for (let attempt = 0; ; attempt++) {
    const answer = await tryOnce(service, path, method, options.body);
    if (answer.reached) {
      if (answer.status === 401 && getToken()) {
        // The token ran out or is no longer accepted.
        signOut("expired");
      }
      if (answer.status >= 400) {
        const message = (answer.data && answer.data.error) || "Something went wrong. Try again.";
        throw new ApiError(message, answer.status);
      }
      return answer.data;
    }
    if (attempt >= waits.length) throw new Unreachable(service);
    if (options.onWaiting) options.onWaiting(service);
    await sleep(waits[attempt]);
  }
}
