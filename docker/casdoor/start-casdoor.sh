#!/bin/bash
set -euo pipefail

POSTGRES_HOST="${CASDOOR_POSTGRES_HOST:-postgres}"
POSTGRES_PORT="${CASDOOR_POSTGRES_PORT:-5432}"
POSTGRES_USER="${CASDOOR_POSTGRES_USER:-${POSTGRES_USER:-locus}}"
POSTGRES_PASSWORD="${CASDOOR_POSTGRES_PASSWORD:-${POSTGRES_PASSWORD:-}}"
POSTGRES_DB="${CASDOOR_POSTGRES_DB:-${POSTGRES_DB:-locus}}"
CASDOOR_PUBLIC_URL="${CASDOOR_PUBLIC_URL:-http://casdoor.localhost}"
CASDOOR_RADIUS_SECRET="${CASDOOR_RADIUS_SECRET:-${POSTGRES_PASSWORD}}"

if [ -z "${POSTGRES_PASSWORD}" ]; then
  echo "CASDOOR_POSTGRES_PASSWORD or POSTGRES_PASSWORD is required" >&2
  exit 1
fi

cat > /conf/app.conf <<EOF
appname = casdoor
httpport = 8000
runmode = dev
copyrequestbody = true
driverName = postgres
dataSourceName = user=${POSTGRES_USER} password=${POSTGRES_PASSWORD} host=${POSTGRES_HOST} port=${POSTGRES_PORT} dbname=${POSTGRES_DB} sslmode=disable
dbName = ${POSTGRES_DB}
tableNamePrefix =
showSql = false
redisEndpoint =
defaultStorageProvider =
isCloudIntranet = false
authState = "casdoor"
socks5Proxy = "127.0.0.1:10808"
verificationCodeTimeout = 10
initScore = 0
logPostOnly = true
isUsernameLowered = false
origin = ${CASDOOR_PUBLIC_URL}
originFrontend = ${CASDOOR_PUBLIC_URL}
staticBaseUrl = "https://cdn.casbin.org"
isDemoMode = false
batchSize = 100
enableErrorMask = false
enableGzip = true
inactiveTimeoutMinutes =
ldapServerPort = 389
radiusServerPort = 1812
radiusSecret = "${CASDOOR_RADIUS_SECRET}"
quota = {"organization": -1, "user": -1, "application": -1, "provider": -1}
logConfig = {"filename": "logs/casdoor.log", "maxdays":99999, "perm":"0770"}
initDataNewOnly = false
initDataFile = "./init_data.json"
frontendBaseDir = "../cc_0"
EOF

exec /server --createDatabase=true