"""Workstream-3 settings, isolated from the frozen shared configuration."""

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RDSSettings:
    host: str = ""
    port: int = 5432
    database: str = "postgres"
    user: str = ""
    password: str = ""
    sslmode: str = "require"


def get_rds_settings() -> RDSSettings:
    defaults = RDSSettings()
    return RDSSettings(
        host=os.getenv("RDS_HOST", defaults.host),  # CONFIGURE(rds-host)
        port=int(os.getenv("RDS_PORT", str(defaults.port))),  # CONFIGURE(rds-port)
        database=os.getenv("RDS_DATABASE", defaults.database),  # CONFIGURE(rds-database)
        user=os.getenv("RDS_USER", defaults.user),  # CONFIGURE(rds-user)
        password=os.getenv("RDS_PASSWORD", defaults.password),  # CONFIGURE(rds-password)
        sslmode=os.getenv("RDS_SSLMODE", defaults.sslmode),  # CONFIGURE(rds-sslmode)
    )
