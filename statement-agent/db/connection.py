"""Fresh-token Lakebase connection factory."""

from collections.abc import Callable
from typing import Any


class OAuthConnectionFactory:
    def __init__(self, workspace_client: Any, endpoint_path: str, host: str,
                 database: str, user: str, port: int = 5432,
                 sslmode: str = "require") -> None:
        self._client, self._endpoint_path = workspace_client, endpoint_path
        self._host, self._database, self._user = host, database, user
        self._port, self._sslmode = port, sslmode

    def __call__(self) -> Any:
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("psycopg is required at runtime for Lakebase persistence") from exc
        credential = self._client.postgres.generate_database_credential(self._endpoint_path)
        return psycopg.connect(host=self._host, port=self._port, dbname=self._database,
            user=self._user, password=credential.token, sslmode=self._sslmode)


ConnectionFactory = Callable[[], Any]
