-- Creates the service's user in its Azure SQL database. Run once per database
-- by infra/setup.sh, which connects to that database as the server admin.
--
-- Azure SQL has no server logins here: each user is "contained", so it exists
-- in one database only and the other database refuses it, as locally.
-- The password is not written here. sqlcmd fills in $(SERVICE_PASSWORD) from an
-- environment variable that setup.sh made up and also stored as a GitHub secret.
-- Running it again sets the same password again and changes nothing else.

IF USER_ID('$(SERVICE_USER)') IS NULL
    CREATE USER [$(SERVICE_USER)] WITH PASSWORD = '$(SERVICE_PASSWORD)';
ELSE
    ALTER USER [$(SERVICE_USER)] WITH PASSWORD = '$(SERVICE_PASSWORD)';

-- The same three roles as locally (db/init.sql): change the schema, so the
-- migrations can run at startup, read rows and write rows.
ALTER ROLE db_ddladmin ADD MEMBER [$(SERVICE_USER)];
ALTER ROLE db_datareader ADD MEMBER [$(SERVICE_USER)];
ALTER ROLE db_datawriter ADD MEMBER [$(SERVICE_USER)];
