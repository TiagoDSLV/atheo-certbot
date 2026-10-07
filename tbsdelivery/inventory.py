"""Inventaire des certificats : import de l'export CSV TBS, classement, recherche.

L'inventaire (inventory.yaml) fait le lien entre une commande TBS et le client
à qui livrer. Il est généré depuis l'export CSV du Certificate Center puis peut
être enrichi à la main (destinataires spécifiques, exclusions, notes) : ces
surcharges sont conservées lors d'un ré-import.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

# Champs saisis à la main dans inventory.yaml et conservés lors d'un ré-import
MANUAL_FIELDS = ("delivery", "dcv", "managed", "notes", "aliases", "client")

CATEGORY_LABELS = {
    "reissue": "Refabrication sous forfait",
    "renewal": "Fin de forfait - rachat à valider",
    "superseded": "Remplacé par une commande plus récente",
    "out_of_scope": "Hors périmètre (certificat non SSL)",
}

# Durée de vie maximale des certificats TLS (CA/B Forum SC-081) selon la date d'émission
VALIDITY_SCHEDULE = (
    (date(2026, 3, 15), 398),
    (date(2027, 3, 15), 200),
    (date(2029, 3, 15), 100),
    (date(9999, 12, 31), 47),
)


def max_validity(issued: date) -> int:
    for until, days in VALIDITY_SCHEDULE:
        if issued < until:
            return days
    return 47


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return value or "inconnu"


def safe_name(cn: str) -> str:
    """Nom de fichier à partir d'un CN (*.domaine.fr -> wildcard.domaine.fr)."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", (cn or "certificat").replace("*.", "wildcard."))


def brand_of(product: str) -> str:
    p = (product or "").lower()
    for brand in ("sectigo", "digicert", "geotrust", "rapidssl", "thawte", "globalsign",
                  "chambersign", "tbs x509", "positivessl"):
        if brand in p:
            return brand.replace(" ", "")
    return "autre"


def is_tls_product(product: str) -> bool:
    p = (product or "").lower()
    if any(k in p for k in ("eidas", "qscd", "authentification", "eiducio", "s/mime", "signature de code")):
        return False
    return any(k in p for k in ("ssl", "ucc", "multiwild", "wild", "multi-domaine", "san"))


def parse_fr_date(value: str) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


@dataclass
class Cert:
    ref: str
    cn: str
    client: str
    product: str = ""
    brand: str = ""
    sans: list = field(default_factory=list)
    sales_ref: str = ""
    ca_ref: str = ""
    expires: date | None = None
    forfait_end: date | None = None
    admin_name: str = ""
    admin_email: str = ""
    category: str = "reissue"
    managed: bool = True
    superseded_by: str = ""
    delivery: dict = field(default_factory=dict)
    dcv: dict = field(default_factory=dict)
    notes: str = ""
    aliases: list = field(default_factory=list)

    @property
    def client_slug(self) -> str:
        return slugify(self.client)

    @property
    def names(self) -> set:
        return {n.lower() for n in [self.cn, *self.sans] if n}

    def to_dict(self) -> dict:
        d = {
            "ref": self.ref,
            "cn": self.cn,
            "sans": self.sans,
            "client": self.client,
            "product": self.product,
            "brand": self.brand,
            "sales_ref": self.sales_ref,
            "ca_ref": self.ca_ref,
            "expires": self.expires.isoformat() if self.expires else None,
            "forfait_end": self.forfait_end.isoformat() if self.forfait_end else None,
            "admin_contact": {"name": self.admin_name, "email": self.admin_email},
            "category": self.category,
            "managed": self.managed,
        }
        if self.superseded_by:
            d["superseded_by"] = self.superseded_by
        if self.aliases:
            d["aliases"] = self.aliases
        if self.delivery:
            d["delivery"] = self.delivery
        if self.dcv:
            d["dcv"] = self.dcv
        if self.notes:
            d["notes"] = self.notes
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Cert":
        contact = d.get("admin_contact") or {}
        return cls(
            ref=str(d["ref"]),
            cn=d.get("cn", ""),
            client=d.get("client", ""),
            product=d.get("product", ""),
            brand=d.get("brand", "") or brand_of(d.get("product", "")),
            sans=list(d.get("sans") or []),
            sales_ref=d.get("sales_ref", "") or "",
            ca_ref=str(d.get("ca_ref", "") or ""),
            expires=parse_fr_date(str(d.get("expires") or "")),
            forfait_end=parse_fr_date(str(d.get("forfait_end") or "")),
            admin_name=contact.get("name", ""),
            admin_email=contact.get("email", ""),
            category=d.get("category", "reissue"),
            managed=bool(d.get("managed", True)),
            superseded_by=str(d.get("superseded_by", "") or ""),
            delivery=d.get("delivery") or {},
            dcv=d.get("dcv") or {},
            notes=d.get("notes", "") or "",
            aliases=[str(a) for a in (d.get("aliases") or [])],
        )


# --------------------------------------------------------------------------- CSV

COLS = {
    "sales_ref": "Votre réf",
    "ca_ref": "Réf CA",
    "cn": "CN",
    "product": "Nom Prod.",
    "ref": "Réf TBS Certificats",
    "expires": "Date d'expiration",
    "forfait_end": "Date de fin du forfait",
    "org": "Nom de l'org.",
    "company": "Raison sociale",
    "admin_name": "Contact admin.",
    "admin_email": "Mél du contact admin.",
    "sans": "SANs.",
}


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def read_tbs_csv(path: str | Path) -> list[Cert]:
    """Lit l'export CSV « liste des certificats » du Certificate Center TBS."""
    text = _read_text(Path(path))
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    certs: list[Cert] = []
    for row in reader:
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items() if k}
        ref = row.get(COLS["ref"], "")
        if not ref:
            continue
        sans = [s.strip() for s in row.get(COLS["sans"], "").split("|") if s.strip()]
        cn = row.get(COLS["cn"], "")
        sans = [s for s in sans if s.lower() != cn.lower()]
        product = row.get(COLS["product"], "")
        client = row.get(COLS["company"]) or row.get(COLS["org"]) or row.get(COLS["admin_name"]) or "INCONNU"
        certs.append(
            Cert(
                ref=ref,
                cn=cn,
                client=client,
                product=product,
                brand=brand_of(product),
                sans=sans,
                sales_ref=row.get(COLS["sales_ref"], ""),
                ca_ref=row.get(COLS["ca_ref"], ""),
                expires=parse_fr_date(row.get(COLS["expires"], "")),
                forfait_end=parse_fr_date(row.get(COLS["forfait_end"], "")),
                admin_name=row.get(COLS["admin_name"], ""),
                admin_email=row.get(COLS["admin_email"], ""),
            )
        )
    return certs


def classify(certs: list[Cert], margin_days: int = 15) -> list[Cert]:
    """Range chaque certificat : refabrication, rachat, remplacé ou hors périmètre."""
    for c in certs:
        if not is_tls_product(c.product):
            c.category, c.managed = "out_of_scope", False
        elif c.forfait_end is None or (
            c.expires and c.forfait_end <= c.expires + timedelta(days=margin_days)
        ):
            c.category = "renewal"
        else:
            c.category = "reissue"

    # Même CN commandé plusieurs fois : la commande la plus récente remplace l'ancienne
    by_cn: dict[str, list[Cert]] = {}
    for c in certs:
        if c.category != "out_of_scope":
            by_cn.setdefault(c.cn.lower(), []).append(c)
    for group in by_cn.values():
        if len(group) < 2:
            continue

        def recency(x: Cert):
            return (x.forfait_end or x.expires or date.min, x.expires or date.min, int(re.sub(r"\D", "", x.ref) or 0))

        group.sort(key=recency, reverse=True)
        newest = group[0]
        for old in group[1:]:
            old.category, old.managed, old.superseded_by = "superseded", False, newest.ref
    return certs


def estimate_reissues(c: Cert, lead_days: int = 30, today: date | None = None) -> int:
    """Nombre de refabrications restantes jusqu'à la fin du forfait (estimation)."""
    if c.category != "reissue" or not c.expires or not c.forfait_end:
        return 0
    today = today or date.today()
    count, current_expiry = 0, c.expires
    while True:
        # anticipation plafonnée au tiers de la durée de vie (certificats 47 jours en 2029)
        lead = min(lead_days, max_validity(current_expiry) // 3)
        reissue_on = max(current_expiry - timedelta(days=lead), today)
        if reissue_on >= c.forfait_end:
            break
        count += 1
        new_expiry = min(reissue_on + timedelta(days=max_validity(reissue_on)), c.forfait_end)
        if new_expiry <= current_expiry or count > 200:
            break
        current_expiry = new_expiry
        if current_expiry >= c.forfait_end:
            break
    return count


# --------------------------------------------------------------------- inventaire YAML

class Inventory:
    def __init__(self, certs: list[Cert], meta: dict | None = None):
        self.certs = certs
        self.meta = meta or {}

    # -- persistance
    @classmethod
    def load(cls, path: str | Path) -> "Inventory":
        p = Path(path)
        if not p.exists():
            return cls([], {})
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        certs = [Cert.from_dict(d) for d in data.get("certificates", [])]
        meta = {k: v for k, v in data.items() if k != "certificates"}
        return cls(certs, meta)

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        header = (
            "# Inventaire tbs-delivery - généré depuis l'export CSV TBS puis éditable.\n"
            "# Surcharges possibles par certificat (conservées au ré-import) :\n"
            "#   managed: false            -> ne plus livrer ce certificat\n"
            "#   delivery: {recipients: [client_admin, it@client.fr], cc: [internal]}\n"
            "#   dcv: {notify: [dns@client.fr]}\n"
            "#   aliases: ['1234567891']   -> autres références TBS (refabrications)\n"
            "#   notes: texte libre\n"
        )
        body = dict(self.meta)
        body["certificates"] = [c.to_dict() for c in self.certs]
        p.write_text(header + yaml.safe_dump(body, allow_unicode=True, sort_keys=False, width=120),
                     encoding="utf-8")

    def merge_from_csv(self, fresh: list[Cert]) -> tuple[int, int]:
        """Met à jour l'inventaire depuis un nouvel export en conservant les surcharges."""
        old = {c.ref: c for c in self.certs}
        added = 0
        for c in fresh:
            prev = old.get(c.ref)
            if prev is None:
                added += 1
                continue
            for f in MANUAL_FIELDS:
                val = getattr(prev, f)
                if f == "managed":
                    # une exclusion manuelle d'un certificat géré est conservée
                    if prev.managed is False and prev.category in ("reissue", "renewal") \
                            and c.category in ("reissue", "renewal"):
                        c.managed = False
                elif f == "client":
                    # un nom de client corrigé à la main dans l'inventaire est conservé
                    if prev.client:
                        c.client = prev.client
                elif val:
                    setattr(c, f, val)
        # références absentes du nouvel export (refabrications enregistrées comme alias) conservées
        fresh_refs = {c.ref for c in fresh}
        kept = [c for c in self.certs if c.ref not in fresh_refs and c.notes.startswith("[manuel]")]
        self.certs = fresh + kept
        return added, len(fresh)

    # -- recherche
    def by_ref(self, ref: str) -> Cert | None:
        ref = str(ref or "").strip()
        for c in self.certs:
            if c.ref == ref or ref in c.aliases:
                return c
        return None

    def find(self, ref: str = "", cn: str = "", sans: list | None = None) -> Cert | None:
        """Retrouve le certificat d'inventaire d'une commande TBS.

        Une refabrication crée une nouvelle référence TBS : si la référence est
        inconnue, on rapproche par CN puis par SAN, en privilégiant les
        certificats gérés.
        """
        hit = self.by_ref(ref) if ref else None
        if hit:
            return hit
        candidates: list[Cert] = []
        if cn:
            candidates = [c for c in self.certs if c.cn.lower() == cn.lower()]
        if not candidates and sans:
            wanted = {s.lower() for s in sans if s}
            candidates = [c for c in self.certs if c.names & wanted]
        if not candidates:
            return None
        candidates.sort(key=lambda c: (c.managed, c.category == "reissue", c.forfait_end or date.min), reverse=True)
        return candidates[0]

    def managed(self) -> list[Cert]:
        return [c for c in self.certs if c.managed and c.category in ("reissue", "renewal")]
