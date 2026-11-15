"""Application configuration, read from the environment at import time.

Every value has a development default so the app and its tests run without a
provisioned environment. app.db.connection re-reads os.environ at CALL time, so tests
override DB_NAME (and friends) by setting environment variables before connecting.
"""

import os

APP_NAME = "AIERP"
APP_VERSION = "0.2.0"

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "aierp")
DB_USER = os.getenv("DB_USER", "aierp")
DB_PASSWORD = os.getenv("DB_PASSWORD", "aierp")

CORS_ORIGINS = [
    o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()
]
