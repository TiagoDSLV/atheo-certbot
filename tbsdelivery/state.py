"""État persistant (SQLite) : livraisons effectuées, demandes DCV, alias de références."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tbs_ref TEXT NOT NULL,
    inventory_ref TEXT,
    client TEXT,
    cn TEXT,
    serial TEXT NOT NULL,
    not_after TEXT,
    package_path TEXT,
    recipients TEXT,
    status TEXT NOT NULL,           -- sent | dry-run | failed | skipped
    detail TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deliv_serial ON deliveries(serial);

CREATE TABLE IF NOT EXISTS dcv_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tbs_ref TEXT NOT NULL,
    inventory_ref TEXT,
    client TEXT,
    cn TEXT,
    method TEXT,
    record_name TEXT,
    record_value TEXT,
    registrar TEXT,
    status TEXT NOT NULL,           -- pending | notified | auto | http | failed | done
    sent_count INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT,
    last_seen TEXT,
    last_sent TEXT,
    detail TEXT,
    UNIQUE(tbs_ref, record_name, record_value)
);

CREATE TABLE IF NOT EXISTS ref_aliases (
    tbs_ref TEXT PRIMARY KEY,
    inventory_ref TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


# Statuts qui comptent comme « déjà livré » : en cas de doute (envoi interrompu ou
# partiel), aucune relivraison automatique ; seul --force (décision humaine) relivre.
DELIVERED_STATUSES = ("sending", "partial", "uncertain", "sent", "dry-run")
_DELIVERED_SQL = ",".join(f"'{s}'" for s in DELIVERED_STATUSES)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class State:
    def __init__(self, path: str | Path):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(p), timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self):
        self.db.close()

    # -- alias (une refabrication crée une nouvelle référence TBS)
    def alias_of(self, tbs_ref: str) -> str | None:
        row = self.db.execute("SELECT inventory_ref FROM ref_aliases WHERE tbs_ref=?", (tbs_ref,)).fetchone()
        return row["inventory_ref"] if row else None

    def set_alias(self, tbs_ref: str, inventory_ref: str) -> None:
        if tbs_ref == inventory_ref:
            return
        self.db.execute(
            "INSERT OR REPLACE INTO ref_aliases(tbs_ref, inventory_ref, created_at) VALUES (?,?,?)",
            (tbs_ref, inventory_ref, now_iso()),
        )
        self.db.commit()

    # -- livraisons
    def already_delivered(self, serial: str) -> bool:
        row = self.db.execute(
            f"SELECT 1 FROM deliveries WHERE serial=? AND status IN ({_DELIVERED_SQL}) LIMIT 1", (serial,)
        ).fetchone()
        return row is not None

    def reserve_delivery(self, *, serial: str, force: bool = False, **kw) -> int | None:
        """Réserve la livraison d'un numéro de série AVANT tout envoi (ligne « sending »).

        Vérification et insertion se font sous verrou d'écriture (BEGIN IMMEDIATE) :
        deux exécutions simultanées ne peuvent pas réserver le même certificat.
        Retourne l'id de la ligne, ou None si une livraison existe déjà (hors force).
        """
        self.db.commit()
        try:
            self.db.execute("BEGIN IMMEDIATE")
            if not force and self.already_delivered(serial):
                self.db.rollback()
                return None
            kw.update(serial=serial, status="sending", created_at=now_iso())
            cols = ",".join(kw)
            cur = self.db.execute(f"INSERT INTO deliveries({cols}) VALUES ({','.join('?' * len(kw))})",
                                  tuple(kw.values()))
            self.db.commit()
            return cur.lastrowid
        except BaseException:
            self.db.rollback()
            raise

    def update_delivery(self, delivery_id: int, **kw) -> None:
        sets = ",".join(f"{k}=?" for k in kw)
        with self.db:  # commit, ou rollback (verrou libéré) en cas d'erreur
            self.db.execute(f"UPDATE deliveries SET {sets} WHERE id=?", (*kw.values(), delivery_id))

    def interrupted_deliveries(self, before: str):
        """Livraisons restées « sending » (processus interrompu pendant l'envoi)."""
        return self.db.execute(
            "SELECT * FROM deliveries WHERE status='sending' AND created_at < ? ORDER BY id", (before,)
        ).fetchall()

    def deliveries_to_check(self, since: str):
        """Envois partiels ou incertains depuis `since`, non suivis d'une relivraison réussie."""
        return self.db.execute(
            "SELECT * FROM deliveries d WHERE d.status IN ('sending','partial','uncertain') AND d.created_at >= ? "
            "AND NOT EXISTS (SELECT 1 FROM deliveries n WHERE n.serial=d.serial AND n.id>d.id "
            "AND n.status IN ('sent','dry-run')) ORDER BY d.id",
            (since,),
        ).fetchall()

    def record_delivery(self, **kw) -> None:
        kw.setdefault("created_at", now_iso())
        cols = ",".join(kw)
        self.db.execute(f"INSERT INTO deliveries({cols}) VALUES ({','.join('?' * len(kw))})", tuple(kw.values()))
        self.db.commit()

    def last_delivery(self, inventory_ref: str):
        return self.db.execute(
            "SELECT * FROM deliveries WHERE inventory_ref=? AND status IN ('sent','dry-run') "
            "ORDER BY id DESC LIMIT 1",
            (inventory_ref,),
        ).fetchone()

    def deliveries(self, limit: int = 50):
        return self.db.execute("SELECT * FROM deliveries ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    # -- DCV
    def dcv_get(self, tbs_ref: str, name: str, value: str):
        return self.db.execute(
            "SELECT * FROM dcv_requests WHERE tbs_ref=? AND record_name=? AND record_value=?",
            (tbs_ref, name, value),
        ).fetchone()

    def dcv_seen(self, *, tbs_ref, inventory_ref, client, cn, method, name, value, registrar,
                 status="pending", detail="") -> str:
        """Enregistre un challenge DCV vu par le hook. Retourne 'new' ou 'known'."""
        row = self.dcv_get(tbs_ref, name, value)
        ts = now_iso()
        if row is None:
            self.db.execute(
                "INSERT INTO dcv_requests(tbs_ref, inventory_ref, client, cn, method, record_name, record_value,"
                " registrar, status, sent_count, first_seen, last_seen, detail) VALUES (?,?,?,?,?,?,?,?,?,0,?,?,?)",
                (tbs_ref, inventory_ref, client, cn, method, name, value, registrar, status, ts, ts, detail),
            )
            self.db.commit()
            return "new"
        new_status = row["status"] if row["status"] in ("notified", "auto", "done") else status
        self.db.execute("UPDATE dcv_requests SET last_seen=?, status=?, detail=? WHERE id=?",
                        (ts, new_status, detail or row["detail"], row["id"]))
        self.db.commit()
        return "known"

    def dcv_mark_sent(self, ids: list[int], status: str = "notified") -> None:
        ts = now_iso()
        for i in ids:
            self.db.execute("UPDATE dcv_requests SET status=?, sent_count=sent_count+1, last_sent=? WHERE id=?",
                            (status, ts, i))
        self.db.commit()

    def dcv_close(self, *, tbs_refs: list[str] = (), inventory_ref: str = "") -> None:
        """Marque les demandes DCV comme résolues une fois le certificat livré."""
        if tbs_refs:
            q = ",".join("?" * len(tbs_refs))
            self.db.execute(f"UPDATE dcv_requests SET status='done' WHERE tbs_ref IN ({q})", list(tbs_refs))
        if inventory_ref:
            self.db.execute("UPDATE dcv_requests SET status='done' WHERE inventory_ref=?", (inventory_ref,))
        self.db.commit()

    def dcv_open(self):
        return self.db.execute(
            "SELECT * FROM dcv_requests WHERE status NOT IN ('done') ORDER BY tbs_ref, id"
        ).fetchall()
