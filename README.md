# Stock watchlist and price alerts

An app for retail investors who follow US stocks. Each investor keeps a private watchlist and sets price rules that fire once and leave a record. It monitors prices. It does not place trades or send email or push notifications.

Assignment 1 was one Flask process with one SQLite file. Assignment 2 splits it into two services along the seam from ADR-2 and ADR-6:

| Service | Folder | Owns | Default port |
|---|---|---|---|
| watchlist | `services/watchlist` | tracked assets, Finnhub prices, accounts and sign in | 8001 |
| alerts | `services/alerts` | price rules, trigger history, the background check | 8002 |

Each service has its own SQL Server database and its own login. The alerts service calls the watchlist service over HTTP. The watchlist service never calls alerts. The static UI in `ui/` calls both from the browser.

## Status

Assignment 2 is in progress. This table is kept honest against the brief.

| Brief section | State |
|---|---|
| 1a Two services, own folders, own manifests | Done |
| 1a HTTP between services through an environment variable | Done |
| 3 Cross-seam flow on the server side, timeout on every call | Done |
| 3 Partial failure handled in a designed way | Done in the API and in the UI |
| 3 `/health` with status, db and version, one log line per request | Done |
| 1a CORS only from the UI's origin, in code | Done. CI checks it on the running containers |
| 5 CI: lint, tests and coverage per service, against a SQL Server container | Done |
| 5 CI builds every image | Done. CI also starts the whole system and checks both `/health` answers |
| 5 CI validates `system.yaml` and each `openapi.yaml` | Done. CI also compiles and lints the Bicep file |
| 1a SQL Server, one database and one login per service, `DATABASE_URL` | Done. SQLite is gone from the code |
| 4 Versioned migrations, applied in order at startup and recorded | Done: `001_init.sql` in each service |
| 4 A migration that changes a table holding data, with evidence on Azure | Not yet. It comes after the first deployment, because the database must hold data first |
| 1a Own Dockerfiles: no `COPY . .`, no secrets in the image, pinned dependencies, non-root user | Done |
| 1a `docker compose up --build` from a clean clone | Done: database, both services and the UI |
| 1a Static UI in `ui/`, addresses from `config.js`, served by nginx locally | Done. The pipeline uploads it to the storage static website |
| 7.5 Cold starts: the services wait for a paused database, the UI handles a sleeping service | Done: retries with backoff at startup, a "starting up" notice and retries in the UI. The first deploy started both services against new databases. A start against a paused database is still to be recorded |
| 7 Initial Azure setup | Done on 26 November 2026 with `infra/setup.sh` |
| 5 and 7 CD: SHA-tagged images in GHCR, Bicep deploy, UI upload, health check | Done. First successful run on 26 November 2026: [run 45881877630](https://github.com/issamarida/devops-a2-issamarida/actions/runs/45881877630) |
| 8a `openapi.yaml` per service | Done, and the tests hold every answer to it |
| 8a `system.yaml` | Done. Validated against the schema in CI and tested against the code |

## Run it with one command

You need Docker with the compose plugin. Nothing else has to be installed, created or configured.

```sh
docker compose up --build
```

That builds both images, makes up the local secrets, starts SQL Server, creates the two databases and logins, and starts both services and the UI. Each service applies its migrations before it answers.

| Address | What is there |
|---|---|
| `http://localhost:8080` | the app. Open this one |
| `http://localhost:8001/health` | watchlist service |
| `http://localhost:8002/health` | alerts service |

- To see the commit in `/health`, build with it: `env GIT_SHA=(git rev-parse --short HEAD) docker compose up --build`.
- To get prices, put `PRICE_API_KEY=<your Finnhub key>` in a git-ignored `.env` file next to `docker-compose.yml`. Without it the system still runs and every price is reported as unavailable.
- To run a single service: `docker compose up --build watchlist`. It starts its database first and nothing else. `docker compose up --build alerts` works the same way, and alerts serves its own data without the watchlist.
- To see partial failure: `docker compose stop watchlist`, then open the price alerts page. "What a user sees when something is down" says what to expect.
- `docker compose down` stops everything and keeps the data. The next `up` finds it again and applies no migration twice.

## The UI

The UI is plain HTML, CSS and JavaScript in `ui/`. There is no framework and no build step. The browser loads the files and then calls both services itself.

| File | What it is |
|---|---|
| `index.html`, `dashboard.js` | The dashboard: the watchlist with live prices |
| `alerts.html`, `alerts.js` | Price alerts: rules, their status and the trigger history |
| `signin.html`, `signin.js` | Sign in and create an account |
| `config.js` | The addresses of the two services. The only file that names them |
| `api.js` | Sends every request: address from `config.js`, the token, a timeout, retries |
| `page.js` | What the signed-in pages share, and the loop that keeps a page up to date |
| `quotes.js` | Live prices, drawn into the tables |
| `styles.css` | The look, in light and dark |

- `config.js` in the repository holds the compose addresses. The deployment pipeline will write it again with the Azure addresses when it uploads the UI. A CI step fails if any other file in `ui/` names an address.
- The dashboard takes its assets and prices from the watchlist service and its two alert numbers from the alerts service. The alerts page takes rules and history from alerts, and the ticker list and prices from the watchlist. Creating a rule is the flow that crosses the seam on the server side.
- The sign-in token is kept in `sessionStorage`, so it lasts as long as the browser tab. A page without a token goes to sign in.
- Each page carries a Content-Security-Policy that only lets its own scripts and styles run. Text from a service is always written as text and never as HTML.
- A page asks again every second for prices and every five seconds for alerts. It stops when the tab is hidden and after five minutes without a key or pointer movement, so a forgotten tab does not keep the Azure containers and databases awake. A notice says the updates are paused, and any movement continues them.

### What a user sees when something is down

Every request has a 20 second timeout, so no page hangs.

| Situation | What the page does |
|---|---|
| A service is asleep or still starting (section 7.5) | A notice says it is starting up and can take up to a minute. A read is tried again for about a minute and the page fills in by itself |
| The sign-in page opens on a sleeping system | It asks `/health` of both services at once to wake them. A sign in sent meanwhile goes through as soon as the service is up |
| A service never answers | The notice becomes an error that names the service. Nothing is left blank without words |
| Watchlist stopped, price alerts page | Rules and history are listed. Open rules show `unknown` with a notice. The create form is replaced by a line saying why, and comes back by itself |
| Watchlist stopped, "Check rules now" | The alerts service's own message: the check did not finish and unchecked rules stay active |
| Alerts stopped, dashboard | The watchlist works as usual. The two alert numbers go blank with a notice |
| The token ran out | Back to sign in, with a line saying so |

## The images

Each service has its own `Dockerfile` in its own folder. Both follow the same rules:

- The base image is pinned by version and by digest.
- Only `requirements.txt`, `app.py`, `app/` and `migrations/` are copied, each by name. There is no `COPY . .`, and `.dockerignore` keeps everything else out of the build, a local `.env` included.
- Every dependency version is pinned in `requirements.txt`.
- The service runs as user 10001, which can read its files and write nothing.
- No secret is in the image. `DATABASE_URL`, `TOKEN_SECRET` and `PRICE_API_KEY` are given to the container when it starts.
- `GIT_SHA` is a build argument. `/health` reports it as `version`.

## Run a service without Docker

Python 3.14. One virtual environment can serve both services while working locally. The database still comes from compose.

```sh
uv venv
source .venv/bin/activate.fish
uv pip install -r services/watchlist/requirements.txt
uv pip install -r services/alerts/requirements.txt
```

Both services need the same `TOKEN_SECRET`, at least 32 characters. It is a secret, so it has no default and is never written in this repository. Make one up for your own machine:

```sh
set -x TOKEN_SECRET (python -c "import secrets; print(secrets.token_urlsafe(48))")
```

Start the database only, then give each service its own connection string. The password is read from the volume where compose keeps it, so it never appears on screen or in a file:

```sh
docker compose up -d db-init
set W (docker compose exec -T db cat /run/local-secrets/watchlist_password)
set A (docker compose exec -T db cat /run/local-secrets/alerts_password)
```

Then start each service from its own folder, in two terminals that share those variables:

```sh
cd services/watchlist; env DATABASE_URL="mssql+pymssql://watchlist_user:$W@localhost:1433/watchlist" python app.py
cd services/alerts; env DATABASE_URL="mssql+pymssql://alerts_user:$A@localhost:1433/alerts" WATCHLIST_URL=http://localhost:8001 python app.py
```

Each service applies its new migrations when it starts and logs what it did, for example `Applied migration 001_init.sql` or `No new migration to apply`.

To run a single service, start only that one. The watchlist service does not need alerts at all. The alerts service starts and serves its own data without the watchlist, as described under "When the watchlist is down".

Prices come from Finnhub. Set `PRICE_API_KEY` for the watchlist service to get them. Without a key the service still runs and every price is reported as unavailable. No price is ever made up.

## The local SQL Server

`docker-compose.yml` starts one SQL Server container that holds one database per service. Each service connects to its own database with its own login and never to the other one.

```sh
docker compose up -d db-init
sh db/check-logins.sh
```

| Compose service | What it does |
|---|---|
| `secrets` | Runs once. Makes up the three database passwords and the token secret on the first start and keeps them in the `local-secrets` volume |
| `db` | SQL Server 2022. Its data is in the `db-data` volume and survives `docker compose down` |
| `db-init` | Runs `db/init.sql` on every start. Creates the `watchlist` and `alerts` databases and one login for each, if they are missing |
| `watchlist`, `alerts` | The two services, each built from the `Dockerfile` in its own folder |
| `ui` | A stock nginx image that serves `ui/` as it is. No Dockerfile. It is not a service container |

- No secret is written in this repository, not even a local one. `db/make-secrets.sh` makes them up on your machine. Compose reads them from the volume into `DATABASE_URL` and `TOKEN_SECRET` when a service starts.
- `db/init.sql` checks before it creates, so running it again changes nothing.
- `watchlist_user` has a user in the `watchlist` database only, and `alerts_user` in `alerts` only. `db/check-logins.sh` proves each login opens its own database and is refused on the other. CI runs it on every push.
- `docker compose down -v` deletes both volumes, which deletes the data and the passwords. It is never needed to restart or rebuild.

## Migrations

The schema of each service is the list of numbered SQL files in `services/<name>/migrations`. There is no other place where a table is created.

- A service applies the files it has not run yet, in number order, every time it starts. A second start applies nothing.
- Each file runs in one transaction together with its row in the `schema_migrations` table. If a statement fails, SQL Server undoes the whole file, nothing is recorded and the service does not start.
- `schema_migrations` also keeps a checksum of each file. If an applied file is edited later, the service refuses to start and says which file. A correction is always a new file.
- If two copies of a service start at the same moment, a lock makes one wait. Each file still runs once.
- A line that holds only `GO` splits a file into batches, as in `sqlcmd`. A statement that uses a new column must come after a `GO`.
- To change the schema, add the next file, for example `002_add_something.sql`. Never edit or delete an old one, and never drop a table to rebuild it.

## API

Every request and answer is JSON. Errors are always `{"error": "..."}`. Apart from `/health` and the two `/auth` endpoints, a request needs the header `Authorization: Bearer <token>` with the token from sign in.

The full description of each API is its OpenAPI 3 document: `services/watchlist/openapi.yaml` and `services/alerts/openapi.yaml`. They list every endpoint with its parameters, request body, status codes and the shape of each answer. The tables below are the short version.

The documents are written by hand and kept true by the tests:

- The routes in the code and the paths in the document must be the same list, in both directions.
- `tests/openapi_check.py` hooks into the app that the API tests use and compares every answer with the document. A status code or a field that is not written down fails the test that saw it. The API tests see 41 of the 44 documented answers. The other three are `/health` answering 503, which its own test checks against the document, and a rate-limited `DELETE`, which shares its code with the rate-limited `POST`.
- The real price and market objects are checked against the document too, because the API tests use a fake price source.
- The alerts tests read the watchlist document and check that the stub they use for the watchlist answers in the shape that document promises. Both sides of the seam are held to one document.
- CI validates both files against the OpenAPI specification.

**watchlist**

| Method and path | What it does |
|---|---|
| `GET /health` | 200 with `status`, `db` and `version`, or 503 |
| `POST /auth/register` | Create an account |
| `POST /auth/login` | Answer with a signed token that lasts `TOKEN_SECONDS` |
| `GET /watchlist` | The account's assets |
| `POST /watchlist` | Add an asset: 201, 400 if invalid, 409 if already there |
| `DELETE /watchlist/<ticker>` | Remove an asset: 204 or 404 |
| `GET /api/quotes` | Live prices for the account's assets, and the market session |
| `GET /api/search?q=` | Ticker suggestions |
| `GET /api/details/<ticker>` | Company details for an asset on the watchlist |
| `GET /internal/snapshot?tickers=` | For the alerts service only: who watches what, and the asked prices |

**alerts**

| Method and path | What it does |
|---|---|
| `GET /health` | 200 with `status`, `db` and `version`, or 503 |
| `GET /alerts` | The account's rules with a status, and `watchlist_available` |
| `POST /alerts` | Add a rule: 201, 400 if invalid, 503 if the watchlist can't be reached |
| `DELETE /alerts/<id>` | Delete a rule: 204 or 404. Its history stays |
| `GET /alerts/events` | The latest 100 trigger events and the total count |
| `POST /alerts/evaluate` | Check the account's rules now: 200 with what fired, or 503 |

## The seam, and when the watchlist is down

The alerts service needs two facts it does not own: is this ticker on the account's watchlist, and what does it cost now. It gets both from one request to `GET /internal/snapshot` on the watchlist service.

- The address comes from `WATCHLIST_URL`. No hostname is written in the code.
- Every call has a timeout, `WATCHLIST_TIMEOUT_SECONDS`.
- Listing and creating rules ask who watches what. Only a check also asks for prices, and only for tickers that have an active rule.
- The background check makes one request per run for all accounts, and none when no rule is active.

When the watchlist service is stopped, too slow or answers nonsense:

| Request to alerts | Answer |
|---|---|
| `GET /alerts` | 200. Open rules get the status `unknown` and `watchlist_available` is `false` |
| `POST /alerts` | 503 with "The watchlist can't be reached right now, so the rule was not saved. Try again in a moment." |
| `POST /alerts/evaluate` | 503. No rule fires on a price nobody could confirm |
| history, delete, `/health` | Unchanged. They only use the alerts service's own data |

The background check skips that run and logs one warning. Every rule stays active and is checked again on the next run.

## Sign in and access

- The watchlist service holds the accounts. Passwords are salted and hashed with scrypt and must be 15 to 128 characters.
- Sign in answers with a JSON Web Token signed with `TOKEN_SECRET` (HS256). It holds the account ID, the username and an expiry time. There are no cookies and no server-side sessions.
- Both services check the token's signature themselves. The alerts service never calls the watchlist to learn who is asking, so it keeps working when the watchlist is stopped.
- A token cannot be withdrawn before it expires. That is the cost of keeping no session state, and why the default lifetime is one hour.
- Every query uses the account ID from the token. An account ID sent in a request body is ignored.
- `/internal/snapshot` only opens for a one-minute token with the `snapshot` scope, which the alerts service signs with the same secret.
- Sign-in attempts and registrations are rate limited in the watchlist database, so the limits survive a restart.
- Tickers and usernames are compared without case, because that is SQL Server's default. The services still store tickers in upper case and usernames in lower case.
- Each service allows browser requests from the addresses in `CORS_ORIGINS` only. The UI keeps the token in `sessionStorage` and sends it in the `Authorization` header, so there is no cookie for another site to ride on.

## Configuration

Every setting is an environment variable. Each service also reads an optional git-ignored `.env` file in its own folder. A variable set in the shell always wins.

**watchlist**

| Variable | Default | Meaning |
|---|---|---|
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `8001` | Port the service listens on |
| `DATABASE_URL` | none, required | Connection string of the watchlist database. A secret, because it holds the password. `mssql+pymssql://user:password@host:port/database` |
| `TOKEN_SECRET` | none, required | Secret that signs the tokens. The same value in both services |
| `PRICE_API_KEY` | none | Finnhub key. A secret. Without it prices are unavailable |
| `CORS_ORIGINS` | `http://localhost:8080` | The UI's address. A comma-separated list is allowed |
| `GIT_SHA` | `dev` | The commit the service was built from, shown by `/health` |
| `TOKEN_SECONDS` | `3600` | How long a sign-in token lasts |
| `PRICE_CACHE_TTL_SECONDS` | `30` | How long a REST quote is reused |
| `QUOTE_REQUESTS_PER_MINUTE` | `50` | Budget for calls to Finnhub. Its free plan allows 60 |
| `LIVE_SYMBOL_LIMIT` | `50` | Most tickers streamed at once |
| `AUTH_ATTEMPTS` | `5` | Sign-in attempts per username, and registrations per address, per window |
| `AUTH_WINDOW_SECONDS` | `900` | Length of that window |
| `MAX_ACCOUNTS` | `50` | Most accounts |
| `MAX_WATCHLIST_ITEMS` | `30` | Most assets per account |
| `TRUSTED_PROXIES` | `0` | Proxies in front of the service that add the caller to `X-Forwarded-For`. `1` on Azure, so the sign-in limits count each browser and not the Container Apps proxy |

**alerts**

| Variable | Default | Meaning |
|---|---|---|
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `8002` | Port the service listens on |
| `DATABASE_URL` | none, required | Connection string of the alerts database. A secret, because it holds the password |
| `TOKEN_SECRET` | none, required | The same secret as the watchlist service |
| `WATCHLIST_URL` | `http://watchlist:8001` | Base address of the watchlist service |
| `WATCHLIST_TIMEOUT_SECONDS` | `3` | Timeout for each call to the watchlist. At most 30 |
| `POLL_INTERVAL_SECONDS` | `15` | Wait between background checks. 0 turns them off |
| `CORS_ORIGINS` | `http://localhost:8080` | The UI's address |
| `GIT_SHA` | `dev` | The commit the service was built from, shown by `/health` |
| `MAX_ALERT_RULES` | `100` | Most rules per account, fired ones included |

A wrong value stops the service at start with a message that names the variable.

`system.yaml` at the root lists the same variables in the course's format. `tests/test_system_yaml.py` in each service records the variables `load_config()` really reads and fails when the file misses one, gives another default or gives a secret a default.

## Tests

Each service has its own tests and its own coverage gate. The tests that touch the database run against a real SQL Server, as the service's own login, so they also prove that login has the rights it needs.

They empty every table before each test, so they use a second copy of the compose database with its own containers, volumes and port. They can never delete the data in the database you work with. A test only reaches a database when `TEST_DATABASE_URL` is set.

```sh
sh db/test-db.sh up
cd services/watchlist; env TEST_DATABASE_URL=(sh ../../db/test-db.sh url watchlist) pytest --cov
cd services/alerts; env TEST_DATABASE_URL=(sh ../../db/test-db.sh url alerts) pytest --cov
ruff check services
sh db/test-db.sh down
```

Verified on 26 November 2026: watchlist 281 tests and alerts 256 tests, both at 100% statement coverage of their measured modules. Coverage below 70% fails the run. As in Assignment 1 the thin route files and `create_app` are left out of the measure and are exercised through the API tests instead.

No test touches the internet. Finnhub is mocked. The cross-seam tests in `services/alerts/tests` replace the watchlist service with `StubWatchlist`, a real HTTP server on localhost that checks the service token as the real one does. The tests stop it, slow it down and make it answer nonsense to prove the answers in the table above.

The UI has its own small tests in `ui-tests/`, 21 of them. They run on Node's built-in test runner, version 22.12 or newer, with no `package.json` and nothing to install:

```sh
node --test "ui-tests/*.test.mjs"
```

They replace `fetch` with a fake and cover `ui/api.js`: the address comes from `config.js`, every request has a timeout, a read is retried while a service starts, a write is sent once, a 503 the service wrote itself is shown and not retried, and a refused token returns to sign in. The pages themselves have no automated browser test in the repository. On 24 November 2026 they were driven once in a headless browser against both running services, including each row of the table under "What a user sees when something is down".

CI in `.github/workflows/ci.yml` runs the linter and both test suites on every push and pull request, against a SQL Server container it starts with `db/test-db.sh`. A second job validates `system.yaml` against `system.schema.json` and each `openapi.yaml` against the OpenAPI specification, and compiles `infra/main.bicep` with every linter warning counted as a failure. A third job runs the UI tests and fails if a file in `ui/` other than `config.js` names a service address. The last job runs `docker compose up --build`, so a failed image build fails the pipeline. It then runs `db/init.sql` a second time, runs `db/check-logins.sh`, checks that both `/health` answers carry the commit, that the UI is served and both addresses in its `config.js` answer, that each service sends the CORS header to the UI's address and to no other, and that neither container runs as root. CI never connects to Azure.

On `main` the CD workflow runs this whole file first, so its push trigger skips `main` and a merge runs CI once.

## Deployment on Azure

The live app is **https://iaiaridaieu2024ui.z1.web.core.windows.net**.

Everything runs in the Azure for Students subscription, in `swedencentral`, in the resource group `rg-iaiarida-ieu2024`:

| Resource | Name | What it is for |
|---|---|---|
| Storage account | `iaiaridaieu2024ui` | Static website serving `ui/`. Its address is the public URL and the only origin the services accept |
| Container Apps environment | `iaiarida-ieu2024-env` | Consumption plan, no Log Analytics workspace |
| Container App | `iaiarida-ieu2024-watchlist` | The watchlist service, 0 to 1 replicas, public HTTPS |
| Container App | `iaiarida-ieu2024-alerts` | The alerts service, 0 to 1 replicas, public HTTPS |
| SQL server | `iaiarida-ieu2024-sql` | One logical server for both databases |
| SQL database | `iaiarida-ieu2024-watchlist-db` | Serverless, General Purpose Gen5, 0.5 to 1 vCore, free offer, local backups |
| SQL database | `iaiarida-ieu2024-alerts-db` | The same, for alerts |
| Managed identity | `iaiarida-ieu2024-github` | The pipeline signs in as it. Contributor on this resource group only |

Images are in GHCR: `ghcr.io/issamarida/watchlist` and `ghcr.io/issamarida/alerts`, one tag per commit.

### How a deploy works

Nothing is changed on Azure by hand after the initial setup. A pull request merged into `main` starts `.github/workflows/cd.yml`:

1. It runs `ci.yml` on the merged commit. Nothing below runs unless it passes.
2. It builds both images with `GIT_SHA` set to the commit and pushes them to GHCR tagged with the full SHA.
3. It signs in to Azure as `iaiarida-ieu2024-github` through OpenID Connect. GitHub signs a token that says "a run on `main` of this repository", and Azure trusts only that. No Azure password or key is stored.
4. It deploys `infra/main.bicep`: the environment and both Container Apps, pointed at the new images. The database passwords, the token secret and the Finnhub key come from GitHub secrets and become Container Apps secrets.
5. It writes `ui/config.js` with the two addresses Bicep returns and uploads `ui/` to the `$web` container.
6. It calls `/health` on both services for up to eight minutes and fails unless each answers 200 with this commit as its version. It also checks that the live UI is served and that each service sends the CORS header to the UI's address and to no other.

### The initial setup

`infra/setup.sh` is the setup as it was run on 26 November 2026. It follows the course's Azure guide and needs `az`, `gh` and Docker. Sign in first with `az login` and `gh auth login`, then run `bash infra/setup.sh` from the repository root. No password is written to a file or printed: each one is made up in the script, handed to Azure and to `gh secret set`, and forgotten when it ends.

1. **Subscription and region.** `az account set --subscription "Azure for Students"`. The script prints the regions the policy allows. This subscription allows `swedencentral`, `spaincentral`, `norwayeast`, `polandcentral` and `switzerlandnorth`. Everything is in `swedencentral`, because the SQL free offer is not available in `spaincentral`.
2. **Providers and resource group.** `az provider register --wait` for `Microsoft.App`, `Microsoft.OperationalInsights`, `Microsoft.Sql`, `Microsoft.Storage` and `Microsoft.ManagedIdentity`, then `az group create --name rg-iaiarida-ieu2024 --location swedencentral`.
3. **Storage static website.** `az storage account create` (StorageV2, Standard_LRS, TLS 1.2, no public blob access), then `az storage blob service-properties update --static-website --index-document index.html`.
4. **SQL.** `az sql server create` with a one-off admin password, the firewall rule `AllowAzureServices` (0.0.0.0), and for each service `az sql db create --edition GeneralPurpose --compute-model Serverless --family Gen5 --min-capacity 0.5 --capacity 1 --backup-storage-redundancy Local --use-free-limit true --free-limit-exhaustion-behavior AutoPause`. The script then opens the firewall to this machine, runs `db/azure-users.sql` in each database with `sqlcmd` from the SQL Server image to make `watchlist_user` and `alerts_user` (contained users with `db_ddladmin`, `db_datareader` and `db_datawriter`), stores their passwords with `gh secret set` and closes the firewall again.
5. **Container Apps.** Not in the script. The first run of the pipeline creates the environment and both apps from `infra/main.bicep`.
6. **The pipeline's identity.** `az identity create --name iaiarida-ieu2024-github`, Contributor on the resource group, Storage Blob Data Contributor on the storage account (so it can upload `ui/` without a key), and two federated credentials with issuer `https://token.actions.githubusercontent.com` and audience `api://AzureADTokenExchange`: one for `repo:issamarida/devops-a2-issamarida:ref:refs/heads/main` and one for the same repository written with its numeric IDs.
7. **GitHub secrets and variables.** Secrets `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `WATCHLIST_DB_PASSWORD`, `ALERTS_DB_PASSWORD`, `TOKEN_SECRET` and `PRICE_API_KEY` (read from the git-ignored `.env`). Variables `PREFIX`, `RESOURCE_GROUP` and `UI_STORAGE`.
8. **Reader for the professors.** `az role assignment create --role Reader` on the resource group for `amartini@faculty.ie.edu` and `bserra@faculty.ie.edu`.

The SQL admin password is not kept anywhere. If it is ever needed, `az sql server update --admin-password` sets a new one.

The course guide warns that new GHCR packages are private, so the first deploy fails until each is made public. Here it did not happen: the images carry the `org.opencontainers.image.source` label, which links each package to this public repository, and both packages could be pulled without signing in from the first push.

### Checked on the live system on 26 November 2026

After the first deploy, against the live addresses:

- Both `/health` answers were 200 with `"db": "ok"` and the merged commit as the version.
- The browser preflight for a `POST` from the UI's address got `Access-Control-Allow-Origin` for that address and both headers the UI sends.
- A test account registered, signed in, added AAPL and got a live Finnhub quote. A rule on AAPL was created, which made the alerts service ask the watchlist service across the seam.
- Five registrations from one machine, each with a different made-up `X-Forwarded-For`, were followed by 429. The proxy handling counts the real address.
- The resource group holds nine resources: the storage account, the SQL server with its `master` and the two databases, the identity, the environment and the two apps. There is no Log Analytics workspace.

### Reading the logs on Azure

```sh
az containerapp logs show --resource-group rg-iaiarida-ieu2024 --name iaiarida-ieu2024-alerts --follow
az containerapp logs show --resource-group rg-iaiarida-ieu2024 --name iaiarida-ieu2024-alerts --type system
```

The first shows the service's own lines, one per request. The second shows starts, stops and probe failures.

## Rules of the domain

- Tickers are stored in upper case. A ticker can be on an account's watchlist once.
- A rule fires when the price is strictly above or below its threshold. A price equal to the threshold does not fire it.
- A rule fires once. Firing deactivates it and writes the history row in the same transaction.
- Removing a ticker from the watchlist leaves its rules dormant. Adding it back resumes them.
- Deleting a rule keeps its history.
- A ticker with no fresh price is skipped and its rules stay active.

## Documents

- `ADR.md`: the decisions, including ADR-6 on the split.
- `AI_USAGE.md`: every AI session and what it produced.
- `docs/`: the Assignment 1 report and diagrams. The Assignment 2 report will replace them.
- `system.yaml`: the whole system in the course's format: services, variables, the call across the seam and the Azure resources.
- `system.schema.json`: the course's schema for `system.yaml`, unchanged.
- `infra/`: `setup.sh`, the one-time Azure setup, and `main.bicep`, what every deploy changes.
