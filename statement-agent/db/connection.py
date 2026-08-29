"""Direct RDS Postgres connection factory (replaces Lakebase OAuth)."""

import os
from collections.abc import Callable
from typing import Any


class RDSConnectionFactory:
    """Connection factory for a direct AWS RDS Postgres connection.

    Uses plain username/password credentials — no ``WorkspaceClient``, no
    endpoint API call, no per-connection token generation.  The previous
    factory minted a fresh OAuth token per connection via the Lakebase
    autoscaling-Postgres API; RDS needs only a static password.
    """

    def __init__(self, host: str, database: str, user: str, password: str,
                 port: int = 5432, sslmode: str = "require") -> None:
        if not host or not host.strip():
            raise RuntimeError("RDS host must not be null or empty")
        self._host = host
        self._database = database
        self._user = user
        self._password = password
        self._port = port
        self._sslmode = sslmode

    def __call__(self) -> Any:
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError(
                "psycopg is required at runtime for RDS persistence"
            ) from exc
        return psycopg.connect(
            host=self._host, port=self._port, dbname=self._database,
            user=self._user, password=self._password, sslmode=self._sslmode,
        )

    @classmethod
    def from_env(cls) -> "RDSConnectionFactory":
        """Build a factory from ``RDS_*`` environment variables.

        Raises :class:`RuntimeError` with a clear message listing the missing
        variables if any required value (``RDS_HOST``, ``RDS_DATABASE``,
        ``RDS_USER``, ``RDS_PASSWORD``) is absent.  Only ``RDS_PORT`` and
        ``RDS_SSLMODE`` have defaults.
        """
        host = os.environ.get("RDS_HOST", "").strip()
        database = os.environ.get("RDS_DATABASE", "").strip()
        user = os.environ.get("RDS_USER", "").strip()
        password = os.environ.get("RDS_PASSWORD", "")
        missing = [
            name for name, val in (
                ("RDS_HOST", host),
                ("RDS_DATABASE", database),
                ("RDS_USER", user),
                ("RDS_PASSWORD", password),
            )
            if not val
        ]
        if missing:
            raise RuntimeError(
                "RDS connection requires these environment variables: "
                + ", ".join(missing)
            )
        return cls(
            host=host,
            port=int(os.environ.get("RDS_PORT", "5432")),
            database=database,
            user=user,
            password=password,
            sslmode=os.environ.get("RDS_SSLMODE", "require"),
        )


ConnectionFactory = Callable[[], Any]
