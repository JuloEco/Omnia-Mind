# 🧠 OmniaMind

Plateforme d'apprentissage communautaire type Quizlet — decks, 6 modes de
révision (Flashcards 3D avec SRS, Match, Swipe, Quiz QCM, Dictée, Cartes
fragiles), XP, séries de jours, heatmap d'activité, succès à débloquer.
Fait partie de l'écosystème **Octix**, dans la branche **Omnia**.

### Renforcement de la rétention (ajouté après le passage initial)
- **Espacement massé en session** : une carte/question ratée n'attend pas le
  lendemain — elle revient 5 à 7 cartes plus loin dans la *même* session
  (Flashcards, Quiz, Swipe, Dictée), plafonné à 2 réinjections par carte.
- **Rappel actif écrit** en Flashcards : un champ facultatif pour taper sa
  réponse avant de retourner la carte (effet de génération), comparée à la
  définition réelle une fois la carte retournée.
- **Mode "Cartes fragiles"** (`focus`) : session ciblée sur les cartes déjà
  vues qui ont un ease factor bas, un échec récent ou une rétention estimée
  faible — sans attendre leur échéance SRS.
- **Plafond d'intervalle SRS** (`MAX_INTERVAL_DAYS = 90` dans `models.py`) :
  même une carte maîtrisée revient au moins tous les ~3 mois pour une
  piqûre de rappel, plutôt que de voir son intervalle croître sans limite.

## 🔐 Authentification — pas de Flask-Login

OmniaMind ne gère **aucun mot de passe** et ne propose **aucun formulaire
d'inscription**. Toute l'authentification est déléguée au service Octix,
exactement comme LearnCode :

- `/login` (POST) envoie le pseudo/mot de passe à `OCTIX_URL/login` et
  récupère un token de session (stocké côté serveur, jamais décodé ici).
- Le bouton « Pas encore de compte ? » renvoie vers `OCTIX_PORTAL_URL`, le
  seul endroit où un compte Octix peut être créé.
- À la première connexion réussie, OmniaMind crée automatiquement un profil
  local minimal (XP, streak, avatar) associé au pseudo Octix — voir
  `octix_auth.get_or_create_local_profile`.

Voir `octix_auth.py` pour l'implémentation complète (`octix_login`,
`login_required`, `owner_required`, `current_user`).

## 🗂️ Structure

```
omniamind/
├── app.py                 # Routes, logique des 6 modes, APIs JSON
├── models.py               # Users, Decks, Cards, CardProgress (SRS), Favorites, StudyLog
├── octix_auth.py            # Auth Octix (remplace Flask-Login)
├── requirements.txt
├── .env.example
├── static/
│   ├── css/style.css        # Flip 3D, glassmorphism, animations
│   └── js/app.js            # Toasts, confettis, level-up, helpers fetch
└── templates/
    ├── base.html             # Layout, navbar, design system
    ├── login.html
    ├── dashboard.html
    ├── explore.html          # Galerie communautaire + recherche
    ├── deck_form.html        # Création / édition de deck
    ├── deck_detail.html      # Choix du mode de révision
    ├── profile.html          # Stats + heatmap d'activité
    ├── study_flashcards.html # Flashcards 3D + SRS
    ├── study_match.html      # Match / sprint chrono
    ├── study_swipe.html      # Swipe façon Tinder
    ├── study_quiz.html       # QCM
    └── study_dictation.html  # Dictée audio (speechSynthesis)
```

## ⚙️ Lancer en local

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # puis édite les valeurs
export $(cat .env | xargs)   # ou utilise python-dotenv / honcho
python app.py
```

L'app tourne alors sur `http://localhost:5000`. Il te faut un service Octix
(`OCTIX_URL`) joignable pour te connecter — en local, pointe-le vers ton
instance de `octix.py`.

## 🧮 Le moteur SRS

`models.CardProgress.apply_review(difficulty)` implémente une variante
simplifiée de SM-2 :
- **À revoir** → l'intervalle retombe à ~30 min et s'allonge légèrement à
  chaque échec consécutif (`lapses_in_a_row`), le coefficient de facilité
  diminue.
- **Moyen** → l'intervalle est multiplié par 1.35, la série d'échecs et de
  "facile" consécutifs est remise à zéro.
- **Facile** → l'intervalle est multiplié par le coefficient de facilité
  (qui augmente) ; la carte ne passe "maîtrisée" qu'après **2 réponses
  faciles d'affilée** avec un intervalle ≥ 21 jours (pas une seule réponse
  chanceuse).

`CardProgress.retention_pct` calcule une "santé du souvenir" en % via une
courbe de l'oubli exponentielle basée sur le temps écoulé depuis la dernière
révision et l'intervalle prévu. `User.average_retention()` en fait la
moyenne sur toutes les cartes déjà révisées et l'affiche sur le dashboard.

## 🔒 Sécurité

- **Accès aux decks** : `_accessible_deck_or_none()` (dans `app.py`) est le
  seul point d'entrée pour vérifier qu'un utilisateur peut voir/réviser un
  deck (public, propriétaire, ou lien de partage validé en session). Toute
  nouvelle route qui prend un `deck_id` doit passer par là.
- **CSRF** : jeton de synchronisation (`csrf_token()` / `csrf_protect()`
  dans `octix_auth.py`), vérifié sur chaque requête POST/PUT/PATCH/DELETE.
  Les formulaires portent un champ cascé `csrf_token`, les appels `fetch()`
  envoient le header `X-CSRFToken` (voir `static/js/app.js`).
- **SECRET_KEY** : l'app refuse de démarrer avec une valeur par défaut ou
  vide hors `FLASK_DEBUG=1` (voir `app.py`).

## ✨ Fonctionnalités additionnelles

- **Tags** sur les decks (recherchables dans Explorer, en plus du titre et
  de la description).
- **Import/export CSV** d'un deck (`term,definition` ou `term;definition`,
  avec ou sans en-tête) — bouton dans le formulaire de deck et sur la page
  du deck.
- **Images par carte** (`Card.image_url`, une URL) affichées en Flashcards,
  Quiz, Swipe et Match. Absentes du mode Dictée par choix pédagogique :
  l'exercice porte sur l'orthographe entendue, pas la reconnaissance
  visuelle.
- **Lien de partage** pour un deck privé (`Deck.share_token`, route
  `/d/<token>`) : donne accès au deck pour la session du visiteur sans le
  rendre public.

## 🧪 Compte de démo

Si `OCTIX_URL` n'est pas encore joignable (pas de service `octix.py` lancé en
local, ou pas encore déployé), connecte-toi avec :

```
Identifiant : demo
Mot de passe : demo1234
```

Ce couple contourne l'appel réseau vers Octix (voir `DEMO_ENABLED` dans
`octix_auth.py`) et crée un profil OmniaMind local classique. Pratique pour
tester l'appli sans dépendre du reste de l'écosystème. À désactiver en
production avec `OMNIAMIND_DEMO=0` dans les variables d'environnement (sinon
n'importe qui peut se connecter avec `demo`/`demo1234`).

## 🩹 Dépannage

**« Le service Octix est injoignable »** — normal si `OCTIX_URL` ne pointe
vers aucun `octix.py` en cours d'exécution (par défaut `http://localhost:5050`).
Soit tu lances ce service, soit tu utilises le compte de démo ci-dessus.

**`psycopg2.errors.DatatypeMismatch` / `foreign key constraint ... cannot be
implemented` au démarrage** — ça arrive si `DATABASE_URL` pointe vers une
base Postgres **déjà utilisée par un autre projet** de l'écosystème (par
exemple une base où `users` existe déjà avec un `id TEXT`, comme dans le
schéma générique `TABLES = ["cours","users",...]` de LearnCode). Pour éviter
tout conflit, toutes les tables d'OmniaMind sont préfixées
(`omniamind_users`, `omniamind_decks`, etc.) — si tu vois encore cette erreur,
c'est qu'une table `omniamind_*` existe déjà dans une version incompatible :
le plus simple est d'utiliser une base Postgres dédiée à OmniaMind plutôt que
de la partager avec les autres apps Octix.

## 🚀 Déploiement (Render)

Mêmes conventions que le reste de l'écosystème :
- `DATABASE_URL` → Postgres managé par Render (le préfixe `postgres://` est
  toléré, converti automatiquement).
- `OCTIX_URL` / `OCTIX_PORTAL_URL` → URLs internes/publiques du hub Octix.
- Build : `pip install -r requirements.txt`
- Start : `gunicorn app:app`
