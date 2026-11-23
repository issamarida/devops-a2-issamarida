#!/bin/sh
# A second copy of the compose database, used only by the tests.
#
# The tests empty every table before each test. This copy has its own
# containers, its own volumes and its own port, so running the tests can never
# delete the data in the database you work with.
#
#     sh db/test-db.sh up                 start it and create the databases and logins
#     sh db/test-db.sh url watchlist      print the connection string for one service
#     sh db/test-db.sh down               delete it, with its volumes
set -eu

cd "$(dirname "$0")/.."
export DB_PORT="${TEST_DB_PORT:-14330}"
# -p gives this copy its own name, so it shares nothing with `docker compose up`.
compose="docker compose -p stocks-test"

case "${1:-}" in
    up)
        $compose up -d --wait db
        $compose run --rm db-init
        ;;
    url)
        service="${2:?say which service: watchlist or alerts}"
        password=$($compose exec -T db cat "/run/local-secrets/${service}_password")
        echo "mssql+pymssql://${service}_user:${password}@localhost:${DB_PORT}/${service}"
        ;;
    down)
        $compose down -v
        ;;
    *)
        echo "usage: sh db/test-db.sh up | url <service> | down" >&2
        exit 2
        ;;
esac
