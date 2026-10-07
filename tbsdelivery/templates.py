"""Textes des mails (français)."""

from __future__ import annotations


def _fmt(d) -> str:
    return d.strftime("%d/%m/%Y") if d else "-"


def delivery(*, client: str, info, zip_name: str, password_channel: str, attached: bool,
             signature: str, company: str) -> tuple[str, str]:
    subject = f"[{company}] Certificat {info.cn} renouvelé - valable jusqu'au {_fmt(info.not_after)}"
    sans = ", ".join(info.sans) if info.sans else info.cn
    if password_channel == "internal_only":
        pwd = "Le mot de passe de l'archive vous sera communiqué séparément par votre interlocuteur Athéo."
    else:
        pwd = "Le mot de passe de l'archive vous est envoyé dans un second message séparé."
    where = ("Vous trouverez en pièce jointe l'archive" if attached
             else "L'archive est trop volumineuse pour être jointe ; votre interlocuteur Athéo vous la transmettra :")
    body = f"""Bonjour,

Le certificat SSL/TLS ci-dessous vient d'être émis :

  Client            : {client}
  Nom commun (CN)   : {info.cn}
  Noms couverts     : {sans}
  Valide jusqu'au   : {_fmt(info.not_after)}
  Numéro de série   : {info.serial}

{where} {zip_name}, chiffrée (AES-256 ; à ouvrir avec 7-Zip
ou WinRAR sous Windows, 7z ou bsdtar sous Linux : l'explorateur Windows et la commande
unzip ne gèrent pas toujours ce chiffrement).
Elle contient le certificat aux formats PEM et PFX, la chaîne de certification et la
clé privée, ainsi qu'un fichier LISEZMOI.txt indiquant le fichier à utiliser selon
l'équipement (IIS/Exchange : .pfx ; Apache/Nginx : .fullchain.pem + .key).

{pwd}

Merci d'installer ce nouveau certificat dès que possible, en remplacement du précédent
(qui expire bientôt), puis de redémarrer ou recharger les services concernés.

Rappel : la durée de vie maximale des certificats est désormais de 200 jours et passera
à 100 jours en mars 2027. Les refabrications seront donc plus fréquentes ; elles vous
seront livrées automatiquement de la même façon.

{signature}
"""
    return subject, body


def password(*, info, zip_name: str, password_value: str, signature: str, company: str) -> tuple[str, str]:
    subject = f"[{company}] Mot de passe - certificat {info.cn}"
    body = f"""Bonjour,

Voici le mot de passe de l'archive {zip_name} (certificat {info.cn}) envoyée séparément :

    {password_value}

Ce mot de passe protège également le fichier .pfx contenu dans l'archive.

{signature}
"""
    return subject, body


def password_internal(*, client: str, info, zip_name: str, password_value: str, recipients: list,
                      signature: str, company: str) -> tuple[str, str]:
    subject = f"[{company}][INTERNE] Mot de passe à transmettre - {client} - {info.cn}"
    body = f"""Livraison effectuée à : {", ".join(recipients)}

Archive    : {zip_name}
Mot de passe (ZIP et PFX) : {password_value}

Merci de transmettre ce mot de passe au client par un canal distinct du mail
(téléphone, SMS). Il n'est conservé nulle part ailleurs.

{signature}
"""
    return subject, body


def dcv_request(*, client: str, cn: str, method: str, records: list, reminder: int,
                signature: str, company: str) -> tuple[str, str]:
    tag = f"Relance {reminder} - " if reminder else ""
    subject = f"[{company}] {tag}Action requise : validation DNS du certificat {cn}"
    if method == "dns-txt-token":
        rtype = "TXT"
    else:
        rtype = "CNAME"
    rows = "\n".join(f"  Type : {rtype}\n  Nom  : {name}\n  Valeur : {value}\n" for name, value in records)
    body = f"""Bonjour,

Le certificat {cn} ({client}) doit être refabriqué. Pour que l'autorité de
certification valide le contrôle du domaine, merci de créer l'enregistrement DNS
suivant dans la zone de votre domaine :

{rows}
Notes :
- pour un CNAME, la valeur se termine par un point si votre interface l'exige ;
- l'enregistrement peut être conservé après la validation : si la clé du certificat
  est réutilisée, il pourra resservir lors des prochaines refabrications ;
- dès que la validation est effectuée, le certificat est émis et vous est livré
  automatiquement.

Sans action de votre part, le certificat actuel expirera et ne pourra pas être remplacé.

{signature}
"""
    return subject, body


def dcv_http_internal(*, client: str, cn: str, url_hint: str, dcv_file: str, signature: str,
                      company: str) -> tuple[str, str]:
    subject = f"[{company}][INTERNE] DCV HTTP à déposer - {client} - {cn}"
    body = f"""Le certificat {cn} ({client}) attend une validation DCV par fichier HTTP.

Fichier généré par TBSCertBot : {dcv_file}
À publier sous : {url_hint}

Alternative : passer ce profil en DCV DNS (CNAME) :
  php tbscertbot.php set-profile <refTBS> request.dcv CNAME_CSR_HASH

{signature}
"""
    return subject, body
