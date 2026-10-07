# CLAUDE.md — tbs-delivery

> Au début de chaque session : relire `tasks/lessons.md` et `tasks/todo.md`.

## Projet

**But** : livrer automatiquement aux clients les certificats SSL/TLS revendus par Athéo
Ingénierie via TBS CERTIFICATS, refabriqués par TBSCertBot.

**Pourquoi** : la validité passe à 200 jours, puis 100 jours en mars 2027, et la
réutilisation de la validation de domaine (DCV) tombe à 10 jours en 2029. La
refabrication et la livraison manuelles ne tiennent plus.

**Fonctionnement**
- TBSCertBot (PHP, v0.9.7, installé à part dans `/opt/tbscertbot`) refabrique les
  certificats sous forfait (`reissue auto on`), les télécharge (mode cron) et appelle
  les hooks `download` et `dcv` avec des variables `PHP_TBS_*`.
- tbs-delivery retrouve le client (réf. TBS, puis CN, puis SAN : chaque refabrication
  crée une nouvelle réf.), fabrique un ZIP chiffré AES-256 (PEM, fullchain, clé, PFX,
  LISEZMOI) et l'envoie au contact admin. Le mot de passe part dans un second mail et
  n'est **jamais stocké**.
- DNS surtout chez les clients : mail DCV groupé par certificat, relance tous les
  3 jours, 5 au maximum. Les zones Gandi d'Athéo peuvent être traitées automatiquement.
- Garde-fous : une seule livraison par numéro de série ; aucun envoi au client si la
  clé ne correspond pas au certificat, si le certificat est expiré ou inconnu (alerte
  interne) ; aucun rachat automatique (`renewal disable`).
- Périmètre v1 : livrer les fichiers. Déployer sur les équipements est hors périmètre.
- État : jamais testé contre l'API TBS réelle, seulement en simulation. Prochaine
  étape : pilote en dry-run sur 2 certificats proches de l'échéance. Les points à
  valider avec TBS sont dans le README.

**Stack** : Python 3 (cryptography, PyYAML, pyzipper), hooks shell, units systemd,
SQLite pour l'état, TBSCertBot (PHP) à part.

**Structure** : `tbsdelivery/` (CLI `__main__.py`, `hooks.py`, `package.py`, `mailer.py`,
`inventory.py`, `state.py`, `report.py`, `bootstrap.py`, `dns_gandi.py`, `templates.py`,
`config.py`), `hooks/` (wrappers shell, toujours `exit 0`), `deploy/` (units systemd),
`tests/test_e2e.py` (5 tests de bout en bout, PKI de test via openssl).

**Commandes** (vérifiées dans le code)
```bash
# Lancer (dry-run par défaut : les mails sont écrits en .eml dans paths.outbox)
python3 -m tbsdelivery -c config.yaml status
# ou : TBS_DELIVERY_CONFIG=config.yaml python3 -m tbsdelivery status
# sans -c ni variable : /etc/tbs-delivery/config.yaml, sinon ./config.yaml
# commandes : import-csv, report, bootstrap, conf-snippet, hook download|dcv [--force],
#             notify, digest [--print], status
# chemins relatifs de la config = relatifs au fichier de config ;
# en prod : /var/lib/tbs-delivery (état, ZIP, outbox), /var/log/tbs-delivery

# Tester (requiert le binaire openssl et requirements.txt)
python3 -m unittest discover -s tests -v
```

## Méthode

- Toute tâche de **3 étapes ou plus** : écrire d'abord le plan dans `tasks/todo.md`,
  puis **attendre ma validation** avant de coder.
- Si ça déraille : s'arrêter et replanifier, ne pas insister.
- Changements minimaux. Corriger la cause racine, jamais de correctif temporaire.

## Vérification

- Je ne relis pas le code : **aucune tâche n'est terminée sans test écrit et passé**.
- Montrer la sortie des tests.
- Résumer chaque changement en français simple : **quoi, pourquoi, risque**.

## Apprentissage

- Après chaque correction de ma part, ajouter la règle dans `tasks/lessons.md`.
- Relire ce fichier en début de session.

## Advisor

Consulter l'outil advisor (disponibilité à confirmer dans ta version de Claude Code) :
- avant une implémentation complexe ;
- après deux échecs sur la même erreur ;
- avant de déclarer une tâche terminée.

## Relecture

Avant tout commit important : faire relire le diff par le sous-agent `relecteur`
(`.claude/agents/relecteur.md`) et me donner son verdict.

Autres sous-agents : `explorateur` (cartographie du code, lecture seule) et
`documentaliste` (doc officielle TBS, Gandi LiveDNS, cryptography, pyzipper).

## Interdits

- Aucun secret ni donnée client dans le code, les tests ou les commits. Les tests
  utilisent des données fictives (ex. `client-a.fr`). Ne pas ouvrir `parc-actuel/`,
  `inventory.yaml`, `outbox/`, `deliveries/`, `state.db`, les journaux, les clés ni les
  exports CSV TBS.
- Jamais de push sur `main` sans mon accord.
- Jamais de passage en mode live.
- Aucun appel réel à l'API TBS, au SMTP ou à Gandi sans mon accord.

## Langue

Me répondre en français, de façon concise, avec des commandes prêtes à copier.
