"""Rapport du parc, plan d'action et synthèse interne périodique."""

from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
from pathlib import Path

from .inventory import CATEGORY_LABELS, Cert, Inventory, estimate_reissues
from .mailer import Mailer
from .state import State


def _d(x: date | None) -> str:
    return x.strftime("%d/%m/%Y") if x else ""


def action_for(c: Cert, today: date, lead_days: int) -> tuple[str, date | None]:
    if c.category == "out_of_scope":
        return "Non concerné", None
    if c.category == "superseded":
        return f"Archiver dans TBSCertBot (remplacé par {c.superseded_by})", None
    if c.category == "renewal":
        if c.forfait_end is None:
            return f"Rachat à proposer avant le {_d(c.expires)} (pas de refabrication incluse)", c.expires
        return f"Rachat à proposer avant le {_d(c.expires)} (fin de forfait {_d(c.forfait_end)})", c.expires
    when = (c.expires - timedelta(days=lead_days)) if c.expires else None
    if when and when <= today:
        return "Refabrication immédiate (dans la fenêtre)", today
    return f"Refabrication automatique vers le {_d(when)}", when


def build_rows(inv: Inventory, today: date, lead_days: int) -> list[dict]:
    rows = []
    for c in inv.certs:
        action, due = action_for(c, today, lead_days)
        days_left = (c.expires - today).days if c.expires else None
        note = ""
        if c.brand == "globalsign" and c.category in ("reissue", "renewal"):
            note = "GlobalSign : vérifier le support de la refabrication via TBSCertBot"
        elif c.brand in ("digicert", "geotrust", "rapidssl", "thawte"):
            note = "DigiCert : DCV DNS en TXT possible"
        rows.append({
            "Client": c.client,
            "CN": c.cn,
            "SAN": " | ".join(c.sans),
            "Réf TBS": c.ref,
            "Produit": c.product,
            "Expiration": _d(c.expires),
            "Jours restants": "" if days_left is None else days_left,
            "Fin de forfait": _d(c.forfait_end),
            "Catégorie": CATEGORY_LABELS.get(c.category, c.category),
            "Action": action,
            "Échéance action": _d(due),
            "Refabrications restantes (estim.)": estimate_reissues(c, lead_days, today) if c.category == "reissue" else "",
            "Contact admin.": c.admin_email,
            "Votre réf": c.sales_ref,
            "Remarque": note,
            "_days": days_left if days_left is not None else 10**6,
            "_cat": c.category,
        })
    order = {"reissue": 0, "renewal": 0, "superseded": 2, "out_of_scope": 3}
    rows.sort(key=lambda r: (order.get(r["_cat"], 9), r["_days"]))
    return rows


def write_csv(rows: list[dict], path: str | Path) -> None:
    cols = [k for k in rows[0] if not k.startswith("_")] if rows else []
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, delimiter=";", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def summary(rows: list[dict], today: date, horizon: int = 60) -> str:
    cats: dict[str, int] = {}
    for r in rows:
        cats[r["_cat"]] = cats.get(r["_cat"], 0) + 1
    total_reissues = sum(int(r["Refabrications restantes (estim.)"] or 0) for r in rows)
    lines = [f"Parc au {today:%d/%m/%Y} : {len(rows)} lignes"]
    for k, label in CATEGORY_LABELS.items():
        lines.append(f"  - {label} : {cats.get(k, 0)}")
    lines.append(f"  - Refabrications à prévoir d'ici la fin des forfaits (estim.) : {total_reissues}")
    urgent = [r for r in rows if r["_cat"] in ("reissue", "renewal") and r["_days"] <= horizon]
    lines.append("")
    lines.append(f"Échéances à moins de {horizon} jours ({len(urgent)}) :")
    for r in urgent:
        lines.append(f"  J-{r['_days']:>3}  {r['Expiration']}  {r['CN']:<34} {r['Client'][:34]:<34} {r['Action']}")
    return "\n".join(lines)


# --------------------------------------------------------------- synthèse interne

def digest(cfg: dict, send: bool = True) -> str:
    inv = Inventory.load(cfg["paths"]["inventory"])
    state = State(cfg["paths"]["state_db"])
    pol = cfg["policy"]
    today = date.today()
    lines: list[str] = [f"Synthèse certificats - {today:%d/%m/%Y}", ""]

    open_dcv = [r for r in state.dcv_open() if r["status"] in ("pending", "notified", "http")]
    lines.append(f"DCV en attente chez les clients : {len(open_dcv)}")
    seen = set()
    for r in open_dcv:
        if r["tbs_ref"] in seen:
            continue
        seen.add(r["tbs_ref"])
        lines.append(f"  - {r['client']} / {r['cn']} (réf. {r['tbs_ref']}) : {r['method']}, "
                     f"{r['sent_count']} envoi(s), depuis le {r['first_seen'][:10]}")

    lines += ["", f"Rachats à proposer (fin de forfait sous {pol['renewal_alert_days']} jours) :"]
    for c in sorted(inv.managed(), key=lambda x: x.expires or date.max):
        if c.category == "renewal" and c.expires and (c.expires - today).days <= int(pol["renewal_alert_days"]):
            lines.append(f"  - J-{(c.expires - today).days} {c.client} / {c.cn} (réf. {c.ref}, {c.product})")

    lines += ["", f"Certificats sous forfait expirant sous {pol['expiry_alert_days']} jours sans nouvelle livraison :"]
    for c in inv.managed():
        if c.category != "reissue" or not c.expires:
            continue
        last = state.last_delivery(c.ref)
        current_expiry = c.expires
        if last and last["not_after"]:
            current_expiry = max(current_expiry, date.fromisoformat(last["not_after"]))
        if (current_expiry - today).days <= int(pol["expiry_alert_days"]):
            lines.append(f"  - J-{(current_expiry - today).days} {c.client} / {c.cn} (réf. {c.ref})")

    failed = [r for r in state.deliveries(200) if r["status"] == "failed"
              and datetime.fromisoformat(r["created_at"]).date() >= today - timedelta(days=7)]
    lines += ["", f"Échecs de livraison (7 derniers jours) : {len(failed)}"]
    for r in failed:
        lines.append(f"  - {r['created_at'][:16]} {r['cn']} (réf. {r['tbs_ref']}) : {r['detail']}")

    recent = [r for r in state.deliveries(200) if r["status"] in ("sent", "dry-run")
              and datetime.fromisoformat(r["created_at"]).date() >= today - timedelta(days=7)]
    lines += ["", f"Livraisons (7 derniers jours) : {len(recent)}"]
    for r in recent:
        lines.append(f"  - {r['created_at'][:10]} {r['client']} / {r['cn']} -> {r['recipients']} [{r['status']}]")
    state.close()

    text = "\n".join(lines) + "\n"
    if send:
        mailer = Mailer(cfg)
        company = cfg["internal"]["company"]
        mailer.send(mailer.build(to=[cfg["internal"]["team_email"]], cc=None,
                                 subject=f"[{company}][INTERNE] Synthèse certificats du {today:%d/%m/%Y}", body=text))
    return text
