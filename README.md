# tbs-delivery

Refabrication et livraison automatisées des certificats SSL/TLS vendus via **TBS CERTIFICATS**,
construites autour de **TBSCertBot** (v0.9.7).

- **TBSCertBot** refabrique les certificats sous forfait (`reissue <ref> auto on` + `emergencyReissue`),
  les télécharge (`cron`) et appelle des *hooks*.
- **tbs-delivery** est appelé par ces hooks : il retrouve le client, fabrique un livrable
  (PEM, fullchain, clé, PFX dans un ZIP chiffré AES-256) et l'envoie par mail, mot de passe à part.
  Il envoie aussi aux clients les enregistrements DNS de validation (DCV) à créer, avec relances.

Périmètre v1 : **livrer les fichiers**. L'installation sur les équipements des clients n'est pas traitée.

```
                       hub Linux Athéo (VM)
 ┌──────────────────────────────────────────────────────────────────────┐
 │ timer systemd 2x/jour                                                │
 │   └─ php tbscertbot.php cron ──► API TBS (refab. forfait, download)  │
 │         ├─ hook dcv      ─► tbsdelivery hook dcv   (enregistre)      │
 │         └─ hook download ─► tbsdelivery hook download                │
 │                               ├─ inventory.yaml : réf/CN → client    │
 │                               ├─ ZIP AES-256 (crt, chain, fullchain, │
 │                               │   key, pfx, LISEZMOI)                │
 │                               └─ mail client (+ cc interne),         │
 │                                  mot de passe dans un 2e mail        │
 │   └─ tbsdelivery notify ─► 1 mail DCV groupé par certificat + relances│
 │ timer lundi : tbsdelivery digest ─► synthèse équipe interne          │
 └──────────────────────────────────────────────────────────────────────┘
```

## Ce que fait chaque commande

| Commande | Rôle |
|---|---|
| `import-csv export.csv` | Crée/met à jour `inventory.yaml` depuis l'export « liste des certificats » du Certificate Center. Classe chaque ligne : refabrication sous forfait, fin de forfait (rachat), remplacée, hors périmètre (eIDAS, authentification…). Les surcharges manuelles sont conservées. |
| `report [--csv plan.csv]` | État du parc et plan d'action trié par échéance. |
| `bootstrap --out s.sh` | Script de mise sous gestion TBSCertBot (`import`, `set-profile … request.dcv`, `renewal disable`, `reissue … auto on`). |
| `conf-snippet` | Extrait `data/conf.ini` à appliquer dans TBSCertBot (hooks, `emergencyReissue`, `reuse-keys`, logs). |
| `hook download` / `hook dcv` | Appelés par TBSCertBot via `hooks/tbs-download.sh` et `hooks/tbs-dcv.sh`. |
| `notify` | Envoie/relance les demandes DCV en attente (un mail par certificat, tous SAN regroupés). |
| `digest [--print]` | Synthèse interne : DCV en attente, rachats à proposer, certificats proches de l'expiration sans relivraison, échecs. |
| `status` | Dernières livraisons et DCV ouvertes. |

## Comportements importants

- **Nouvelle référence à chaque refabrication** : TBS attribue une nouvelle référence. Le rapprochement
  se fait par référence, puis par CN, puis par SAN ; l'alias est mémorisé.
- **Une seule livraison par certificat** (numéro de série) : le cron peut repasser sans renvoyer.
  La livraison est réservée en base *avant* l'envoi (verrou SQLite), ce qui protège aussi contre
  deux exécutions simultanées. En cas de doute, rien n'est relivré automatiquement :
  - envoi **partiel** (archive partie, mot de passe non) : alerte interne immédiate ;
  - envoi **interrompu** (plantage pendant l'envoi) : signalé par `notify` après une heure ;
  - les deux figurent dans la synthèse hebdomadaire (« Livraisons à vérifier »).

  Relivrer volontairement, après vérification : `TBS_DELIVERY_FORCE=1 php tbscertbot.php test-hook download <ref>`
  (nouveau ZIP, nouveau mot de passe).
- **Mot de passe jamais stocké** : il n'existe que dans le mail. Option `password_channel: internal_only`
  pour que l'équipe le transmette par téléphone/SMS.
- **Contrôles** avant envoi : la clé correspond au certificat, le certificat n'est pas expiré.
  Sinon : alerte interne, rien n'est envoyé au client.
- **Certificat inconnu de l'inventaire** : livré uniquement à l'équipe interne.
- **Pas de rachat automatique** : le bootstrap désactive le renouvellement automatique
  (`renewal disable`) ; un renouvellement hors forfait est une décision commerciale, remontée dans la synthèse.
- **DCV DNS chez le client** : le hook enregistre chaque challenge ; `notify` envoie au contact admin.
  un mail unique avec tous les enregistrements, puis relance tous les 3 jours (5 fois max).
  Pour les zones hébergées par Athéo chez Gandi, création automatique (`dcv.auto_dns.gandi`, zones listées).
- **Mode `dry-run`** par défaut : les mails sont écrits en `.eml` dans `outbox/`. Passer en `live` après validation.

## Installation (hub Debian/Ubuntu)

```sh
sudo ./install.sh                         # venv, utilisateur tbscertbot, units systemd
sudoedit /etc/tbs-delivery/config.yaml    # SMTP, adresses, mode
sudoedit /etc/tbs-delivery/env            # TBS_DELIVERY_SMTP_PASSWORD, GANDI_TOKEN
```

TBSCertBot dans `/opt/tbscertbot` (archive TBS), configuré avec un utilisateur API dédié
(`php tbscertbot.php` au premier lancement), propriété de l'utilisateur `tbscertbot`.

```sh
cd /opt/tbs-delivery && sudo -u tbscertbot venv/bin/python -m tbsdelivery import-csv export_tbs.csv
sudo -u tbscertbot venv/bin/python -m tbsdelivery report --csv /tmp/plan.csv
sudo -u tbscertbot venv/bin/python -m tbsdelivery conf-snippet   # à reporter dans /opt/tbscertbot/data/conf.ini
sudo -u tbscertbot venv/bin/python -m tbsdelivery bootstrap --out /tmp/bootstrap.sh
```

## Mise en service conseillée

1. **Pilote sur 2 certificats** dont l'échéance est proche, en `dry-run`, destinataire forcé à l'équipe
   interne dans `inventory.yaml` (`delivery: {recipients: [internal], cc: []}`) :
   exécuter les lignes du bootstrap correspondantes, puis `php tbscertbot.php reissue <ref> now`,
   puis `php tbscertbot.php cron` et `tbsdelivery notify`. Vérifier les `.eml` produits.
2. **Valider avec TBS** les points ci-dessous.
3. Importer le reste du parc, passer en `live`, activer les timers :
   `systemctl enable --now tbs-delivery-cycle.timer tbs-delivery-digest.timer`.
4. À chaque nouvelle vente : ré-exporter le CSV et relancer `import-csv` (les surcharges sont conservées),
   puis `bootstrap` pour les nouvelles références.

## Points à valider avec TBS / sur le pilote

1. `reissue <ref> auto on` + `emergencyReissue` déclenchent bien la refabrication en mode `cron` et les hooks.
2. **Stabilité du CNAME de DCV** : avec `reuse-keys = 1`, le CSR est réutilisé ; si Sectigo garde le même
   enregistrement d'une refabrication à l'autre, le client ne crée le CNAME qu'une fois. À vérifier.
3. **Réutilisation de la validation de domaine** : une DCV reste réutilisable 200 jours (100 jours à partir
   de mars 2027, 10 jours en 2029). Tant qu'elle est valide, la refabrication ne devrait pas en redemander.
4. `renewal disable` n'empêche pas la refabrication sous forfait.
5. Support de la refabrication **GlobalSign** via TBSCertBot.
6. `import` d'un certificat dont la clé privée est restée chez le client : première refabrication avec
   `--no-reuse-keys` (nouvelle clé générée sur le hub).
7. Pour les clients sans compétence DNS : DCV par mail (`set-profile <ref> request.dcv admin@domaine`),
   le client n'a qu'à cliquer le lien de l'AC.

## Sécurité

- Clés privées : `/opt/tbscertbot/data/keys` (700) et archives `/var/lib/tbs-delivery` (700), utilisateur dédié.
- Le ZIP est chiffré AES-256 (7-Zip/WinRAR/bsdtar pour l'ouvrir) ; le PFX utilise le même mot de passe
  (AES-256 par défaut, `pfx_legacy: true` pour Windows Server 2012/2016 et anciennes appliances).
- Secrets uniquement dans `/etc/tbs-delivery/env` (600).

## Tests

```sh
python3 -m unittest discover -s tests -v
```
Simule TBSCertBot (PKI de test + variables `PHP_TBS_*`) : refabrication avec nouvelle référence,
idempotence, ouverture du ZIP et du PFX, DCV groupée puis clôturée, clé incohérente, certificat inconnu.
