"""Chargement de la configuration (YAML) avec valeurs par défaut."""

from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

DEFAULTS: dict = {
    # dry-run : rien n'est envoyé, les mails sont écrits en .eml dans paths.outbox
    "mode": "dry-run",
    "paths": {
        "inventory": "/var/lib/tbs-delivery/inventory.yaml",
        "state_db": "/var/lib/tbs-delivery/state.db",
        "deliveries": "/var/lib/tbs-delivery/deliveries",
        "outbox": "/var/lib/tbs-delivery/outbox",
        "log": "/var/log/tbs-delivery/tbs-delivery.log",
    },
    "internal": {
        "team_email": "certificats@atheo.net",
        "sender": "Athéo Ingénierie - Certificats <certificats@atheo.net>",
        "company": "Athéo Ingénierie",
        "signature": "L'équipe Certificats\nAthéo Ingénierie",
    },
    "smtp": {
        "host": "localhost",
        "port": 25,
        "starttls": False,
        "ssl": False,
        "username": "",
        "password_env": "TBS_DELIVERY_SMTP_PASSWORD",
        "timeout": 30,
    },
    "delivery": {
        # destinataires : client_admin (contact admin. TBS), internal, ou adresses explicites
        "recipients": ["client_admin"],
        "cc": ["internal"],
        # separate_email : mot de passe envoyé dans un 2e mail aux mêmes destinataires
        # internal_only  : mot de passe envoyé uniquement à l'équipe interne (transmis par téléphone/SMS)
        "password_channel": "separate_email",
        "zip_encrypt": True,
        "pfx": True,
        # PFX 3DES/SHA1 pour vieux Windows / appliances (AES-256 par défaut)
        "pfx_legacy": False,
        "attach_max_mb": 15,
    },
    "dcv": {
        "notify": ["client_admin"],
        "cc": ["internal"],
        "reminder_days": 3,
        "max_reminders": 5,
        # automatisation DNS quand le domaine est chez un registrar maîtrisé par Athéo
        "auto_dns": {
            "gandi": {
                "enabled": False,
                "token_env": "GANDI_TOKEN",
                "registrars": ["GANDI SAS"],
                # n'automatiser que les zones explicitement listées (sécurité)
                "zones": [],
            }
        },
    },
    "policy": {
        "reissue_lead_days": 30,
        "renewal_alert_days": 60,
        "expiry_alert_days": 21,
        # marge (jours) : forfait considéré terminé si fin forfait <= expiration + marge
        "forfait_end_margin_days": 15,
    },
    "tbscertbot": {
        "path": "/opt/tbscertbot",
        "php": "/usr/bin/php",
        "dcv_method_default": "CNAME_CSR_HASH",
        # méthode DCV par marque (DigiCert/GeoTrust/Thawte/RapidSSL acceptent le TXT)
        "dcv_method_by_brand": {
            "digicert": "DNSTXT_CSR_HASH",
            "geotrust": "DNSTXT_CSR_HASH",
            "thawte": "DNSTXT_CSR_HASH",
            "rapidssl": "DNSTXT_CSR_HASH",
        },
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def default_config_path() -> Path:
    env = os.environ.get("TBS_DELIVERY_CONFIG")
    if env:
        return Path(env)
    for candidate in (Path("/etc/tbs-delivery/config.yaml"), Path("config.yaml")):
        if candidate.exists():
            return candidate
    return Path("/etc/tbs-delivery/config.yaml")


def load_config(path: str | os.PathLike | None = None) -> dict:
    cfg_path = Path(path) if path else default_config_path()
    data: dict = {}
    if cfg_path.exists():
        with open(cfg_path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    cfg = _merge(DEFAULTS, data)
    cfg["_path"] = str(cfg_path)
    # chemins relatifs = relatifs au fichier de configuration
    base = cfg_path.parent if cfg_path.exists() else Path.cwd()
    for key, value in cfg["paths"].items():
        p = Path(value)
        if not p.is_absolute():
            cfg["paths"][key] = str((base / p).resolve())
    return cfg


def is_dry_run(cfg: dict) -> bool:
    return str(cfg.get("mode", "dry-run")).lower() != "live"
