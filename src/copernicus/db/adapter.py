from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

import psycopg2

from ..utils.env import truthy_env


@dataclass(frozen=True)
class DbConfig:
    dbname: str
    user: str
    password: str
    host: str
    port: str = "5432"

    @staticmethod
    def from_env(prefix: str = "") -> "DbConfig":
        # Reuse existing DB_* naming by default (matches core.config)
        p = prefix
        return DbConfig(
            dbname=os.environ.get(f"{p}DB_NAME", ""),
            user=os.environ.get(f"{p}DB_USER", ""),
            password=os.environ.get(f"{p}DB_PASS", ""),
            host=os.environ.get(f"{p}DB_HOST", ""),
            port=os.environ.get(f"{p}DB_PORT", "5432"),
        )


class SafeDb:
    """
    psycopg2 wrapper with TEST_MODE safety.

    - When TEST_MODE is truthy: no writes. SQL is printed, not executed.
    - When TEST_MODE is falsy: executes normally using a single connection.
    """

    def __init__(self, config: DbConfig, *, test_mode: Optional[bool] = None):
        self.config = config
        # Safe default: TEST_MODE is enabled unless explicitly disabled.
        self.test_mode = truthy_env("TEST_MODE", True) if test_mode is None else bool(test_mode)
        self._conn = None

    def connect(self):
        if self._conn is not None:
            return self._conn
        if not (self.config.dbname and self.config.user and self.config.password and self.config.host):
            raise RuntimeError("Missing DB config in environment (DB_NAME/DB_USER/DB_PASS/DB_HOST).")
        self._conn = psycopg2.connect(
            dbname=self.config.dbname,
            user=self.config.user,
            password=self.config.password,
            host=self.config.host,
            port=self.config.port,
        )
        return self._conn

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> None:
        if self.test_mode and _looks_like_write(sql):
            print("[TEST_MODE] SKIP WRITE SQL:", sql)
            if params:
                print("[TEST_MODE] PARAMS:", params)
            return
        conn = self.connect()
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()

    def fetchall(self, sql: str, params: Sequence[Any] | None = None) -> list[tuple]:
        conn = self.connect()
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def _looks_like_write(sql: str) -> bool:
    s = (sql or "").strip().lower()
    # conservative: treat everything except SELECT/WITH as write-ish
    return not (s.startswith("select") or s.startswith("with"))

