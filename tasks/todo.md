# Plan en cours

Plan précédent (revalidation sur le vrai code) : terminé, voir `tasks/rapport-setup.md`
et le commit `abe2c33`.

## Correction des défauts 2b et 2 (rapport-setup.md)

Statut : **validé** (hook dcv inclus) — étapes 6 à 9 faites : 6 tests ajoutés, échec constaté avant correction, 11/11 OK après ; 1re relecture « À reprendre » traitée (alerte après envoi), 12/12 OK ; 2e relecture OK avec réserves — reste : commit après accord

### Défaut 2b — surcharge par certificat ignorée (`hooks.py:84-86, 95-96`)

**Cause racine** : `handle_download` fusionne la surcharge `delivery` de l'inventaire
(`dspec`), mais ne l'utilise que pour les destinataires. `password_channel`, `pfx`,
`pfx_legacy`, `zip_encrypt` et `attach_max_mb` sont lus dans la config globale (`dcfg`).

**Correction**
1. Calculer une seule fois les réglages effectifs : `dspec` = config globale + surcharge
   du certificat (config globale seule si le certificat est inconnu). Utiliser `dspec`
   pour les 5 options.
2. **Repli sécurisé** : toute valeur de `password_channel` autre que `separate_email`
   (faute de frappe comme `internal-only`, valeur vide…) est traitée comme
   `internal_only`. Le mot de passe part alors à l'équipe interne, jamais au client par
   erreur. Un avertissement est journalisé.

**Tests ajoutés** (`tests/test_e2e.py`, données fictives `client-a.fr`)
- `password_channel: internal_only` posé sur un certificat → le mail du mot de passe
  part uniquement à l'équipe interne ; aucun mail destiné au client ne contient le mot
  de passe.
- Valeur inconnue (`internal-only`) → même comportement (repli sécurisé).
- `pfx: false` posé sur un certificat → ZIP à 5 fichiers, sans PFX (preuve que les
  autres options sont bien lues par certificat).

### Défaut 2 — erreurs inattendues silencieuses (`hooks.py:40-130`)

**Cause racine** : seule `PackageError` est rattrapée, et seulement autour de la
fabrication. Tout le reste (inventaire YAML invalide, base SQLite, certificat PEM
corrompu, droits sur un dossier) remonte jusqu'à l'interpréteur : la trace part
uniquement sur stderr, sans alerte, sans ligne en base, sans entrée dans le journal.

**Correction**
3. Garde-fou unique à l'entrée du hook `download` : toute exception non prévue est
   - écrite dans le journal (`log.exception`, donc dans le fichier de log) ;
   - enregistrée en base comme livraison `failed`, si la base est accessible ;
   - signalée par une alerte interne (type d'erreur + message, sans trace détaillée).
   Rien ne part au client. Le code de sortie reste 0, comme pour les autres échecs
   (TBSCertBot ne doit jamais échouer).
4. Même garde-fou sur le hook `dcv` : même cause (un inventaire YAML invalide le fait
   aussi échouer en silence). Pas d'enregistrement en base dans ce cas, seulement
   journal + alerte.
5. Vérifier qu'aucune alerte ne peut contenir la clé privée ou le mot de passe : le
   message d'alerte ne reprend que le type et le texte de l'exception, jamais le contenu
   des fichiers ni des variables.

**Tests ajoutés**
- Certificat PEM corrompu → 1 alerte interne, aucun mail au client, ligne `failed`
  visible dans `status`, erreur présente dans le fichier journal.
- `inventory.yaml` invalide → hook `download` : alerte interne, aucun mail au client.
- `inventory.yaml` invalide → hook `dcv` : alerte interne.
- Aucune alerte ne contient `PRIVATE KEY` ni le mot de passe.

### Déroulé
6. Écrire d'abord les tests et montrer qu'ils **échouent** sur le code actuel.
7. Corriger `tbsdelivery/hooks.py` (seul fichier applicatif modifié).
8. Lancer toute la suite (les 5 tests existants + les nouveaux) et montrer la sortie.
9. Mettre à jour `tasks/rapport-setup.md` (défauts 2 et 2b marqués corrigés).
10. Relecture par le sous-agent `relecteur`, puis te donner son verdict.
11. Commit et push sur `claude/lucid-albattani-qaam1x`, uniquement après ton accord.

### Hors périmètre (restent ouverts)
- 2c (idempotence : enregistrement après l'envoi), 2d (rapprochement par un seul SAN),
  1 (surcharges perdues au changement de réf.).
- Le garde-fou ne règle pas ce cas : mail envoyé puis écriture en base qui échoue.
  L'alerte interne part, mais une relivraison reste possible au passage suivant
  (relève de 2c).
- `notify` (échec SMTP sur une DCV HTTP qui interrompt la boucle) : non traité.

### Risques
- Si le vrai `inventory.yaml` contient déjà des surcharges `pfx`, `zip_encrypt`… par
  certificat, elles prendront effet (comportement voulu, mais nouveau). Je ne peux pas
  le vérifier : ce fichier m'est interdit en lecture.
- Repli sécurisé : une valeur mal saisie fera passer le mot de passe par l'équipe
  interne au lieu du client. C'est voulu, mais l'équipe devra le transmettre.
- L'outil advisor n'étant pas confirmé, la relecture se fera par le sous-agent
  `relecteur`.
