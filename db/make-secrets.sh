#!/bin/sh
# Makes up the secrets for the local system and keeps them in a Docker volume.
#
# Compose runs this once before SQL Server starts. A secret that already exists
# is kept, so the databases stay reachable and sign-in tokens stay valid across
# restarts. Nothing here is used on Azure, where the secrets come from GitHub
# Actions secrets.
#
# The three passwords are for SQL Server. token_secret signs the sign-in tokens
# and is the same for both services.
set -eu

cd /run/local-secrets

# Prints 40 random letters and digits. SQL Server refuses a password without
# an upper case letter, a lower case letter and a digit, so try until all three
# are there. Only letters and digits, so the value is safe inside a URL.
new_secret() {
    while true; do
        value=$(head -c 48 /dev/urandom | base64 | tr -d '+/=\n' | cut -c 1-40)
        case "$value" in *[A-Z]*) ;; *) continue ;; esac
        case "$value" in *[a-z]*) ;; *) continue ;; esac
        case "$value" in *[0-9]*) ;; *) continue ;; esac
        printf '%s' "$value"
        return
    done
}

for name in sa_password watchlist_password alerts_password token_secret; do
    if [ ! -s "$name" ]; then
        new_secret > "$name"
        # Read only. SQL Server and the services run as other users and must read it.
        chmod 444 "$name"
        echo "Made up $name"
    fi
done
