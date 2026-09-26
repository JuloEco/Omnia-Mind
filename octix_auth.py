"""
octix_auth.py — Authentification déléguée à Octix (aucun Flask-Login ici).

OmniaMind ne stocke jamais de mot de passe. Chaque connexion vérifie le
couple identifiant/mot de passe auprès du service Octix (OCTIX_URL), qui
répond avec un token de session. Ce token est gardé en session Flask pour
les futures requêtes qui doivent parler à d'autres services de l'écosystème
(Opsiom, Axiom...), mais OmniaMind lui-même ne le décode pas.

La création de compte se fait exclusivement sur le portail Octix
(OCTIX_PORTAL_URL) : OmniaMind ne propose aucun formulaire d'inscription,
seulement un lien vers ce portail.
"""
import os
import secrets
from functools import wraps

import requests
from flask import session, redirect, url_for, request, flash, abort

from models import db, User

from dotenv import load_dotenv

load_dotenv()

OCTIX_URL = os.environ.get("OCTIX_URL", "http://localhost:5050")
OCTIX_PORTAL_URL = os.environ.get("OCTIX_PORTAL_URL", "http://localhost:5051")
OCTIX_APP_NAME = "OmniaMind"

# ----------------------------------------------------------------------
# Compte de démo — utile en local quand aucun service Octix (octix.py)
# n'est lancé/joignable sur OCTIX_URL. Contourne l'appel réseau pour ce
# seul couple identifiant/mot de passe. Désactivable en prod en mettant
# OMNIAMIND_DEMO=0 dans l'environnement.
# ----------------------------------------------------------------------
DEMO_ENABLED = os.environ.get("OMNIAMIND_DEMO", "1") == "1"
DEMO_USERNAME = os.environ.get("OMNIAMIND_DEMO_USER", "demo")
DEMO_PASSWORD = os.environ.get("OMNIAMIND_DEMO_PASSWORD", "demo1234")


def octix_login(username, password):
    """Vérifie les identifiants auprès d'Octix.
    Retourne (ok: bool, token_ou_message_erreur: str)."""
    if DEMO_ENABLED and username == DEMO_USERNAME and password == DEMO_PASSWORD:
        return True, "demo-token"
    try:
        r = requests.post(
            f"{OCTIX_URL}/login",
            json={"username": username, "password": password},
            timeout=5,
        )
        if r.status_code == 200:
            return True, r.json().get("token", "")
        return False, "Pseudo ou mot de passe Octix incorrect."
    except requests.exceptions.RequestException:
        return False, "Le service Octix est injoignable pour le moment. Réessaie plus tard."


def get_or_create_local_profile(octix_username):
    """Récupère (ou crée à la première connexion) le profil OmniaMind local
    associé à ce pseudo Octix. C'est la seule "inscription" côté OmniaMind :
    aucune donnée sensible, juste un profil de progression."""
    user = User.query.filter_by(octix_username=octix_username).first()
    if user is None:
        user = User(octix_username=octix_username, display_name=octix_username)
        db.session.add(user)
        db.session.commit()
    return user


# ----------------------------------------------------------------------
# CSRF — synchronizer token pattern (pas de dépendance externe).
# Un jeton aléatoire est stocké côté session à la première visite ; tout
# POST doit renvoyer ce même jeton, soit dans un champ caché du formulaire
# (`csrf_token`), soit dans le header `X-CSRFToken` pour les appels fetch().
# ----------------------------------------------------------------------
def csrf_token():
    """À appeler depuis les templates : {{ csrf_token() }}."""
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return session["csrf_token"]


def csrf_protect():
    """À brancher sur app.before_request. Ne bloque que les requêtes qui
    modifient un état (POST/PUT/PATCH/DELETE) ; laisse passer GET/HEAD."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    expected = session.get("csrf_token")
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRFToken")
    if not expected or not sent or not secrets.compare_digest(expected, sent):
        abort(400, description="Jeton CSRF invalide ou manquant. Recharge la page et réessaie.")


def current_user():
    """Équivalent maison de flask_login.current_user : None si non connecté."""
    username = session.get("octix_user")
    if not username:
        return None
    return User.query.filter_by(octix_username=username).first()


def login_required(view_func):
    """Bloque l'accès si personne n'est connecté (remplace @login_required)."""
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if "octix_user" not in session:
            flash("Connecte-toi avec ton compte Octix pour continuer.")
            return redirect(url_for("login_page", next=request.path))
        return view_func(*args, **kwargs)
    return wrapped


def owner_required(get_deck):
    """Décorateur paramétrable : vérifie que l'utilisateur connecté est bien
    le propriétaire du deck retourné par `get_deck(*args, **kwargs)`."""
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapped(*args, **kwargs):
            deck = get_deck(*args, **kwargs)
            u = current_user()
            if deck is None or u is None or deck.owner_id != u.id:
                flash("Tu n'as pas les droits pour modifier ce deck.")
                return redirect(url_for("explore"))
            return view_func(*args, **kwargs)
        return wrapped
    return decorator
