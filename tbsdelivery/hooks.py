"""Traitements appelés par les hooks TBSCertBot (« download » et « dcv »).

TBSCertBot exécute le script de hook avec les variables d'environnement
PHP_TBS_* (voir manuel TBSCertBot §3.1.3). Ces fonctions les lisent,
retrouvent le client dans l'inventaire et agissent.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import templates
from .dns_gandi import GandiError, upsert_record
from .inventory import Cert, Inventory, safe_name
from .mailer import Mailer, resolve_recipients
from .package import PackageError, build_package, peek_serial
from .state import State

log = logging.getLogger("tbs-delivery")


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _locate(inv: Inventory, state: State, ref: str, cn: str, sans: list) -> Cert | None:
    alias = state.alias_of(ref) if ref else None
    if alias:
        hit = inv.by_ref(alias)
        if hit:
            return hit
    return inv.find(ref=ref, cn=cn, sans=sans)


# ------------------------------------------------------------------------- download

def handle_download(cfg: dict, force: bool = False) -> int:
    progress = {"sending": False}  # passe à True dès le premier envoi au client
    try:
        return _handle_download(cfg, force, progress)
    except Exception as exc:  # noqa: BLE001 - aucune erreur ne doit rester silencieuse
        _unexpected_error(cfg, "download", exc, record=True, maybe_sent=progress["sending"])
        return 0  # ne pas faire échouer TBSCertBot : l'alerte interne suffit


def _handle_download(cfg: dict, force: bool, progress: dict) -> int:
    ref = _env("PHP_TBS_REFERENCE")
    cn = _env("PHP_TBS_CN")
    sans = [s.strip() for s in _env("PHP_TBS_SAN").split(",") if s.strip()]
    key, cert, chain = _env("PHP_TBS_KEY"), _env("PHP_TBS_CERT"), _env("PHP_TBS_CHAIN")
    force = force or _env("TBS_DELIVERY_FORCE") in ("1", "true", "yes")
    log.info("hook download : ref=%s cn=%s", ref, cn)

    inv = Inventory.load(cfg["paths"]["inventory"])
    state = State(cfg["paths"]["state_db"])
    mailer = Mailer(cfg)
    internal = cfg["internal"]["team_email"]
    company = cfg["internal"]["company"]
    signature = cfg["internal"]["signature"]
    dcfg = cfg["delivery"]
    dspec = dict(dcfg)  # réglages effectifs : config globale + surcharge du certificat

    serial = peek_serial(cert) if cert else None
    if serial and state.already_delivered(serial) and not force:
        log.info("certificat %s (série %s) déjà livré : ignoré", cn, serial)
        state.close()
        return 0

    try:
        item = _locate(inv, state, ref, cn, sans)
        if item is None:
            log.warning("ref %s (%s) absente de l'inventaire : livraison à l'équipe interne uniquement", ref, cn)
            client, to, cc, admin = "NON RÉFÉRENCÉ", [internal], [], ""
        else:
            if not item.managed:
                log.info("ref %s (%s) marquée non gérée : rien à livrer", ref, item.cn)
                return 0
            client, admin = item.client, item.admin_email
            dspec.update(item.delivery or {})
            to = resolve_recipients(dspec.get("recipients", []), client_admin=admin, internal=internal)
            cc = resolve_recipients(dspec.get("cc", []), client_admin=admin, internal=internal)
            if not to:
                log.warning("aucun destinataire pour %s : envoi à l'équipe interne", item.cn)
                to = [internal]
            if ref and ref != item.ref:
                state.set_alias(ref, item.ref)

        out_dir = Path(cfg["paths"]["deliveries"]) / (item.client_slug if item else "non-reference") / safe_name(cn)
        pkg = build_package(
            key_path=key, cert_path=cert, chain_path=chain, out_dir=out_dir, client=client, company=company,
            pfx=bool(dspec.get("pfx", True)), pfx_legacy=bool(dspec.get("pfx_legacy", False)),
            zip_encrypt=bool(dspec.get("zip_encrypt", True)),
        )
    except PackageError as exc:
        log.error("ref %s : fabrication impossible : %s", ref, exc)
        state.record_delivery(tbs_ref=ref, inventory_ref=None, client=None, cn=cn, serial=f"ERR-{ref}",
                              status="failed", detail=str(exc))
        _alert_internal(mailer, cfg, f"Échec de fabrication du livrable {cn} (réf. {ref})", str(exc))
        state.close()
        return 0  # ne pas faire échouer TBSCertBot : l'alerte interne suffit

    attach_ok = pkg.zip_path.stat().st_size <= float(dspec.get("attach_max_mb", 15)) * 1024 * 1024
    channel = "internal_only" if item is None else _password_channel(dspec, cn)
    status, detail = "sent", ""
    progress["sending"] = True
    try:
        subject, body = templates.delivery(client=client, info=pkg.info, zip_name=pkg.zip_path.name,
                                           password_channel=channel, attached=attach_ok,
                                           signature=signature, company=company)
        res1 = mailer.send(mailer.build(to=to, cc=cc, subject=subject, body=body,
                                        attachments=[pkg.zip_path] if attach_ok else []))
        if channel == "internal_only" or item is None:
            s2, b2 = templates.password_internal(client=client, info=pkg.info, zip_name=pkg.zip_path.name,
                                                 password_value=pkg.password, recipients=to,
                                                 signature=signature, company=company)
            res2 = mailer.send(mailer.build(to=[internal], cc=None, subject=s2, body=b2))
        else:
            s2, b2 = templates.password(info=pkg.info, zip_name=pkg.zip_path.name, password_value=pkg.password,
                                        signature=signature, company=company)
            res2 = mailer.send(mailer.build(to=to, cc=None, subject=s2, body=b2))
        if mailer.dry_run:
            status, detail = "dry-run", f"{res1} | {res2}"
    except Exception as exc:  # noqa: BLE001 - toute erreur SMTP doit être tracée
        status, detail = "failed", f"envoi : {exc}"
        log.exception("envoi impossible pour %s", cn)
        _alert_internal(mailer, cfg, f"Échec d'envoi du certificat {cn} ({client})",
                        f"{exc}\nArchive : {pkg.zip_path}\nRelance : php tbscertbot.php test-hook download {ref}")

    state.record_delivery(
        tbs_ref=ref, inventory_ref=item.ref if item else None, client=client, cn=pkg.info.cn,
        serial=pkg.info.serial, not_after=pkg.info.not_after.date().isoformat(),
        package_path=str(pkg.zip_path), recipients=", ".join(to + (cc or [])), status=status, detail=detail,
    )
    if status != "failed":
        state.dcv_close(tbs_refs=[ref], inventory_ref=item.ref if item else "")
    log.info("livraison %s : %s -> %s (%s)", status, pkg.info.cn, ", ".join(to), pkg.zip_path.name)
    state.close()
    return 0


def _password_channel(dspec: dict, cn: str) -> str:
    """Canal du mot de passe ; toute valeur inconnue retombe sur l'équipe interne."""
    channel = dspec.get("password_channel", "separate_email")
    if channel not in ("separate_email", "internal_only"):
        log.warning("password_channel inconnu (%r) pour %s : mot de passe envoyé à l'équipe interne", channel, cn)
        return "internal_only"
    return channel


def _unexpected_error(cfg: dict, hook: str, exc: Exception, record: bool, maybe_sent: bool = False) -> None:
    """Erreur non prévue dans un hook : journal, trace en base (download) et alerte interne.

    Le garde-fou n'envoie rien au client ; `maybe_sent` signale que l'erreur est
    survenue après le début des envois (des mails ont pu partir). L'alerte ne reprend que le type et le message de
    l'exception, jamais le contenu des fichiers (clé privée) ni le mot de passe.
    """
    ref, cn = _env("PHP_TBS_REFERENCE"), _env("PHP_TBS_CN")
    log.exception("hook %s : erreur inattendue pour ref=%s cn=%s", hook, ref, cn)
    detail = f"{type(exc).__name__} : {exc}"
    if record:
        try:
            state = State(cfg["paths"]["state_db"])
            state.record_delivery(tbs_ref=ref, inventory_ref=None, client=None, cn=cn, serial=f"ERR-{ref}",
                                  status="failed", detail=detail)
            state.close()
        except Exception:  # noqa: BLE001
            log.exception("impossible d'enregistrer l'échec en base")
    try:
        mailer = Mailer(cfg)
    except Exception:  # noqa: BLE001
        log.exception("impossible de préparer l'alerte interne")
        return
    if maybe_sent:
        advice = ("ATTENTION : l'erreur est survenue après le début des envois. Des mails ont pu partir au "
                  "client (archive et mot de passe). Vérifier le journal et la boîte d'envoi AVANT toute "
                  "relance : une relance enverrait une nouvelle archive avec un nouveau mot de passe.")
    elif hook == "download":
        advice = ("Rien n'a été envoyé au client. Après correction de la cause, relancer :\n"
                  f"  php tbscertbot.php test-hook download {ref}")
    else:
        advice = "Rien n'a été envoyé au client. Le challenge DCV n'a pas été enregistré."
    _alert_internal(mailer, cfg, f"Erreur inattendue du hook {hook} : {cn} (réf. {ref})",
                    f"{detail}\n\n{advice}\n\nDétails dans le journal de tbs-delivery.")


def _alert_internal(mailer: Mailer, cfg: dict, subject: str, body: str) -> None:
    company = cfg["internal"]["company"]
    try:
        mailer.send(mailer.build(to=[cfg["internal"]["team_email"]], cc=None,
                                 subject=f"[{company}][ALERTE] {subject}", body=body))
    except Exception:  # noqa: BLE001
        log.exception("impossible d'envoyer l'alerte interne")


# ------------------------------------------------------------------------------ dcv

def handle_dcv(cfg: dict) -> int:
    """Enregistre le challenge DCV ; crée l'enregistrement DNS si la zone est gérée par Athéo.

    L'envoi au client est regroupé par certificat par la commande `notify`
    (lancée juste après `tbscertbot.php cron`), pour éviter un mail par SAN.
    """
    try:
        return _handle_dcv(cfg)
    except Exception as exc:  # noqa: BLE001 - aucune erreur ne doit rester silencieuse
        _unexpected_error(cfg, "dcv", exc, record=False)
        return 0  # ne pas faire échouer TBSCertBot


def _handle_dcv(cfg: dict) -> int:
    ref = _env("PHP_TBS_REFERENCE")
    cn = _env("PHP_TBS_CN")
    method = _env("PHP_TBS_DCV_METHOD") or "http-token"
    root, sub, value = _env("PHP_TBS_DCV_DOMAIN_ROOT"), _env("PHP_TBS_DCV_DOMAIN_SUB"), _env("PHP_TBS_DCV_VALUE")
    registrar = _env("PHP_TBS_REGISTRAR")
    dcv_file = _env("PHP_TBS_DCV")
    log.info("hook dcv : ref=%s cn=%s méthode=%s registrar=%s", ref, cn, method, registrar)

    inv = Inventory.load(cfg["paths"]["inventory"])
    state = State(cfg["paths"]["state_db"])
    sans = [s.strip() for s in _env("PHP_TBS_SAN").split(",") if s.strip()]
    item = _locate(inv, state, ref, cn, sans)
    if item and ref and ref != item.ref:
        state.set_alias(ref, item.ref)
    client = item.client if item else "NON RÉFÉRENCÉ"
    inv_ref = item.ref if item else None

    if method in ("dns-cname-token", "dns-txt-token"):
        # PHP_TBS_DCV_DOMAIN_SUB peut être relatif (_abc.www) ou complet (_abc.www.domaine.fr)
        sub = sub.rstrip(".")
        rel = sub[: -len(root) - 1] if root and sub.lower().endswith("." + root.lower()) else sub
        fqdn = f"{rel}.{root}" if rel else root
        status, detail = "pending", ""
        gcfg = (cfg["dcv"].get("auto_dns") or {}).get("gandi") or {}
        zones = {z.lower() for z in gcfg.get("zones") or []}
        if gcfg.get("enabled") and registrar in (gcfg.get("registrars") or []) and root.lower() in zones:
            rtype = "TXT" if method == "dns-txt-token" else "CNAME"
            try:
                detail = upsert_record(zone=root, name=rel or "@", rtype=rtype, value=value,
                                       token_env=gcfg.get("token_env", "GANDI_TOKEN"))
                status = "auto"
                log.info("DCV %s créé automatiquement chez Gandi (%s)", fqdn, detail)
            except GandiError as exc:
                status, detail = "pending", f"Gandi : {exc}"
                log.error("DCV auto Gandi impossible pour %s : %s", fqdn, exc)
        state.dcv_seen(tbs_ref=ref, inventory_ref=inv_ref, client=client, cn=cn, method=method, name=fqdn,
                       value=value, registrar=registrar, status=status, detail=detail)
    else:
        # DCV HTTP : le fichier doit être publié sur le serveur web du client -> équipe interne
        url_hint = f"http://{cn.replace('*.', '')}/.well-known/pki-validation/{Path(dcv_file).name}" if dcv_file else ""
        state.dcv_seen(tbs_ref=ref, inventory_ref=inv_ref, client=client, cn=cn, method=method,
                       name=url_hint or cn, value=dcv_file, registrar=registrar, status="http", detail="")
    state.close()
    return 0


def notify_dcv(cfg: dict) -> int:
    """Envoie (ou relance) les demandes DCV en attente, regroupées par certificat."""
    inv = Inventory.load(cfg["paths"]["inventory"])
    state = State(cfg["paths"]["state_db"])
    mailer = Mailer(cfg)
    internal = cfg["internal"]["team_email"]
    company, signature = cfg["internal"]["company"], cfg["internal"]["signature"]
    reminder_days = int(cfg["dcv"].get("reminder_days", 3))
    max_rem = int(cfg["dcv"].get("max_reminders", 5))
    now = datetime.now(timezone.utc)
    stale = now - timedelta(days=7)

    groups: dict[str, list] = {}
    for row in state.dcv_open():
        groups.setdefault(row["tbs_ref"], []).append(row)

    sent = 0
    for ref, rows in groups.items():
        # challenge plus vu depuis 7 jours : TBSCertBot ne le réclame plus
        if all(datetime.fromisoformat(r["last_seen"]) < stale for r in rows):
            continue
        item = inv.by_ref(rows[0]["inventory_ref"]) if rows[0]["inventory_ref"] else None
        cn = rows[0]["cn"] or (item.cn if item else ref)
        client = rows[0]["client"]

        dns_rows = [r for r in rows if r["status"] in ("pending", "notified")]
        http_rows = [r for r in rows if r["status"] == "http" and not r["last_sent"]]

        if http_rows:
            r = http_rows[0]
            s, b = templates.dcv_http_internal(client=client, cn=cn, url_hint=r["record_name"],
                                               dcv_file=r["record_value"], signature=signature, company=company)
            mailer.send(mailer.build(to=[internal], cc=None, subject=s, body=b))
            state.dcv_mark_sent([x["id"] for x in http_rows], status="http")
            sent += 1

        if not dns_rows:
            continue
        never_sent = any(r["sent_count"] == 0 for r in dns_rows)
        last = max((datetime.fromisoformat(r["last_sent"]) for r in dns_rows if r["last_sent"]), default=None)
        count = max(r["sent_count"] for r in dns_rows)
        due = never_sent or (last and now - last >= timedelta(days=reminder_days) and count <= max_rem)
        if not due:
            continue

        spec = {**cfg["dcv"], **((item.dcv or {}) if item else {})}
        admin = item.admin_email if item else ""
        to = resolve_recipients(spec.get("notify", []), client_admin=admin, internal=internal) or [internal]
        cc = resolve_recipients(spec.get("cc", []), client_admin=admin, internal=internal)
        records = [(r["record_name"], r["record_value"]) for r in dns_rows]
        s, b = templates.dcv_request(client=client, cn=cn, method=dns_rows[0]["method"], records=records,
                                     reminder=0 if never_sent else count, signature=signature, company=company)
        try:
            mailer.send(mailer.build(to=to, cc=cc, subject=s, body=b))
            state.dcv_mark_sent([r["id"] for r in dns_rows])
            sent += 1
            log.info("demande DCV envoyée pour %s (%d enregistrement(s)) -> %s", cn, len(records), ", ".join(to))
        except Exception:  # noqa: BLE001
            log.exception("envoi de la demande DCV impossible pour %s", cn)
    state.close()
    return sent
