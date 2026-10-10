"""Envoi des mails (SMTP) ou écriture en .eml dans la boîte d'envoi en mode dry-run."""

from __future__ import annotations

import mimetypes
import os
import re
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

from .config import is_dry_run


def resolve_recipients(spec: list, *, client_admin: str = "", internal: str = "") -> list[str]:
    """Transforme ['client_admin', 'internal', 'x@y.fr'] en liste d'adresses dédoublonnée."""
    out: list[str] = []
    for item in spec or []:
        item = str(item).strip()
        if item == "client_admin":
            addr = client_admin
        elif item == "internal":
            addr = internal
        else:
            addr = item
        for a in re.split(r"[;,\s]+", addr or ""):
            if a and "@" in a and a.lower() not in {x.lower() for x in out}:
                out.append(a)
    return out


class Mailer:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.smtp = cfg["smtp"]
        self.sender = cfg["internal"]["sender"]
        self.dry_run = is_dry_run(cfg)
        self.outbox = Path(cfg["paths"]["outbox"])

    def build(self, *, to: list, cc: list | None, subject: str, body: str,
              attachments: list | None = None) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = self.sender
        msg["To"] = ", ".join(to)
        if cc:
            cc = [c for c in cc if c.lower() not in {t.lower() for t in to}]
            if cc:
                msg["Cc"] = ", ".join(cc)
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=self.sender.split("@")[-1].rstrip(">") or None)
        msg["X-Mailer"] = "tbs-delivery"
        msg.set_content(body)
        for path in attachments or []:
            p = Path(path)
            ctype, _ = mimetypes.guess_type(p.name)
            maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
            msg.add_attachment(p.read_bytes(), maintype=maintype, subtype=subtype, filename=p.name)
        return msg

    def send(self, msg: EmailMessage) -> str:
        """Envoie le message. Retourne 'sent' ou le chemin du .eml en dry-run."""
        if self.dry_run:
            self.outbox.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", msg["Subject"])[:80]
            path = self.outbox / f"{datetime.now():%Y%m%d-%H%M%S-%f}_{safe}.eml"
            path.write_bytes(bytes(msg))
            path.chmod(0o600)
            return str(path)

        s = self.smtp
        password = os.environ.get(s.get("password_env") or "", "")
        timeout = int(s.get("timeout", 30))
        if s.get("ssl"):
            server = smtplib.SMTP_SSL(s["host"], int(s.get("port", 465)), timeout=timeout,
                                      context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(s["host"], int(s.get("port", 25)), timeout=timeout)
        try:
            if s.get("starttls") and not s.get("ssl"):
                server.starttls(context=ssl.create_default_context())
            if s.get("username"):
                server.login(s["username"], password)
            server.send_message(msg)
        finally:
            try:
                server.quit()
            except (smtplib.SMTPException, OSError):
                pass  # le message est déjà accepté : une coupure pendant QUIT n'est pas un échec d'envoi
        return "sent"
