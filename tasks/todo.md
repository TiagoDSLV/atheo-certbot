# Plan en cours

## Revalidation de la mise en place sur le vrai code (tbs-delivery.zip)

Statut : **validé le 2026-10-07** — étapes 1 à 6 faites (relecteur : OK avec réserves) ; pyzipper installé ; commit du code demandé

### Étapes

1. **Extraire le code sans les données client**
   - Extraire le ZIP dans le dépôt en **excluant `parc-actuel/`** (rien n'est extrait,
     rien n'est lu au-delà du nom des fichiers).
   - Ne pas copier les deux exports CSV joints (`20261007084433.csv`,
     `plan_action_certificats_2026-10-07.csv`) dans le dépôt.
   - Contrôle : `git status --ignored` ; recherche de mails, domaines et réf. TBS
     réels dans le code, les tests et `config.example.yaml`.

2. **Vérifier `.gitignore` et `.claude/settings.json` face au code réel**
   - Chemins réels de `state.db`, `outbox/`, `deliveries/`, des clés, de la config
     et du fichier env (lus dans `config.py` et `config.example.yaml`).
   - Ajuster si besoin (ex. `*.pem`, `data/state.db`, réserves du relecteur).

3. **Vérifier `CLAUDE.md`**
   - Stack, commandes de lancement et de test, chemin de config par défaut, liste
     des commandes CLI (lue dans `__main__.py`), mode dry-run par défaut.
   - Retirer l'avertissement « non vérifié » ou corriger ce qui diffère.

4. **Inventaire réel des tests** (sans modifier `tests/`)
   - Lister les tests de `test_e2e.py` et ce que chacun vérifie.
   - Les lancer. **pyzipper manque ici** : je note l'échec sans rien installer.
     Si tu veux que je l'installe (`pip install -r requirements.txt`), dis-le.
   - Confronter les 14 fonctions critiques au code réel, classées par risque.

5. **Comparer le README et le code** : relever les incohérences.

6. **Mettre à jour `tasks/rapport-setup.md`**, faire relire par le sous-agent
   `relecteur`, puis te donner son verdict.

7. **Commit et push** sur `claude/lucid-albattani-qaam1x` : uniquement après ton
   accord explicite. Question ouverte : committer aussi le code applicatif extrait du
   ZIP (sans `parc-actuel/`), ou seulement les fichiers de configuration Claude ?

### Hors périmètre
Aucune modification du code applicatif, aucun appel à TBS, au SMTP ou à Gandi,
aucun passage en mode live.
