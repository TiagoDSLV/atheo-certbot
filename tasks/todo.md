# Plan en cours

Plans précédents : revalidation (commit `abe2c33`), défauts 2 et 2b (commit `48a14fc`),
2c (commit `47c2ba6`). Détails dans `tasks/rapport-setup.md`.

## Correction du défaut 2d — rapprochement par un seul SAN

Statut : **plan à valider** — rien n'est codé

### Problème (`inventory.py:369-375`, `hooks.py:29-35, 88-89`)

Quand la réf. TBS et le CN sont inconnus, `Inventory.find` retient tout certificat qui
partage **un seul** nom (SAN) avec la commande. Un SAN commun avec le certificat d'un
autre client suffit pour lui livrer le ZIP (clé privée comprise) ; l'alias est ensuite
mémorisé (`state.set_alias`) et la mauvaise attribution devient permanente.
Probabilité faible, conséquence grave (clé privée chez le mauvais client).

**Cause racine** : « partager au moins un nom » n'identifie pas une commande. Et en cas
d'ambiguïté, le code choisit quand même un candidat au lieu de refuser.

### Correction (`tbsdelivery/inventory.py` uniquement)

1. **Rapprochement SAN strict** : on retient un candidat seulement si l'ensemble des noms
   de la commande (CN + SAN du hook) est **identique** à l'ensemble de ses noms
   (`Cert.names`). Un recouvrement partiel ne suffit plus.
2. **Pas d'ambiguïté** : si plusieurs candidats correspondent et qu'ils appartiennent à
   des clients différents (`client_slug`), on ne choisit pas : `find` renvoie `None`.
   Le tri existant (géré, refabrication, fin de forfait) reste pour un même client.
3. Conséquence voulue : un certificat non reconnu suit le chemin « non référencé » déjà
   en place (livraison à l'équipe interne uniquement, pas d'alias mémorisé).
   Le rapprochement par réf. puis par CN (exact) ne change pas.

Pas de changement de schéma, ni de `hooks.py`, ni de la config.

### Tests (écrits d'abord, échec constaté sur le code actuel)

Dans `tests/test_e2e.py`, données fictives (`client-a.fr`, `client-b.fr`) :
- **SAN partagé seul** : réf. et CN inconnus, un SAN commun avec le certificat du client B
  → rien n'est envoyé à B, livraison interne « NON RÉFÉRENCÉ », aucun alias créé.
- **Ensemble de noms identique** (réf. et CN inconnus, mêmes noms dans un autre ordre) →
  rapproché du bon client (le cas légitime reste couvert).
- **Ambiguïté** : deux certificats de clients différents avec le même ensemble de noms →
  `find` renvoie `None`.
- Test unitaire direct de `Inventory.find` pour ces 3 cas.
- Les 22 tests existants restent verts (dont `test_delivery_after_reissue_new_reference`).

### Déroulé
1. Tests d'abord, échec montré sur le code actuel.
2. Correction dans `inventory.py`.
3. Suite complète, sortie montrée.
4. `tasks/rapport-setup.md` : 2d marqué corrigé.
5. Relecture par le sous-agent `relecteur`, verdict transmis ; commit après ton accord.

### Choix et risques
- **Risque** : une refabrication qui change légèrement la liste des SAN (ajout/retrait
  d'un nom) n'est plus rapprochée par SAN et part à l'équipe interne. C'est le sens
  voulu (pas de livraison au client en cas de doute) ; l'équipe peut ajouter l'alias
  dans `inventory.yaml`. Le rapprochement par CN couvre le cas courant.
- La réf. TBS stable ou non après refabrication reste un point à valider avec TBS.

## Correction du défaut 2c — idempotence des livraisons

Statut : **validé** — étapes 7 à 10 faites : 7 tests ajoutés, 6 en échec avant correction, 19/19 OK après ; extension « A » (nom du ZIP, QUIT SMTP) : 22/22 OK ; relecture finale OK ; commit validé

### Problème (constaté par le relecteur, `hooks.py:66-145`, `state.py:84-95`)

La livraison n'est enregistrée en base qu'**après** l'envoi des mails, et seuls les
statuts `sent` et `dry-run` comptent comme « déjà livré ». Trois scénarios :

| # | Scénario | Aujourd'hui |
|---|---|---|
| A | Plantage ou base verrouillée entre l'envoi et l'écriture en base | Aucune trace : au passage suivant, **2e livraison** (nouveau ZIP, nouveau mot de passe) |
| B | Le mail du ZIP part, celui du mot de passe échoue | Statut `failed`, non compté comme livré : le client a un ZIP **inutilisable**, et la relance conseillée par l'alerte envoie un autre ZIP avec un autre mot de passe, sans le dire |
| C | Deux exécutions simultanées (timer + `test-hook` manuel) | Les deux voient « pas encore livré » : **double livraison** |

**Cause racine** : la vérification « déjà livré ? » et l'enregistrement ne forment pas
une seule opération atomique, et l'enregistrement vient après l'envoi.

### Correction

1. **Réservation avant l'envoi** (`state.py`) : nouvelle méthode
   `reserve_delivery(serial, …, force)`. Dans une transaction `BEGIN IMMEDIATE`
   (verrou d'écriture SQLite, valable entre processus), elle vérifie qu'aucune ligne
   « livrée ou en cours » n'existe pour ce numéro de série, puis insère une ligne au
   statut `sending`. Elle renvoie l'identifiant de la ligne, ou rien si une livraison
   existe déjà (sauf `--force`). → règle **C**.
2. **Statuts mis à jour au fil de l'envoi** (`hooks.py`) : la ligne réservée passe à
   - `failed` si le 1er mail (ZIP) échoue : rien n'est parti, relance possible ;
   - `partial` si le ZIP est parti mais pas le mot de passe ;
   - `sent` ou `dry-run` si les deux sont partis.
3. **« Déjà livré » élargi** (`state.py`) : `sending`, `partial`, `uncertain`, `sent` et
   `dry-run` bloquent une nouvelle livraison automatique. Seul `--force` (décision
   humaine) passe outre. → règle **A** et **B** : en cas de doute, on ne renvoie pas.
4. **Alerte claire pour le cas B** : « le client a reçu l'archive mais pas le mot de
   passe ; le mot de passe n'est conservé nulle part ; pour lui envoyer une nouvelle
   archive avec un nouveau mot de passe : `TBS_DELIVERY_FORCE=1 php tbscertbot.php
   test-hook download <ref>` ».
5. **Livraisons interrompues (cas A)** : la commande `notify`, déjà lancée 2 fois par
   jour par le timer, repère les lignes `sending` de plus d'une heure, les passe en
   `uncertain` et envoie **une** alerte interne (« vérifier la boîte d'envoi et le
   journal ; relancer avec `--force` seulement si rien n'est parti »). Le `digest`
   hebdomadaire liste aussi les lignes `partial` et `uncertain`.
6. **Réservation refusée** : si une autre exécution a livré entre-temps, le ZIP qui
   vient d'être fabriqué est supprimé (il contient la clé et son mot de passe est
   perdu) et rien n'est envoyé.

Aucun changement de schéma SQLite (le statut est un texte libre) : pas de migration.

### Tests (écrits d'abord, avec échec constaté sur le code actuel)

- **A** : un trigger SQLite fait échouer la mise à jour finale après l'envoi ; un
  2e appel du hook n'envoie **aucun** nouveau mail.
- **B** : le 2e envoi échoue (test en processus, `Mailer.send` simulé) → statut
  `partial`, alerte interne avec la commande `--force`, aucun mot de passe ni clé dans
  l'alerte ; un 2e appel du hook n'envoie rien.
- **C** : une ligne `sending` existe déjà pour ce numéro de série → le hook n'envoie
  rien et supprime le ZIP fabriqué. Test unitaire de `reserve_delivery` : une 2e
  réservation est refusée. Test déterministe : je ne lance pas deux processus en
  parallèle, ce qui donnerait un test instable.
- **`--force`** relivre bien après `sent` et après `partial`.
- **`notify`** : une ligne `sending` vieille de plus d'une heure → 1 alerte, statut
  `uncertain` ; un 2e `notify` n'envoie pas de 2e alerte.
- Les 12 tests existants restent verts.

### Déroulé
7. Tests d'abord, échec montré sur le code actuel.
8. Correction : `tbsdelivery/state.py`, `tbsdelivery/hooks.py`, `tbsdelivery/report.py`
   (digest). Aucun autre fichier applicatif.
9. Suite complète, sortie montrée.
10. Mise à jour de `tasks/rapport-setup.md` (2c corrigé) et du README (section
    « Une seule livraison par certificat » : nouveaux statuts, rôle de `--force`).
11. Relecture par le sous-agent `relecteur`, verdict transmis.
12. Commit et push sur `claude/lucid-albattani-qaam1x` après ton accord.

### Choix et risques
- **Principe retenu : « au plus une livraison automatique »**. En cas de doute (plantage
  pendant l'envoi), on préfère ne pas relivrer et prévenir l'équipe, plutôt que
  d'envoyer un 2e ZIP. Contrepartie : un certificat peut rester non livré jusqu'à ce
  que l'équipe relance avec `--force`. Le délai maximal est d'une demi-journée, au
  passage suivant de `notify`.
- **Cas B** : le mot de passe n'étant jamais stocké, impossible de renvoyer le même. La
  seule réparation reste une nouvelle archive, mais elle est désormais explicite et
  décidée par l'équipe.
- **`--force` reste sans verrou contre lui-même** : deux `--force` simultanés
  livreraient deux fois. C'est une action humaine, et le risque est accepté.
- **Envoi SMTP partiel** (mail accepté par le serveur, puis la connexion coupe avant la
  confirmation) : le code le verra comme un échec du 1er mail (`failed`). C'est
  indécidable côté client SMTP. Je le signale sans le traiter.
- **Bases existantes** : les anciennes lignes `failed` ne bloquent rien, comme avant.

### Hors périmètre
2d (rapprochement par un seul SAN), 1 (surcharges perdues au changement de réf.),
`pfx_legacy: "false"` entre guillemets.

### Extension validée après relecture (choix « A », 2026-10-10)
13. **Nom du ZIP unique** (`package.py`) : un suffixe aléatoire évite que deux exécutions
    dans la même seconde écrivent (puis suppriment) la même archive.
14. **Fin de connexion SMTP** (`mailer.py`) : une coupure réseau pendant `QUIT`, après
    l'acceptation du mail, ne doit plus faire croire que rien n'est parti.
15. Tests d'abord (échec constaté), correction, suite complète, nouvelle relecture,
    commit après accord.
