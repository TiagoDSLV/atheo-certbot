# Rapport de mise en place Claude Code — 2026-10-07 (revalidé sur le vrai code)

Historique : une première version a été écrite sur un dépôt vide. Ce rapport la
remplace après extraction du code fourni (`tbs-delivery.zip`), selon le plan de
`tasks/todo.md`.

## Fichiers créés ou modifiés

| Fichier | Action |
|---|---|
| Code applicatif (`tbsdelivery/`, `hooks/`, `deploy/`, `tests/`, `install.sh`, `requirements.txt`, `config.example.yaml`) | Ajouté depuis le ZIP, **sans modification** |
| `README.md` | Ajouté depuis le ZIP ; **1 seule modification** : suppression d'un domaine client réel (point 5 de « Points à valider avec TBS ») |
| `parc-actuel/` | **Non extrait** (données client réelles) |
| `.gitignore` | Complété : `inventory.yaml` (partout), `/bootstrap*.sh`, `/*.zip`, `*.eml`, `*.log` (en plus des règles du premier commit) |
| `.claude/settings.json` | Complété : mêmes fichiers + chemins de prod `/var/lib/tbs-delivery/**`, `/var/log/tbs-delivery/**`, `/opt/tbscertbot/data/keys/**` |
| `CLAUDE.md` | Commandes vérifiées (avertissement retiré), structure du code, chemin de config de secours, fichiers interdits complétés |
| `tasks/todo.md` | Plan de revalidation |
| `tasks/rapport-setup.md` | Ce rapport |
| `CLAUDE.md` (Advisor), agents, `tasks/lessons.md` | Inchangés depuis le premier commit |

Les deux exports CSV joints à la conversation n'ont été copiés **nulle part**.

## Choix faits et justification

- **`parc-actuel/` exclu dès l'extraction** (`unzip -x`) plutôt qu'extrait puis ignoré :
  aucune copie locale de données client dans le dépôt.
- **Domaine client retiré du README** : la règle « aucune donnée client dans les
  commits » prime ; le README n'est pas dans la liste du code applicatif protégé.
- **`.gitignore` élargi** : le code produit d'autres fichiers sensibles que ceux listés
  au départ. `inventory.yaml` contient noms et mails ; les journaux contiennent CN et
  destinataires ; le script bootstrap contient les réf. TBS ; un ZIP du projet à la
  racine contiendrait `parc-actuel/`.
- **Chemins de prod dans `settings.json`** : par défaut, tout est sous
  `/var/lib/tbs-delivery` (état, ZIP, outbox, inventaire). Utile si Claude tourne un jour
  sur le hub.
- **Rappel** : ces règles bloquent l'outil Read, pas un `cat` lancé via Bash.
- **pyzipper installé** (`pip install -r requirements.txt`, avec ton accord).

## Inventaire des tests

### Tests existants (`tests/test_e2e.py`, sans réseau, PKI de test via openssl)

| Test | Ce qu'il vérifie |
|---|---|
| `test_classification` | `import-csv` + `report` : comptage par catégorie (refabrication, rachat, remplacé, hors périmètre eIDAS) |
| `test_delivery_after_reissue_new_reference` | Nouvelle réf. TBS retrouvée par le CN ; 2 mails (ZIP au contact admin + cc interne, puis mot de passe) ; ZIP AES ouvrable avec le mot de passe, 6 fichiers ; PFX ouvrable ; **idempotence** : 2e appel du hook = aucun nouveau mail |
| `test_dcv_grouped_then_closed` | 2 challenges DCV → 1 seul mail groupé ; pas de relance immédiate ; DCV close après livraison |
| `test_key_mismatch_alerts_internal` | Clé ≠ certificat → 1 seul mail, alerte interne, rien au client |
| `test_unknown_certificate_goes_internal_only` | Certificat inconnu → tous les mails vont à l'équipe interne. Limite : le test passerait aussi si aucun mail n'était produit |

### Sortie

```
$ python3 -m unittest discover -s tests -v
test_classification (test_e2e.EndToEnd.test_classification) ... ok
test_dcv_grouped_then_closed (test_e2e.EndToEnd.test_dcv_grouped_then_closed) ... ok
test_delivery_after_reissue_new_reference (test_e2e.EndToEnd.test_delivery_after_reissue_new_reference) ... ok
test_key_mismatch_alerts_internal (test_e2e.EndToEnd.test_key_mismatch_alerts_internal) ... ok
test_unknown_certificate_goes_internal_only (test_e2e.EndToEnd.test_unknown_certificate_goes_internal_only) ... ok
----------------------------------------------------------------------
Ran 5 tests in 7.220s
OK
```

Environnement : Python 3.13.16, OpenSSL 3.0.13, cryptography 50.0.1, PyYAML 6.0.1,
pyzipper 0.4.0.

### Fonctions critiques non couvertes, classées par risque

« Sonde » = petit essai jetable dans le scratchpad, hors du dépôt, pour vérifier un doute
à la lecture du code. Aucun test n'a été ajouté.

| # | Fonction / comportement | Code | Constat |
|---|---|---|---|
| 1 | **Surcharges manuelles après changement de réf.** (`merge_from_csv`) | `inventory.py:320-346` | ⚠ **Défaut confirmé par sonde** : si un nouvel export TBS porte une réf. différente pour le même certificat, les surcharges (`delivery`, `dcv`, `managed: false`…) sont **perdues**, et l'ancienne entrée est supprimée, sauf si ses `notes` commencent par `[manuel]` (convention non documentée). Conséquence : mails au contact admin par défaut au lieu du destinataire choisi, ou reprise d'un certificat exclu. Conservation OK si la réf. est identique. |
| 2 | **Erreur inattendue dans le hook download** | `hooks.py:48-93` | ⚠ **Défaut confirmé par sonde** : seule `PackageError` est rattrapée. Toute autre exception rend l'échec **silencieux** : pas d'alerte interne, pas de ligne en base, et la trace part seulement sur stderr, pas dans le fichier journal. Exemples : certificat PEM corrompu (`ValueError`), faute de frappe YAML dans `inventory.yaml` (bloque alors **toutes** les livraisons), droits insuffisants sur le dossier de sortie, erreur SQLite. TBSCertBot n'échoue pas (le wrapper fait `exit 0`) et rien ne part au client. |
| 2b | **Surcharge `password_channel` par certificat ignorée** | `hooks.py:84-86, 95-96` | ⚠ **Défaut confirmé par le relecteur (sonde) et à la lecture** : la surcharge `delivery` de l'inventaire n'est appliquée qu'aux destinataires. `password_channel`, `pfx`, `pfx_legacy`, `zip_encrypt` et `attach_max_mb` sont lus dans la config globale. Avec `delivery: {password_channel: internal_only}` sur un certificat, **le mot de passe part quand même au client**. |
| 2c | **Idempotence : livraison enregistrée après l'envoi** | `hooks.py:98-125` | ⚠ Constat du relecteur, à la lecture. Un plantage entre l'envoi et `record_delivery` entraîne une 2e livraison au passage suivant (nouveau ZIP, nouveau mot de passe). Si le mail du ZIP part et que celui du mot de passe échoue, le statut `failed` n'est pas compté comme livré : le client a un ZIP sans mot de passe, et la relance conseillée envoie un autre ZIP avec un autre mot de passe. |
| 2d | **Rapprochement par un seul SAN** | `inventory.py:369-375`, `hooks.py:78-79` | Constat du relecteur. Si la réf. et le CN sont inconnus, un seul SAN commun avec un certificat d'un autre client suffit pour lui livrer le ZIP, clé comprise, puis l'alias est mémorisé. Probabilité faible, conséquence grave. |
| 3 | Refus d'un certificat expiré | `package.py:198` | Non testé. Code correct à la lecture (`days_left < 0` → `PackageError` → alerte interne). |
| 4 | `password_channel: internal_only` pour un client connu | `hooks.py:96-112` | Non testé. Correct si l'option est réglée dans la config globale ; **ignorée si elle est réglée par certificat** (voir 2b). |
| 5 | Relivraison forcée (`--force`, `TBS_DELIVERY_FORCE=1`) | `hooks.py:45,57` | Non testé. |
| 6 | Envoi SMTP réel (`mailer.send`) | `mailer.py:64-93` | Non testé. Remarque : si `starttls: false` et `username` renseigné, l'authentification part **en clair** (pas de garde-fou). L'exemple de config utilise bien STARTTLS. |
| 7 | Relances DCV (délai, maximum) | `hooks.py:234-238` | Non testé. Sonde : 1 envoi initial + 5 relances = 6 mails, conforme au README. |
| 8 | `dns_gandi.upsert_record` | `dns_gandi.py` | Non testé (URL, guillemets TXT, point final CNAME, erreurs HTTP). Désactivé par défaut et limité aux zones listées. |
| 9 | Hooks shell : ne jamais faire échouer TBSCertBot | `hooks/*.sh` | Non testé. À la lecture : `exit 0` systématique, y compris si `cd` échoue. |
| 10 | Dépassement de `attach_max_mb` | `hooks.py:95` | Non testé. Si `cc` est vide, personne en interne n'est prévenu qu'il faut transmettre le ZIP à la main. |
| 11 | `pfx_legacy` (3DES/SHA1) | `package.py:219-224` | Non testé. |
| 12 | Concurrence : deux hooks simultanés pour le même certificat | `hooks.py:56-60` | Non testé, aucun verrou : double livraison possible en théorie. Le cron est séquentiel, mais un `test-hook` manuel lancé pendant le timer suffit. Risque faible. |
| 13 | `estimate_reissues` / `max_validity` | `inventory.py:32-44, 263-282` | Non testé. Sonde : 398 j avant le 15/03/2026, 200 j, 100 j à partir du 15/03/2027, 47 j à partir du 15/03/2029 : conforme au calendrier CA/B Forum. |
| 14 | `digest` | `report.py:94-146` | Non testé. |
| 15 | `parse_fr_date` | `inventory.py:74-83` | Non testé directement. Sonde : `jj/mm/aaaa`, `aaaa-mm-jj`, `jj/mm/aa` OK, date invalide → `None`. |
| 16 | `bootstrap_script` | `bootstrap.py` | Non testé. Un nom de client contenant un retour à la ligne sortirait du commentaire shell, et `c.ref` n'est pas protégé par `shlex.quote` : risque faible, mais le script est exécuté sur le hub. |
| 18 | DCV redemandée avec le même enregistrement | `state.py:126` | Constat du relecteur. Si la réf. TBS et le CNAME restent identiques (`reuse-keys`), une nouvelle demande retombe sur une ligne `done` et reste `done` : client jamais notifié, absente du digest. |
| 19 | Points mineurs | `package.py`, `hooks.py:228` | Pas de contrôle de `not_before` (un certificat pas encore valide serait livré) ; dans `notify`, un échec SMTP sur une DCV HTTP interrompt les notifications des autres certificats. |
| 17 | Fermeture de la base si certificat « non géré » | `hooks.py:68-70` | `return` sans `state.close()` : sans effet grave (fin de processus). |

## Relecture

Verdict du sous-agent `relecteur` : **OK avec réserves**, rien de bloquant pour le
commit. Aucune donnée client ni aucun secret ; la clé privée et le mot de passe ne sont
jamais journalisés ni stockés en base. Il a corrigé une affirmation fausse sur le
défaut n°2 et ajouté les défauts 2b, 2c, 2d, 18 et 19. Il suggère d'étendre les règles
de `settings.json` aux sous-dossiers (`**/inventory.yaml`, `**/outbox/**`,
`**/deliveries/**`) : **non appliqué, à ta décision**, car il s'agit de règles de
permission. Côté `.gitignore`, `inventory.yaml` est maintenant ignoré partout.

## Incohérences README / code / description

1. **Surcharges « conservées au ré-import »** (README, 2 endroits) : faux quand la réf.
   TBS change (voir défaut n°1). La convention `notes: "[manuel] …"` n'est pas documentée.
2. **Prémisse « chaque refabrication crée une nouvelle réf. TBS »** : dans l'export TBS,
   la colonne « Réf CA » porte des suffixes de remplacement (`…repl#N`), alors que la
   « Réf TBS » semble rester la même. **À valider avec TBS.** Si la réf. TBS ne change pas,
   le défaut n°1 ne se produit pas ; si elle change, il se produit à chaque ré-import.
3. **Certificat inconnu** : ta description parle d'une « alerte interne ». Le code
   **livre** le ZIP et le mot de passe à l'équipe interne, ce qui est conforme au README.
   Aucun envoi au client dans les deux cas.
4. **Extrait `conf-snippet`** : `emergencyReissue`, `reuse-keys`, `logLevel`, `sendLog`
   sont écrits sous l'en-tête `[HOOKS]`. Dans un fichier .ini, ils appartiennent donc à la
   section HOOKS. À vérifier dans le manuel TBSCertBot. L'adresse `certificats@atheo.net`
   y est codée en dur au lieu de venir de la config.
5. **Variables `PHP_TBS_*`** : le code suppose des SAN séparés par des virgules et des
   méthodes DCV nommées `dns-cname-token`, `dns-txt-token`, `http-token`. À confronter au
   manuel TBSCertBot §3.1.3 (sous-agent `documentaliste`).
6. **Mot de passe « jamais stocké »** : vrai en mode live. En dry-run, il est écrit dans
   le `.eml` de l'outbox (fichier en 600). Le README ne le signale pas.
7. **Config par défaut** : le code se rabat sur `./config.yaml` si
   `/etc/tbs-delivery/config.yaml` est absent. Ce n'est pas documenté.

## Points à valider par toi

1. **Données client déjà suivies par git** : aucune. Le premier commit ne contenait que la
   configuration Claude ; `parc-actuel/` n'est pas extrait ; le seul domaine client du
   README a été retiré.
2. **CSV joints à la conversation** : ils contiennent des données client réelles et ont
   été lus automatiquement à l'attachement. Ne plus les joindre.
3. **Défauts n°1, 2, 2b, 2c et 2d** : à corriger avant le pilote ? Je recommande au
   minimum 2b (mot de passe au client malgré la consigne) et 2 (échecs silencieux).
   Chacun demande un plan dans `tasks/todo.md` et ta validation (cause racine + test).
4. **Points TBS** : réf. TBS stable ou non après refabrication (incohérence n°2),
   structure de `conf.ini` (n°4), format des `PHP_TBS_*` (n°5).
5. **Outil advisor** : disponibilité non confirmée dans ma version (Claude Code 2.1.292 ;
   `claude --help` ne le mentionne pas). Tape `/advisor` ou regarde `/help`.
6. **Tests cloud** : pyzipper a été installé à la main dans cette session. Pour les
   prochaines sessions cloud, il faudrait un hook SessionStart
   (`pip install -r requirements.txt`).

## Commandes utiles

```bash
python3 -m unittest discover -s tests -v
git status --ignored --short          # vérifier qu'aucune donnée client n'est suivie
```
