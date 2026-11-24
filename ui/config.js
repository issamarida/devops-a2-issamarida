// Where the two services are. This is the only file in ui/ that names an address.
//
// These values are for docker compose on your own machine. The deployment
// pipeline writes a new config.js with the Azure addresses when it uploads the UI,
// so no page and no other script has to change between the two.
window.APP_CONFIG = {
  watchlistUrl: "http://localhost:8001",
  alertsUrl: "http://localhost:8002",
};
