"""SQLite persistence. Records are stored as JSON plus indexed filter columns."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .models import Article, Trial

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    uid TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    abstract TEXT NOT NULL DEFAULT '',
    year INTEGER,
    subtype TEXT,
    evidence TEXT,
    outcome TEXT DEFAULT 'na',
    solid INTEGER NOT NULL DEFAULT 0,
    is_preprint INTEGER NOT NULL DEFAULT 0,
    cited_by INTEGER,
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_articles_year ON articles(year);
CREATE INDEX IF NOT EXISTS idx_articles_subtype ON articles(subtype);

CREATE TABLE IF NOT EXISTS trials (
    nct_id TEXT PRIMARY KEY,
    status TEXT,
    subtype_fit TEXT,
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tags (
    item_type TEXT NOT NULL,          -- 'article' | 'trial'
    item_id TEXT NOT NULL,
    mechanism_id TEXT NOT NULL,
    matches TEXT NOT NULL,            -- JSON list of matched snippets
    PRIMARY KEY (item_type, item_id, mechanism_id)
);
CREATE INDEX IF NOT EXISTS idx_tags_mech ON tags(mechanism_id, item_type);

CREATE TABLE IF NOT EXISTS ingest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    n_items INTEGER DEFAULT 0,
    status TEXT,
    message TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path, *, readonly: bool = False):
        if readonly:
            self.conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
            self.conn.row_factory = sqlite3.Row
            return
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), timeout=30)
        self.conn.row_factory = sqlite3.Row
        if str(path) != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(articles)")}
        if "outcome" not in cols:
            self.conn.execute("ALTER TABLE articles ADD COLUMN outcome TEXT DEFAULT 'na'")
        if "solid" not in cols:
            self.conn.execute("ALTER TABLE articles ADD COLUMN solid INTEGER NOT NULL DEFAULT 0")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def commit(self) -> None:
        self.conn.commit()

    # ------------------------------------------------------------ tags
    def _set_tags(self, item_type: str, item_id: str, tags: dict[str, list[str]]) -> None:
        self.conn.execute("DELETE FROM tags WHERE item_type=? AND item_id=?", (item_type, item_id))
        self.conn.executemany(
            "INSERT INTO tags(item_type, item_id, mechanism_id, matches) VALUES (?,?,?,?)",
            [(item_type, item_id, mid, json.dumps(m)) for mid, m in tags.items()],
        )

    def tags_for(self, item_type: str, item_id: str) -> dict[str, list[str]]:
        rows = self.conn.execute(
            "SELECT mechanism_id, matches FROM tags WHERE item_type=? AND item_id=?", (item_type, item_id))
        return {r["mechanism_id"]: json.loads(r["matches"]) for r in rows}

    def all_tags(self, item_type: str) -> dict[str, list[str]]:
        """{item_id: [mechanism_ids]}"""
        out: dict[str, list[str]] = {}
        for r in self.conn.execute("SELECT item_id, mechanism_id FROM tags WHERE item_type=?", (item_type,)):
            out.setdefault(r["item_id"], []).append(r["mechanism_id"])
        return out

    # ------------------------------------------------------------ articles
    def get_article(self, uid: str) -> Article | None:
        row = self.conn.execute("SELECT data FROM articles WHERE uid=?", (uid,)).fetchone()
        return Article.from_dict(json.loads(row["data"])) if row else None

    def put_article(self, a: Article, tags: dict[str, list[str]]) -> None:
        self.conn.execute(
            """INSERT INTO articles(uid,title,abstract,year,subtype,evidence,outcome,solid,is_preprint,cited_by,data,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(uid) DO UPDATE SET title=excluded.title, abstract=excluded.abstract,
                 year=excluded.year, subtype=excluded.subtype, evidence=excluded.evidence, outcome=excluded.outcome, solid=excluded.solid,
                 is_preprint=excluded.is_preprint, cited_by=excluded.cited_by, data=excluded.data,
                 updated_at=excluded.updated_at""",
            (a.uid, a.title, a.abstract, a.year, a.subtype, a.evidence, a.outcome, int(a.solid_trial), int(a.is_preprint), a.cited_by,
             json.dumps(a.to_dict()), _now()),
        )
        self._set_tags("article", a.uid, tags)

    def iter_articles(self) -> Iterator[Article]:
        for row in self.conn.execute("SELECT data FROM articles"):
            yield Article.from_dict(json.loads(row["data"]))

    def article_facts(self) -> list[sqlite3.Row]:
        """Lightweight rows for scoring without JSON decoding."""
        return self.conn.execute("SELECT uid, year, subtype, evidence, outcome, solid, is_preprint FROM articles").fetchall()

    def search_articles(self, q: str | None = None, mechanism: str | None = None, year_from: int | None = None,
                        subtype: str | None = None, evidence: str | None = None, preprints: bool | None = None,
                        limit: int = 50, offset: int = 0, outcome_in: list[str] | None = None,
                        evidence_in: list[str] | None = None) -> tuple[list[Article], int]:
        where, args = [], []
        for col, values in (("outcome", outcome_in), ("evidence", evidence_in)):
            if values:
                where.append(f"{col} IN ({','.join('?' * len(values))})")  # column names are fixed strings
                args += list(values)
        for term in (q or "").split():
            where.append("(title LIKE ? OR abstract LIKE ?)")
            args += [f"%{term}%", f"%{term}%"]
        if mechanism:
            where.append("uid IN (SELECT item_id FROM tags WHERE item_type='article' AND mechanism_id=?)")
            args.append(mechanism)
        if year_from:
            where.append("year >= ?")
            args.append(year_from)
        if subtype:
            where.append("subtype = ?")
            args.append(subtype)
        if evidence:
            where.append("evidence = ?")
            args.append(evidence)
        if preprints is not None:
            where.append("is_preprint = ?")
            args.append(int(preprints))
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        total = self.conn.execute(f"SELECT COUNT(*) FROM articles {clause}", args).fetchone()[0]
        rows = self.conn.execute(
            f"SELECT data FROM articles {clause} ORDER BY year DESC, cited_by DESC LIMIT ? OFFSET ?",
            [*args, limit, offset])
        return [Article.from_dict(json.loads(r["data"])) for r in rows], total

    # ------------------------------------------------------------ trials
    def get_trial(self, nct_id: str) -> Trial | None:
        row = self.conn.execute("SELECT data FROM trials WHERE nct_id=?", (nct_id,)).fetchone()
        return Trial.from_dict(json.loads(row["data"])) if row else None

    def put_trial(self, t: Trial, tags: dict[str, list[str]]) -> None:
        self.conn.execute(
            """INSERT INTO trials(nct_id,status,subtype_fit,data,updated_at) VALUES (?,?,?,?,?)
               ON CONFLICT(nct_id) DO UPDATE SET status=excluded.status, subtype_fit=excluded.subtype_fit,
                 data=excluded.data, updated_at=excluded.updated_at""",
            (t.nct_id, t.status, t.subtype_fit, json.dumps(t.to_dict()), _now()),
        )
        self._set_tags("trial", t.nct_id, tags)

    def delete_trial(self, nct_id: str) -> None:
        self.conn.execute("DELETE FROM trials WHERE nct_id=?", (nct_id,))
        self.conn.execute("DELETE FROM tags WHERE item_type='trial' AND item_id=?", (nct_id,))

    def iter_trials(self) -> Iterator[Trial]:
        for row in self.conn.execute("SELECT data FROM trials"):
            yield Trial.from_dict(json.loads(row["data"]))

    # ------------------------------------------------------------ runs & stats
    def start_run(self, source: str) -> int:
        cur = self.conn.execute("INSERT INTO ingest_runs(source, started_at, status) VALUES (?,?,'running')",
                                (source, _now()))
        self.conn.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, n_items: int, status: str, message: str = "") -> None:
        self.conn.execute("UPDATE ingest_runs SET finished_at=?, n_items=?, status=?, message=? WHERE id=?",
                          (_now(), n_items, status, message, run_id))
        self.conn.commit()

    def recent_runs(self, limit: int = 10) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM ingest_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def stats(self) -> dict[str, int]:
        q = lambda sql: self.conn.execute(sql).fetchone()[0]  # noqa: E731
        return {
            "articles": q("SELECT COUNT(*) FROM articles"),
            "msa_c_articles": q("SELECT COUNT(*) FROM articles WHERE subtype='msa_c'"),
            "preprints": q("SELECT COUNT(*) FROM articles WHERE is_preprint=1"),
            "trials": q("SELECT COUNT(*) FROM trials"),
            "open_trials": q("SELECT COUNT(*) FROM trials WHERE status IN "
                             "('RECRUITING','NOT_YET_RECRUITING','ENROLLING_BY_INVITATION','AVAILABLE')"),
        }
