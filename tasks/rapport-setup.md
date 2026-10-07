# Rapport de mise en place Claude Code — 2026-10-07

## ⚠ Constat principal : le dépôt est vide

Le dossier `/home/user/atheo-certbot` est bien un dépôt git (branche
`claude/lucid-albattani-qaam1x`), mais il ne contient **aucun commit ni aucun
fichier** : pas de `tbsdelivery/`, `hooks/`, `deploy/`, `tests/`, `install.sh`,
`README`, `requirements.txt` ni `parc-actuel/`. Le remote
`github.com/TiagoDSLV/atheo-certbot` est vide lui aussi (`git ls-remote` ne renvoie
aucune branche).

Conséquences :
- ce que tu as décrit (stack, commandes, tests) **n'a pas pu être vérifié dans le
  code**. C'est repris tel quel dans `CLAUDE.md`, avec un avertissement explicite ;
- l'inventaire des tests (tâche 6) n'a pas pu être fait sur du code réel ;
- aucune donnée client n'est suivie par git (il n'y a rien de suivi du tout).

## Fichiers créés (aucun fichier modifié, aucun commit, aucun push)

| Fichier | Rôle |
|---|---|
| `.gitignore` | Créé (il n'existait pas). N'ignore que `.claude/settings.local.json` côté Claude, plus les données client et les fichiers sensibles |
| `CLAUDE.md` | Contexte du projet, méthode, vérification, apprentissage, advisor, relecture, interdits, langue |
| `tasks/todo.md` | Titre seul |
| `tasks/lessons.md` | Titre seul |
| `.claude/agents/explorateur.md` | Lecture seule (Read, Grep, Glob), sonnet |
| `.claude/agents/documentaliste.md` | WebSearch, WebFetch, Read, sonnet, sources citées |
| `.claude/agents/relecteur.md` | Read, Grep, Glob, Bash, opus, verdict sans correction |
| `.claude/settings.json` | `permissions.deny` en lecture sur les fichiers sensibles |
| `tasks/rapport-setup.md` | Ce rapport |

## Choix faits et justification

**.gitignore**
- La règle « `.claude/` ignoré en entier » n'existait pas (pas de `.gitignore`) :
  j'ai mis directement `.claude/settings.local.json`, pour que `settings.json` et
  `agents/` soient partagés via git.
- `/*.csv` : les exports TBS ne sont ignorés **qu'à la racine**, pour ne pas masquer
  d'éventuelles fixtures CSV fictives dans `tests/`.
- `/config.yaml` et `/env` à la racine seulement : un éventuel
  `deploy/config.example.yaml` ou modèle d'env reste suivi.
- `state.db-*` ajouté : SQLite crée des fichiers `-wal` / `-journal` qui contiennent
  aussi l'état.
- Vérifié avec `git check-ignore` : les 17 chemins sensibles testés sont ignorés ;
  `.claude/settings.json`, `.claude/agents/*`, `tests/fixtures/*.csv` et
  `deploy/config.example.yaml` restent suivis.

**CLAUDE.md** : environ une page et demie. Les commandes sont marquées « non
vérifiées ». La règle « plan + validation » ne s'applique qu'aux sessions suivantes.

**Sous-agents** : prompts en français. Tous ont la consigne de ne pas ouvrir les
fichiers sensibles. Le relecteur n'a pas d'outil d'écriture (pas d'Edit / Write),
mais il a Bash : il *pourrait* techniquement modifier des fichiers, son prompt le lui
interdit. Le documentaliste ne doit jamais mettre de nom client ou de réf. TBS dans
une recherche web.

**settings.json**
- Chemins en `./…` (relatifs au dossier du projet) et `**/…` pour les extensions de
  clés. Ajout de `//etc/tbs-delivery/config.yaml` et `//etc/tbs-delivery/env` (chemins
  absolus de prod, utiles si Claude tourne un jour sur le serveur).
- Rien sous `tests/` n'est bloqué.
- **Limite importante : ces règles ne bloquent que l'outil Read de Claude (et les
  outils de recherche intégrés). Elles n'empêchent pas un `cat parc-actuel/…`,
  `sqlite3 state.db` ou `unzip` lancé via Bash.** Pour durcir, tu peux ajouter des
  règles `Bash(cat ./parc-actuel/*)`, etc. (contournables), ou mieux : ne pas
  stocker ces données dans le dossier de travail.

## Inventaire des tests

**Tests existants** : aucun (dossier `tests/` absent). Sortie de la commande :

```
$ python3 -m unittest discover -s tests -v
ImportError: Start directory is not importable: 'tests'
```

Le fichier `tests/test_e2e.py` (5 tests de bout en bout) que tu mentionnes n'est donc
pas présent.

**Dépendances dans cet environnement** (constat, rien installé) :
- Python 3.13.16 ✅, openssl 3.0.13 ✅, cryptography 50.0.1 ✅, PyYAML 6.0.1 ✅
- **pyzipper ❌ absent** → les tests qui fabriquent le ZIP échoueraient ici.
- `requirements.txt` absent : versions attendues inconnues.
- PHP 8.3 présent, mais TBSCertBot (`/opt/tbscertbot`) absent (normal : les tests
  le simulent).

**Fonctions critiques à couvrir, classées par risque** (d'après tes pistes, **non
vérifiées dans le code** — à confirmer dès que le code est poussé) :

| # | Fonction / comportement | Risque si non testé |
|---|---|---|
| 1 | Refus d'un certificat expiré (aucun envoi client, alerte interne) | Livraison d'un certificat inutilisable au client |
| 2 | `password_channel: internal_only` | Mot de passe envoyé au client alors qu'il ne devait pas l'être |
| 3 | Relivraison forcée (vs idempotence par n° de série) | Double livraison, ou relivraison impossible en cas de besoin |
| 4 | `mailer.send` — envoi SMTP réel (TLS, auth, échecs) | Échec silencieux en prod ; seul le dry-run `.eml` est testé |
| 5 | Hooks shell : ne jamais faire échouer TBSCertBot | Blocage de la chaîne de refabrication de TOUS les certificats |
| 6 | Relances DCV (délai 3 jours, maximum 5) | Spam client ou DCV jamais validée → certificat expiré |
| 7 | `dns_gandi.upsert_record` | Écrasement ou suppression d'un enregistrement DNS d'Athéo |
| 8 | `merge_from_csv` : conservation des surcharges manuelles | Perte des contacts corrigés à la main → mail au mauvais destinataire |
| 9 | Dépassement de `attach_max_mb` | Mail rejeté par le SMTP, livraison perdue |
| 10 | `pfx_legacy` (chiffrement PFX ancien format) | PFX illisible sur équipements anciens (Windows Server, appliances) |
| 11 | `estimate_reissues` et `max_validity` (200 → 100 → 47 jours) | Mauvais calendrier de refabrication / estimation de charge |
| 12 | `parse_fr_date` | Dates mal lues depuis les CSV TBS → mauvais calcul d'échéance |
| 13 | `digest` | Récapitulatif interne faux ou absent |
| 14 | `bootstrap_script` | Script d'amorçage TBSCertBot erroné (réfs TBS mal reprises) |

## Points à valider par toi

1. **Où est le code ?** Le dépôt local et le remote sont vides. Pousse le code (sans
   `parc-actuel/` ni exports CSV), puis relance une session pour : vérifier les
   commandes de `CLAUDE.md`, faire l'inventaire réel des tests et la comparaison
   README / code.
2. **Données client suivies par git** : aucune aujourd'hui (rien n'est suivi).
   Attention au premier commit : vérifie avec `git status --ignored` que
   `parc-actuel/` et les CSV sont bien ignorés **avant** `git add`.
3. **Outil advisor** : je n'ai pas pu confirmer sa disponibilité (Claude Code 2.1.292
   ici ; `claude --help` ne mentionne pas d'advisor). À vérifier dans ta version :
   tape `/advisor` ou regarde `/help`. S'il n'existe pas, la section « Advisor » de
   `CLAUDE.md` reste lettre morte ; on pourra la remplacer par un appel au
   sous-agent `relecteur` ou par un modèle plus puissant.
4. **Incohérences README / code** : impossible à évaluer, ni README ni code présents.
5. **pyzipper** absent de cet environnement cloud : si tu veux que les sessions
   cloud lancent les tests, il faudra l'installer via un script de démarrage (hook
   SessionStart) — non fait, car tu as demandé de ne rien installer.
6. **Nom du fichier « env »** : j'ai supposé `./env` à la racine et
   `/etc/tbs-delivery/env`. À corriger si c'est un autre nom ou un autre chemin.

## Commandes utiles

```bash
# Vérifier ce qui serait ignoré avant le premier commit
git status --ignored --short
# Lancer les tests (une fois le code présent)
python3 -m unittest discover -s tests -v
```
