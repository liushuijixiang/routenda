from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import json
import sqlite3
from typing import Any
from uuid import uuid4


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _tokens(text: str) -> set[str]:
    normalized = "".join(ch.lower() if ch.isalnum() else " " for ch in text)
    tokens = {item for item in normalized.split() if item}
    tokens.update(ch for ch in text if "\u4e00" <= ch <= "\u9fff")
    return tokens


@dataclass(frozen=True)
class SearchHit:
    id: str
    text: str
    metadata: dict[str, Any]
    score: float


class AgentSQLiteStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS kv (
                    namespace TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (namespace, key)
                );
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    text TEXT NOT NULL,
                    importance REAL NOT NULL,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    title TEXT NOT NULL,
                    text TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS feedback (
                    id TEXT PRIMARY KEY,
                    task TEXT NOT NULL,
                    signal REAL NOT NULL,
                    note TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def set_value(self, namespace: str, key: str, value: Any) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO kv(namespace, key, value, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(namespace, key)
                DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (namespace, key, json.dumps(value, ensure_ascii=False), _now()),
            )

    def get_value(self, namespace: str, key: str) -> Any | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM kv WHERE namespace = ? AND key = ?",
                (namespace, key),
            ).fetchone()
        return json.loads(row["value"]) if row else None

    def add_memory(
        self,
        text: str,
        *,
        scope: str = "global",
        importance: float = 0.5,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        memory_id = f"mem_{uuid4().hex}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memories(id, scope, text, importance, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_id,
                    scope,
                    text,
                    importance,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    _now(),
                ),
            )
        return memory_id

    def search_memories(self, query: str, *, scope: str = "global", limit: int = 5) -> list[SearchHit]:
        return self._search("memories", query, scope=scope, limit=limit)

    def add_document(
        self,
        text: str,
        *,
        source: str = "manual",
        title: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        document_id = f"doc_{uuid4().hex}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO documents(id, source, title, text, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    source,
                    title,
                    text,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    _now(),
                ),
            )
        return document_id

    def search_documents(self, query: str, *, limit: int = 5) -> list[SearchHit]:
        return self._search("documents", query, limit=limit)

    def add_feedback(
        self,
        task: str,
        signal: float,
        note: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        feedback_id = f"fb_{uuid4().hex}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO feedback(id, task, signal, note, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    feedback_id,
                    task,
                    signal,
                    note,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    _now(),
                ),
            )
        return feedback_id

    def feedback_summary(self, *, limit: int = 20) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT task, signal, note, metadata FROM feedback ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        if not rows:
            return {"count": 0, "average_signal": 0.0, "items": []}
        items = [
            {
                "task": row["task"],
                "signal": row["signal"],
                "note": row["note"],
                "metadata": json.loads(row["metadata"]),
            }
            for row in rows
        ]
        return {
            "count": len(items),
            "average_signal": sum(float(item["signal"]) for item in items) / len(items),
            "items": items,
        }

    def _search(
        self,
        table: str,
        query: str,
        *,
        scope: str | None = None,
        limit: int = 5,
    ) -> list[SearchHit]:
        query_tokens = _tokens(query)
        if not query_tokens:
            return []
        if table == "memories":
            sql = "SELECT id, text, metadata, importance FROM memories WHERE scope = ?"
            params: tuple[Any, ...] = (scope or "global",)
        else:
            sql = "SELECT id, text, metadata, 0.5 AS importance FROM documents"
            params = ()
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        hits: list[SearchHit] = []
        for row in rows:
            text = str(row["text"])
            overlap = len(query_tokens & _tokens(text))
            if overlap <= 0:
                continue
            score = overlap + float(row["importance"])
            hits.append(
                SearchHit(
                    id=str(row["id"]),
                    text=text,
                    metadata=json.loads(row["metadata"]),
                    score=score,
                )
            )
        return sorted(hits, key=lambda item: item.score, reverse=True)[:limit]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn
