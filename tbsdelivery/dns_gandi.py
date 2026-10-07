"""Création d'enregistrements DCV via l'API Gandi LiveDNS (zones gérées par Athéo)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

API = "https://api.gandi.net/v5/livedns"


class GandiError(Exception):
    pass


def upsert_record(*, zone: str, name: str, rtype: str, value: str, token: str | None = None,
                  token_env: str = "GANDI_TOKEN", ttl: int = 300) -> str:
    """Crée ou remplace l'enregistrement (PUT = idempotent, CREATE comme UPDATE)."""
    token = token or os.environ.get(token_env, "")
    if not token:
        raise GandiError(f"Jeton Gandi absent (variable {token_env})")
    if rtype == "CNAME" and not value.endswith("."):
        value += "."
    if rtype == "TXT" and not value.startswith('"'):
        value = f'"{value}"'
    url = f"{API}/domains/{zone}/records/{name}/{rtype}"
    payload = json.dumps({"rrset_values": [value], "rrset_ttl": ttl}).encode()
    req = urllib.request.Request(url, data=payload, method="PUT", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        raise GandiError(f"Gandi {exc.code} : {exc.read().decode(errors='replace')[:300]}") from exc
    except urllib.error.URLError as exc:
        raise GandiError(f"Gandi injoignable : {exc.reason}") from exc
