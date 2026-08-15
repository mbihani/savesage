"""Workstream-3 settings, isolated from the frozen shared configuration."""

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LakebaseSettings:
    profile: str = "fevm-stable"
    project_id: str = "savesage-statement-agent"
    database: str = "databricks_postgres"
    catalog: str = "stable_classic_7ppxjq_catalog"
    schema: str = "savesage"
    endpoint_id: str = "primary"


def get_lakebase_settings() -> LakebaseSettings:
    defaults = LakebaseSettings()
    return LakebaseSettings(
        profile=os.getenv("WS3_DATABRICKS_PROFILE", defaults.profile),  # CONFIGURE(ws3-profile)
        project_id=os.getenv("WS3_LAKEBASE_PROJECT", defaults.project_id),  # CONFIGURE(ws3-project)
        database=os.getenv("WS3_LAKEBASE_DATABASE", defaults.database),  # CONFIGURE(ws3-database)
        catalog=os.getenv("WS3_UC_CATALOG", defaults.catalog),  # CONFIGURE(ws3-catalog)
        schema=os.getenv("WS3_UC_SCHEMA", defaults.schema),  # CONFIGURE(ws3-schema)
        endpoint_id=os.getenv("WS3_LAKEBASE_ENDPOINT", defaults.endpoint_id),  # CONFIGURE(ws3-endpoint)
    )
