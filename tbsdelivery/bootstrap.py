"""Génère le script de mise sous gestion des certificats dans TBSCertBot."""

from __future__ import annotations

import shlex
from datetime import date

from .inventory import Inventory


def bootstrap_script(cfg: dict, inv: Inventory) -> str:
    tcfg = cfg["tbscertbot"]
    by_brand = tcfg.get("dcv_method_by_brand") or {}
    default_dcv = tcfg.get("dcv_method_default", "CNAME_CSR_HASH")
    q = shlex.quote
    out = [
        "#!/bin/sh",
        f"# Mise sous gestion TBSCertBot - généré par tbs-delivery le {date.today():%d/%m/%Y}",
        "# À exécuter ÉTAPE PAR ÉTAPE sur le hub (commencer par 1 ou 2 certificats de test).",
        "# 'import' est interactif : TBSCertBot demande le chemin de la clé privée.",
        "#   - clé connue (générée par Athéo) : indiquer son chemin ;",
        "#   - clé inconnue (CSR généré chez le client) : laisser vide, la 1re refabrication",
        "#     se fera avec une nouvelle clé (reissue <ref> now --no-reuse-keys).",
        "set -u",
        f"cd {q(tcfg.get('path', '/opt/tbscertbot'))} || exit 1",
        f"TBS={q(tcfg.get('php', '/usr/bin/php'))}' tbscertbot.php'",
        "",
    ]
    managed = [c for c in inv.certs if c.managed and c.category in ("reissue", "renewal")]
    for c in sorted(managed, key=lambda x: (x.client, x.cn)):
        dcv = by_brand.get(c.brand, default_dcv)
        out.append(f"# --- {c.client} | {c.cn} | {c.product} | exp. {c.expires} | "
                   f"fin forfait {c.forfait_end or 'aucune'}")
        if c.brand == "globalsign":
            out.append("# ATTENTION GlobalSign : vérifier avec TBS le support de la refabrication via TBSCertBot")
        out.append(f"$TBS import {c.ref}")
        out.append(f"$TBS set-profile {c.ref} request.dcv {dcv}")
        # aucun rachat automatique : un renouvellement est une décision commerciale
        out.append(f"$TBS renewal disable {c.ref} {q('Rachat soumis a validation commerciale Atheo')}")
        if c.category == "reissue":
            out.append(f"$TBS reissue {c.ref} auto on")
            out.append(f"$TBS reissue {c.ref} plan")
        else:
            out.append("# forfait terminé à l'expiration : rachat à valider, pas de refabrication automatique")
        out.append("")
    skipped = [c for c in inv.certs if c not in managed]
    if skipped:
        out.append("# Non importés :")
        for c in skipped:
            reason = {"superseded": f"remplacé par {c.superseded_by}", "out_of_scope": "hors périmètre SSL"}.get(
                c.category, "exclu manuellement (managed: false)")
            out.append(f"#   {c.ref}  {c.cn}  ({c.client}) - {reason}")
    out += ["", "$TBS checkup", "$TBS show", ""]
    return "\n".join(out)


CONF_SNIPPET = """; --- extrait data/conf.ini de TBSCertBot pour tbs-delivery ---
[HOOKS]
dcv = "/opt/tbs-delivery/hooks/tbs-dcv.sh"
download = "/opt/tbs-delivery/hooks/tbs-download.sh"

; Refabrication automatique X jours avant expiration (forfaits)
emergencyReissue = 30
; Réutiliser clé et CSR à la refabrication : le CNAME de DCV (CNAME_CSR_HASH) reste
; alors identique d'une refabrication à l'autre (à confirmer avec TBS/Sectigo)
reuse-keys = 1

; Logs + rapport d'erreur par mail
logLevel = info
sendLog = certificats@atheo.net < data/log/currentlog.txt
emailFrom = certificats@atheo.net
"""
