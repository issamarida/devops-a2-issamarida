// The sign-in page. One form that either signs in or creates an account.
//
// Accounts live in the watchlist service. It answers a sign in with a token,
// which api.js keeps for this tab and sends with every later request to both
// services.

import { ApiError, call, getToken, saveSignIn } from "./api.js";

const form = document.querySelector("[data-form]");
const errorBox = document.querySelector("[data-error]");
const starting = document.querySelector("[data-starting]");
const button = form.querySelector("button");

let registering = false;

function showError(text) {
  errorBox.textContent = text;
  errorBox.hidden = !text;
}

// Switch the one form between "Sign in" and "Create account".
function setMode(register) {
  registering = register;
  document.title = (register ? "Create account" : "Sign in") + " · Market workspace";
  document.querySelector("[data-title]").textContent = register ? "Create your account" : "Welcome back";
  document.querySelector("[data-intro]").textContent = register
    ? "Choose a username and a long, unique passphrase."
    : "Sign in to your watchlist and price alerts.";
  document.querySelector("[data-submit-label]").textContent = register ? "Create account" : "Sign in";
  document.querySelector("[data-password-hint]").hidden = !register;
  document.querySelector("[data-switch-text]").textContent = register ? "Already have an account?" : "New here?";
  document.querySelector("[data-switch]").textContent = register ? "Sign in" : "Create an account";
  const password = form.elements.password;
  password.autocomplete = register ? "new-password" : "current-password";
  if (register) password.minLength = 15;
  else password.removeAttribute("minlength");
  showError("");
}

async function submit(event) {
  event.preventDefault();
  showError("");
  button.disabled = true;
  const body = { username: form.elements.username.value, password: form.elements.password.value };
  try {
    // Creating an account is sent once only. Sending it twice could report a
    // name as taken that this very request had just created.
    if (registering) {
      await call("watchlist", "/auth/register", { method: "POST", body });
      // The account exists now. If the sign in below fails, pressing the
      // button again must sign in, not try to create the account twice.
      setMode(false);
    }
    // Signing in changes nothing, so it is safe to try again while the
    // service wakes up.
    const answer = await call("watchlist", "/auth/login", {
      method: "POST",
      body,
      retry: true,
      onWaiting: () => {
        starting.hidden = false;
      },
    });
    saveSignIn(answer.token, answer.username);
    location.assign("index.html");
  } catch (error) {
    starting.hidden = true;
    showError(
      error instanceof ApiError
        ? error.message
        : "The service that signs you in is not answering. It may be starting up. Try again in a moment.",
    );
    button.disabled = false;
  }
}

// On Azure the services are stopped while nobody uses them. Asking for /health
// as soon as the page opens wakes both, so they are ready by the time the
// form is filled in. The answer itself is not used.
async function wakeServices() {
  // The dashboard needs alerts next, so wake it too. Nothing here waits for it.
  call("alerts", "/health").catch(() => {});
  // Sign in needs the watchlist service, so the notice follows that one.
  try {
    await call("watchlist", "/health", {
      onWaiting: () => {
        starting.hidden = false;
      },
    });
  } catch {
    showError("The service that signs you in is not answering. Reload the page to try again.");
  }
  starting.hidden = true;
}

function start() {
  if (getToken()) {
    location.replace("index.html");
    return;
  }
  if (new URLSearchParams(location.search).has("expired")) {
    showError("Your sign in ran out. Sign in again to continue.");
  }
  form.addEventListener("submit", submit);
  // The button starts disabled in the HTML, so a click before this script has
  // loaded cannot send the form the browser's own way, which the page's
  // security policy blocks. It works from here on.
  form.querySelector("button[type=submit]").disabled = false;
  document.querySelector("[data-switch]").addEventListener("click", () => setMode(!registering));
  wakeServices();
}

start();
