"""Fabrication du paquet livrable à partir des fichiers produits par TBSCertBot.

Contenu du ZIP (chiffré AES-256) :
  <nom>.crt            certificat seul (PEM)
  <nom>.chain.pem      chaîne intermédiaire (PEM)
  <nom>.fullchain.pem  certificat + chaîne (Apache, Nginx, HAProxy, reverse proxies)
  <nom>.key            clé privée (PEM)
  <nom>.pfx            PKCS#12 certificat + clé + chaîne (IIS, Exchange, Windows, appliances)
  LISEZMOI.txt         caractéristiques du certificat et usage de chaque fichier
"""

from __future__ import annotations

import hashlib
import secrets
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtensionOID, NameOID

from .inventory import safe_name

PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"


class PackageError(Exception):
    pass


def generate_password(groups: int = 4, size: int = 5) -> str:
    return "-".join("".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(size)) for _ in range(groups))


def _load_certs(path: Path) -> list[x509.Certificate]:
    data = path.read_bytes()
    if b"-----BEGIN CERTIFICATE-----" in data:
        return x509.load_pem_x509_certificates(data)
    try:
        return [x509.load_der_x509_certificate(data)]
    except ValueError as exc:
        raise PackageError(f"Format de certificat non reconnu : {path}") from exc


def peek_serial(cert_path: str) -> str | None:
    """Numéro de série du certificat (pour savoir s'il a déjà été livré), sans rien fabriquer."""
    try:
        return format(_load_certs(Path(cert_path))[0].serial_number, "X")
    except (OSError, PackageError, ValueError):
        return None


def _load_key(path: Path):
    data = path.read_bytes()
    try:
        if b"-----BEGIN" in data:
            return serialization.load_pem_private_key(data, password=None)
        return serialization.load_der_private_key(data, password=None)
    except (ValueError, TypeError) as exc:
        raise PackageError(f"Clé privée illisible ou chiffrée : {path} ({exc})") from exc


def _pub_bytes(key) -> bytes:
    return key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)


def _cn(name: x509.Name) -> str:
    attrs = name.get_attributes_for_oid(NameOID.COMMON_NAME)
    return attrs[0].value if attrs else name.rfc4514_string()


@dataclass
class CertInfo:
    cn: str
    sans: list
    serial: str
    not_before: datetime
    not_after: datetime
    issuer: str
    sha256: str
    key_type: str

    @property
    def days_left(self) -> int:
        return (self.not_after - datetime.now(timezone.utc)).days


def describe(cert: x509.Certificate, key) -> CertInfo:
    try:
        sans = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value.get_values_for_type(
            x509.DNSName)
    except x509.ExtensionNotFound:
        sans = []
    key_type = type(key).__name__.replace("_", "")
    size = getattr(key, "key_size", None)
    if "RSA" in key_type.upper():
        key_type = f"RSA {size}"
    elif "ELLIPTIC" in key_type.upper() or "EC" in key_type.upper():
        key_type = f"ECDSA {getattr(key.curve, 'name', '')}"
    return CertInfo(
        cn=_cn(cert.subject),
        sans=list(sans),
        serial=format(cert.serial_number, "X"),
        not_before=cert.not_valid_before_utc,
        not_after=cert.not_valid_after_utc,
        issuer=_cn(cert.issuer),
        sha256=cert.fingerprint(hashes.SHA256()).hex(":").upper(),
        key_type=key_type,
    )


@dataclass
class Package:
    zip_path: Path
    password: str
    info: CertInfo
    files: list
    zip_encrypted: bool


def _readme(info: CertInfo, base: str, client: str, company: str, pfx: bool) -> str:
    sans = ", ".join(info.sans) if info.sans else "-"
    lines = [
        f"Certificat SSL/TLS livré par {company}",
        "=" * 60,
        f"Client            : {client}",
        f"Nom commun (CN)   : {info.cn}",
        f"Noms (SAN)        : {sans}",
        f"Valide du         : {info.not_before:%d/%m/%Y %H:%M} UTC",
        f"Valide jusqu'au   : {info.not_after:%d/%m/%Y %H:%M} UTC",
        f"Numéro de série   : {info.serial}",
        f"Émetteur          : {info.issuer}",
        f"Empreinte SHA-256 : {info.sha256}",
        f"Clé               : {info.key_type}",
        "",
        "Fichiers",
        "--------",
        f"{base}.crt            certificat seul (PEM)",
        f"{base}.chain.pem      certificats intermédiaires (PEM)",
        f"{base}.fullchain.pem  certificat + intermédiaires : Apache (SSLCertificateFile),",
        "                        Nginx (ssl_certificate), HAProxy (avec la clé), reverse proxies",
        f"{base}.key            clé privée (PEM, non chiffrée) - à protéger, ne jamais diffuser",
    ]
    if pfx:
        lines += [
            f"{base}.pfx            PKCS#12 (certificat + clé + chaîne) : IIS, Exchange, ADFS, RDS,",
            "                        magasin Windows, FortiGate, appliances. Mot de passe = celui du ZIP.",
        ]
    lines += [
        "",
        "Rappels",
        "-------",
        "- Les durées de vie des certificats sont désormais courtes (200 jours, puis 100 jours",
        "  à partir de mars 2027) : ce certificat sera refabriqué et relivré automatiquement",
        "  avant son expiration.",
        "- Après installation, pensez à redémarrer / recharger le service concerné.",
        "- Supprimez ce ZIP et les fichiers extraits une fois le certificat installé.",
    ]
    return "\n".join(lines) + "\n"


def build_package(
    *,
    key_path: str,
    cert_path: str,
    chain_path: str | None,
    out_dir: str | Path,
    client: str,
    company: str = "Athéo Ingénierie",
    pfx: bool = True,
    pfx_legacy: bool = False,
    zip_encrypt: bool = True,
    password: str | None = None,
) -> Package:
    key_p, cert_p = Path(key_path or ""), Path(cert_path or "")
    if not key_p.is_file():
        raise PackageError(f"Clé privée introuvable : {key_path}")
    if not cert_p.is_file():
        raise PackageError(f"Certificat introuvable : {cert_path}")

    certs = _load_certs(cert_p)
    leaf, extra = certs[0], certs[1:]
    chain = list(extra)
    if chain_path and Path(chain_path).is_file():
        for c in _load_certs(Path(chain_path)):
            if c.fingerprint(hashes.SHA256()) != leaf.fingerprint(hashes.SHA256()) and c not in chain:
                chain.append(c)
    key = _load_key(key_p)

    if _pub_bytes(leaf.public_key()) != _pub_bytes(key.public_key()):
        raise PackageError("La clé privée ne correspond pas au certificat (refabrication avec nouvelle clé ?)")
    info = describe(leaf, key)
    if info.days_left < 0:
        raise PackageError(f"Certificat déjà expiré ({info.not_after:%d/%m/%Y})")
    # chaîne ordonnée : on ne garde pas les racines auto-signées
    chain = [c for c in chain if c.issuer != c.subject]

    password = password or generate_password()
    base = safe_name(info.cn)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest = Path(out_dir)
    dest.mkdir(parents=True, exist_ok=True)

    pem = serialization.Encoding.PEM
    with tempfile.TemporaryDirectory(prefix="tbsdeliv-") as tmp:
        t = Path(tmp)
        files = {
            f"{base}.crt": leaf.public_bytes(pem),
            f"{base}.chain.pem": b"".join(c.public_bytes(pem) for c in chain),
            f"{base}.fullchain.pem": leaf.public_bytes(pem) + b"".join(c.public_bytes(pem) for c in chain),
            f"{base}.key": key.private_bytes(pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()),
        }
        if pfx:
            if pfx_legacy:
                enc = (serialization.PrivateFormat.PKCS12.encryption_builder()
                       .kdf_rounds(2048)
                       .key_cert_algorithm(pkcs12.PBES.PBESv1SHA1And3KeyTripleDESCBC)
                       .hmac_hash(hashes.SHA1())
                       .build(password.encode()))
            else:
                enc = serialization.BestAvailableEncryption(password.encode())
            files[f"{base}.pfx"] = pkcs12.serialize_key_and_certificates(
                name=info.cn.encode(), key=key, cert=leaf, cas=chain or None, encryption_algorithm=enc)
        files["LISEZMOI.txt"] = _readme(info, base, client, company, pfx).encode("utf-8")
        for name, data in files.items():
            (t / name).write_bytes(data)

        # suffixe aléatoire : deux exécutions dans la même seconde n'écrivent pas la même archive
        zip_path = dest / f"{base}_{stamp}_{info.serial[-8:]}_{secrets.token_hex(3)}.zip"
        encrypted = False
        if zip_encrypt:
            try:
                import pyzipper  # type: ignore
            except ImportError as exc:
                raise PackageError("Le module pyzipper est requis pour chiffrer le ZIP (pip install pyzipper)") from exc
            with pyzipper.AESZipFile(zip_path, "w", compression=pyzipper.ZIP_DEFLATED,
                                     encryption=pyzipper.WZ_AES) as zf:
                zf.setpassword(password.encode())
                zf.setencryption(pyzipper.WZ_AES, nbits=256)
                for name in files:
                    zf.write(t / name, arcname=f"{base}/{name}")
            encrypted = True
        else:
            # sans chiffrement du ZIP, la clé PEM n'est pas livrée en clair : seul le PFX la contient
            import zipfile
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for name in files:
                    if name.endswith(".key"):
                        continue
                    zf.write(t / name, arcname=f"{base}/{name}")
        zip_path.chmod(0o600)
        shutil.rmtree(t, ignore_errors=True)

    return Package(zip_path=zip_path, password=password, info=info, files=list(files), zip_encrypted=encrypted)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
