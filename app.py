"""
app.py — OmniaMind
Plateforme d'apprentissage communautaire type Quizlet.
Authentification : Octix (voir octix_auth.py). PAS de Flask-Login, PAS de
mot de passe stocké localement.
"""
import csv
import io
import os
import random
from datetime import datetime

from flask import (
    Flask, render_template, request, redirect, url_for, session, jsonify, flash, Response,
)

from models import (
    db, User, Deck, Card, CardProgress, Favorite, StudyLog,
    Achievement, BADGE_CATALOG, BADGE_BY_KEY, evaluate_and_unlock_achievements,
)
from octix_auth import (
    octix_login, get_or_create_local_profile, current_user, login_required,
    owner_required, OCTIX_PORTAL_URL, OCTIX_APP_NAME,
    DEMO_ENABLED, DEMO_USERNAME, DEMO_PASSWORD,
    csrf_token, csrf_protect,
)

app = Flask(__name__)

# --- Clé secrète : refuse de démarrer avec une valeur par défaut/placeholder
# hors développement (voir .env.example). En dev, lance avec FLASK_DEBUG=1
# pour utiliser la clé de secours locale.
_INSECURE_SECRET_VALUES = {"", "change_me_en_production", "omniamind_dev_secret_change_me"}
_secret_key = os.environ.get("SECRET_KEY", "")
_is_debug = os.environ.get("FLASK_DEBUG", "0") == "1"
if _secret_key in _INSECURE_SECRET_VALUES:
    if _is_debug:
        _secret_key = "omniamind_dev_secret_change_me"
    else:
        raise RuntimeError(
            "SECRET_KEY manquante ou par défaut : définis une vraie valeur aléatoire "
            "dans l'environnement (voir .env.example) avant de lancer l'app hors "
            "développement (ou passe FLASK_DEBUG=1 en local)."
        )
app.secret_key = _secret_key

app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///omniamind.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 Mo, confortable pour un import CSV

db.init_app(app)

with app.app_context():
    db.create_all()

app.before_request(csrf_protect)

CATEGORIES = ["Général", "Langues", "Sciences", "Histoire-Géo", "Maths", "Code & Informatique",
              "Droit & Économie", "Art & Culture", "Médecine", "Autre"]

XP_TABLE = {"flashcards": 8, "match": 15, "swipe": 8, "quiz": 12, "dictee": 10}

# Limites de longueur appliquées côté serveur (les colonnes SQL ont les
# mêmes bornes : ça évite un crash SQL type DataError sur un champ trop long).
MAX_LEN = {"title": 140, "description": 2000, "term": 500, "definition": 1000, "tags": 300}


def _clip(value, field):
    return (value or "").strip()[: MAX_LEN[field]]


# ----------------------------------------------------------------------
# Contexte global des templates
# ----------------------------------------------------------------------
@app.context_processor
def inject_globals():
    return {
        "app_name": OCTIX_APP_NAME,
        "octix_portal_url": OCTIX_PORTAL_URL,
        "current_user": current_user(),
        "categories": CATEGORIES,
        "demo_enabled": DEMO_ENABLED,
        "demo_username": DEMO_USERNAME,
        "demo_password": DEMO_PASSWORD,
        "csrf_token": csrf_token,
        "badge_catalog": BADGE_CATALOG,
        "pending_badges": [BADGE_BY_KEY[k] for k in session.pop("pending_badges", []) if k in BADGE_BY_KEY],
    }


def _deck_or_404(deck_id):
    return Deck.query.get(deck_id)


def _accessible_deck_or_none(deck_id, user):
    """Un deck est accessible s'il est public, si l'utilisateur en est le
    propriétaire, ou si l'utilisateur a validé un lien de partage pour ce
    deck précis pendant cette session (voir /d/<token>).

    À utiliser dans TOUTES les routes de révision/API qui prennent un
    deck_id — sans ce contrôle, n'importe quel utilisateur connecté peut
    accéder aux decks privés des autres en devinant l'id (IDOR)."""
    deck = Deck.query.get(deck_id)
    if deck is None:
        return None
    if deck.is_public or deck.owner_id == user.id:
        return deck
    if session.get(f"shared_deck_{deck.id}"):
        return deck
    return None


# ----------------------------------------------------------------------
# Auth (Octix)
# ----------------------------------------------------------------------
@app.route("/")
def index():
    if "octix_user" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login_page"))


@app.route("/login")
def login_page():
    if "octix_user" in session:
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/login", methods=["POST"])
def login():
    u = request.form.get("username", "").strip()
    p = request.form.get("password", "")
    if not u or not p:
        flash("Merci de renseigner ton identifiant et ton mot de passe Octix.")
        return redirect(url_for("login_page"))

    ok, result = octix_login(u, p)
    if not ok:
        flash(result)
        return redirect(url_for("login_page"))

    session["octix_user"] = u
    session["octix_token"] = result
    get_or_create_local_profile(u)

    next_url = request.args.get("next")
    return redirect(next_url or url_for("dashboard"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))


# ----------------------------------------------------------------------
# Dashboard / Profil
# ----------------------------------------------------------------------
DAILY_GOAL_SESSIONS = 3


@app.route("/dashboard")
@login_required
def dashboard():
    u = current_user()
    my_decks = u.decks.order_by(Deck.updated_at.desc()).limit(6).all()
    recent_logs = u.logs.order_by(StudyLog.created_at.desc()).limit(5).all()
    due_count = (
        db.session.query(CardProgress)
        .join(Card, CardProgress.card_id == Card.id)
        .join(Deck, Card.deck_id == Deck.id)
        .filter(CardProgress.user_id == u.id, Deck.owner_id == u.id,
                CardProgress.next_review_at <= datetime.utcnow())
        .count()
    )
    today_start = datetime.combine(datetime.utcnow().date(), datetime.min.time())
    sessions_today = u.logs.filter(StudyLog.created_at >= today_start).count()
    return render_template(
        "dashboard.html",
        my_decks=my_decks,
        recent_logs=recent_logs,
        due_count=due_count,
        lvl=u.level_data,
        avg_retention=u.average_retention(),
        sessions_today=min(sessions_today, DAILY_GOAL_SESSIONS),
        daily_goal=DAILY_GOAL_SESSIONS,
        goal_reached=sessions_today >= DAILY_GOAL_SESSIONS,
    )


@app.route("/profile")
@login_required
def profile():
    u = current_user()
    total_sessions = u.logs.count()
    mastered = (
        CardProgress.query.filter_by(user_id=u.id, status="maitrisee").count()
    )
    evaluate_and_unlock_achievements(u)  # rattrape les succès non encore détectés
    unlocked_keys = {a.key for a in Achievement.query.filter_by(user_id=u.id).all()}
    rank = User.query.filter(User.xp > u.xp).count() + 1
    return render_template(
        "profile.html",
        lvl=u.level_data,
        total_sessions=total_sessions,
        mastered=mastered,
        heatmap=u.heatmap_data(),
        decks=u.decks.order_by(Deck.created_at.desc()).all(),
        unlocked_keys=unlocked_keys,
        rank=rank,
    )


@app.route("/leaderboard")
@login_required
def leaderboard():
    u = current_user()
    top = User.query.order_by(User.xp.desc()).limit(20).all()
    rank = User.query.filter(User.xp > u.xp).count() + 1
    return render_template("leaderboard.html", top=top, rank=rank)


# ----------------------------------------------------------------------
# Explorer / Communauté
# ----------------------------------------------------------------------
@app.route("/explore")
@login_required
def explore():
    q = request.args.get("q", "").strip()
    cat = request.args.get("cat", "")
    query = Deck.query.filter_by(is_public=True)
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(
            Deck.title.ilike(like), Deck.description.ilike(like), Deck.tags.ilike(like),
        ))
    if cat:
        query = query.filter_by(category=cat)
    decks = query.order_by(Deck.created_at.desc()).limit(60).all()

    u = current_user()
    fav_ids = {f.deck_id for f in u.favorites}
    return render_template("explore.html", decks=decks, q=q, cat=cat, fav_ids=fav_ids)


@app.route("/favorite/<int:deck_id>/toggle", methods=["POST"])
@login_required
def toggle_favorite(deck_id):
    u = current_user()
    fav = Favorite.query.filter_by(user_id=u.id, deck_id=deck_id).first()
    if fav:
        db.session.delete(fav)
        db.session.commit()
        return jsonify(favorited=False)
    db.session.add(Favorite(user_id=u.id, deck_id=deck_id))
    db.session.commit()
    newly = evaluate_and_unlock_achievements(u)
    return jsonify(favorited=True, new_badges=[BADGE_BY_KEY[k] for k in newly])


# ----------------------------------------------------------------------
# Decks : CRUD
# ----------------------------------------------------------------------
def _safe_category(raw):
    return raw if raw in CATEGORIES else "Général"


def _parse_csv_cards(file_storage):
    """Parse un fichier CSV uploadé (colonnes term;definition ou
    term,definition, avec ou sans en-tête) et renvoie une liste de
    (term, definition) déjà nettoyés/tronqués. Ignore silencieusement les
    lignes incomplètes plutôt que de faire planter tout l'import."""
    if not file_storage or not file_storage.filename:
        return []
    raw = file_storage.read().decode("utf-8-sig", errors="ignore")
    try:
        dialect = csv.Sniffer().sniff(raw[:2048], delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(raw), dialect))
    pairs = []
    for row in rows:
        if len(row) < 2:
            continue
        t, d = _clip(row[0], "term"), _clip(row[1], "definition")
        if not t or not d:
            continue
        if t.lower() in ("term", "terme") and d.lower() in ("definition", "définition"):
            continue  # ligne d'en-tête
        pairs.append((t, d))
    return pairs


@app.route("/decks/new", methods=["GET", "POST"])
@login_required
def deck_new():
    if request.method == "GET":
        return render_template("deck_form.html", deck=None)

    u = current_user()
    title = _clip(request.form.get("title"), "title") or "Deck sans titre"
    deck = Deck(
        owner_id=u.id,
        title=title,
        description=_clip(request.form.get("description"), "description"),
        category=_safe_category(request.form.get("category", "Général")),
        tags=Deck.normalize_tags(request.form.get("tags", "")),
        is_public=bool(request.form.get("is_public")),
        cover_emoji=request.form.get("cover_emoji", "📚")[:4] or "📚",
    )
    db.session.add(deck)
    db.session.flush()

    terms = request.form.getlist("term[]")
    defs = request.form.getlist("definition[]")
    images = request.form.getlist("image_url[]")
    position = 0
    for i, (t, d) in enumerate(zip(terms, defs)):
        t, d = _clip(t, "term"), _clip(d, "definition")
        if t and d:
            img = (images[i].strip()[:500] if i < len(images) else "")
            db.session.add(Card(deck_id=deck.id, term=t, definition=d, image_url=img, position=position))
            position += 1

    for t, d in _parse_csv_cards(request.files.get("csv_file")):
        db.session.add(Card(deck_id=deck.id, term=t, definition=d, position=position))
        position += 1

    db.session.commit()
    newly = evaluate_and_unlock_achievements(u)
    flash("Deck créé avec succès !")
    if newly:
        session["pending_badges"] = newly
    return redirect(url_for("deck_detail", deck_id=deck.id))


@app.route("/decks/<int:deck_id>")
@login_required
def deck_detail(deck_id):
    u = current_user()
    deck = _accessible_deck_or_none(deck_id, u)
    if deck is None:
        flash("Ce deck n'existe pas ou n'est pas accessible.")
        return redirect(url_for("explore"))
    is_owner = deck.owner_id == u.id
    is_fav = Favorite.query.filter_by(user_id=u.id, deck_id=deck.id).first() is not None
    return render_template("deck_detail.html", deck=deck, is_owner=is_owner, is_fav=is_fav)


@app.route("/d/<token>")
@login_required
def deck_shared(token):
    """Point d'entrée d'un lien de partage : donne accès à un deck privé
    sans le rendre public, pour la durée de la session de l'utilisateur."""
    deck = Deck.query.filter_by(share_token=token).first()
    if deck is None:
        flash("Ce lien de partage est invalide.")
        return redirect(url_for("explore"))
    session[f"shared_deck_{deck.id}"] = True
    return redirect(url_for("deck_detail", deck_id=deck.id))


@app.route("/decks/<int:deck_id>/export.csv")
@login_required
def deck_export_csv(deck_id):
    deck = _accessible_deck_or_none(deck_id, current_user())
    if deck is None:
        return redirect(url_for("explore"))
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["term", "definition"])
    for c in deck.cards:
        writer.writerow([c.term, c.definition])
    filename = "".join(ch for ch in deck.title if ch.isalnum() or ch in " -_").strip() or "deck"
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'},
    )


@app.route("/decks/<int:deck_id>/edit", methods=["GET", "POST"])
@owner_required(lambda deck_id: _deck_or_404(deck_id))
def deck_edit(deck_id):
    deck = _deck_or_404(deck_id)
    if request.method == "GET":
        return render_template("deck_form.html", deck=deck)

    deck.title = _clip(request.form.get("title"), "title") or deck.title
    deck.description = _clip(request.form.get("description"), "description")
    deck.category = _safe_category(request.form.get("category", deck.category))
    deck.tags = Deck.normalize_tags(request.form.get("tags", deck.tags))
    deck.is_public = bool(request.form.get("is_public"))
    deck.cover_emoji = request.form.get("cover_emoji", deck.cover_emoji)[:4] or deck.cover_emoji

    # Cartes existantes (édition en place) — la suppression d'une carte passe
    # par l'endpoint AJAX dédié /cards/<id>/delete, pas par ce formulaire.
    ids = request.form.getlist("card_id[]")
    terms = request.form.getlist("term[]")
    defs = request.form.getlist("definition[]")
    images = request.form.getlist("image_url[]")
    next_position = deck.card_count
    for i, (cid, t, d) in enumerate(zip(ids, terms, defs)):
        t, d = _clip(t, "term"), _clip(d, "definition")
        if not t or not d:
            continue
        img = (images[i].strip()[:500] if i < len(images) else "")
        if cid:
            card = Card.query.get(int(cid))
            if card and card.deck_id == deck.id:
                card.term, card.definition, card.image_url = t, d, img
        else:
            db.session.add(Card(deck_id=deck.id, term=t, definition=d, image_url=img, position=next_position))
            next_position += 1

    for t, d in _parse_csv_cards(request.files.get("csv_file")):
        db.session.add(Card(deck_id=deck.id, term=t, definition=d, position=next_position))
        next_position += 1

    db.session.commit()
    newly = evaluate_and_unlock_achievements(current_user())
    flash("Deck mis à jour !")
    if newly:
        session["pending_badges"] = newly
    return redirect(url_for("deck_detail", deck_id=deck.id))


@app.route("/decks/<int:deck_id>/delete", methods=["POST"])
@owner_required(lambda deck_id: _deck_or_404(deck_id))
def deck_delete(deck_id):
    deck = _deck_or_404(deck_id)
    db.session.delete(deck)
    db.session.commit()
    flash("Deck supprimé.")
    return redirect(url_for("dashboard"))


@app.route("/cards/<int:card_id>/delete", methods=["POST"])
@login_required
def card_delete(card_id):
    card = Card.query.get(card_id)
    u = current_user()
    if card and card.deck.owner_id == u.id:
        db.session.delete(card)
        db.session.commit()
    return jsonify(ok=True)


# ----------------------------------------------------------------------
# Pages des modes d'étude
# ----------------------------------------------------------------------
@app.route("/decks/<int:deck_id>/study/<mode>")
@login_required
def study(deck_id, mode):
    deck = _accessible_deck_or_none(deck_id, current_user())
    if deck is None:
        flash("Ce deck n'existe pas ou n'est pas accessible.")
        return redirect(url_for("explore"))
    templates = {
        "flashcards": "study_flashcards.html",
        "match": "study_match.html",
        "swipe": "study_swipe.html",
        "quiz": "study_quiz.html",
        "dictee": "study_dictation.html",
    }
    tpl = templates.get(mode)
    if not tpl:
        return redirect(url_for("deck_detail", deck_id=deck_id))
    if deck.card_count == 0:
        flash("Ajoute au moins une carte à ce deck avant de réviser.")
        return redirect(url_for("deck_detail", deck_id=deck_id))
    return render_template(tpl, deck=deck)


# ----------------------------------------------------------------------
# API — données brutes pour les jeux (JSON)
# ----------------------------------------------------------------------
@app.route("/api/decks/<int:deck_id>/cards.json")
@login_required
def api_deck_cards(deck_id):
    deck = _accessible_deck_or_none(deck_id, current_user())
    if deck is None:
        return jsonify(error="not_found"), 404
    cards = [
        {"id": c.id, "term": c.term, "definition": c.definition, "image_url": c.image_url or ""}
        for c in deck.cards
    ]
    return jsonify(cards=cards)


@app.route("/api/decks/<int:deck_id>/srs_queue")
@login_required
def api_srs_queue(deck_id):
    """File de révision SRS : cartes dues en premier, puis cartes nouvelles."""
    u = current_user()
    deck = _accessible_deck_or_none(deck_id, u)
    if deck is None:
        return jsonify(error="not_found"), 404

    cards = deck.cards.all()
    progress_by_card = {
        p.card_id: p for p in CardProgress.query.filter(
            CardProgress.user_id == u.id, CardProgress.card_id.in_([c.id for c in cards])
        )
    }

    due, new = [], []
    for c in cards:
        p = progress_by_card.get(c.id)
        if p is None:
            new.append((c, None))
        elif p.is_due:
            due.append((c, p))

    random.shuffle(due)
    random.shuffle(new)
    ordered = due + new

    payload = []
    for c, p in ordered:
        payload.append({
            "card_id": c.id,
            "term": c.term,
            "definition": c.definition,
            "image_url": c.image_url or "",
            "status": p.status if p else "nouvelle",
            "retention": p.retention_pct if p else 0,
        })
    return jsonify(queue=payload, due_count=len(due), new_count=len(new))


@app.route("/api/cards/<int:card_id>/review", methods=["POST"])
@login_required
def api_review_card(card_id):
    u = current_user()
    card = Card.query.get(card_id)
    if card is None or _accessible_deck_or_none(card.deck_id, u) is None:
        return jsonify(error="not_found"), 404
    payload = request.get_json(silent=True) or {}
    difficulty = payload.get("difficulty", "moyen")

    progress = CardProgress.query.filter_by(user_id=u.id, card_id=card_id).first()
    if progress is None:
        progress = CardProgress(user_id=u.id, card_id=card_id)
        db.session.add(progress)
        db.session.flush()  # peuple les valeurs par défaut (ease_factor, times_seen...)
    progress.apply_review(difficulty)
    db.session.commit()
    return jsonify(
        status=progress.status,
        interval_days=progress.interval_days,
        retention=progress.retention_pct,
        next_review_at=progress.next_review_at.isoformat(),
    )


@app.route("/api/study/complete", methods=["POST"])
@login_required
def api_study_complete():
    """Appelé à la fin de n'importe quel mode : loggue la session, met à
    jour XP/streak, et indique si un level up vient d'avoir lieu."""
    data = request.get_json(silent=True) or {}
    u = current_user()
    deck_id = data.get("deck_id")
    mode = data.get("mode", "flashcards")
    try:
        score = float(data.get("score", 0))
    except (TypeError, ValueError):
        score = 0.0

    deck = _accessible_deck_or_none(deck_id, u) if deck_id is not None else None
    if deck is None:
        return jsonify(error="not_found"), 404

    old_level = u.level_data["level"]
    base_xp = XP_TABLE.get(mode, 8)
    bonus = int(base_xp * (score / 100)) if mode in ("quiz", "swipe") else base_xp
    xp_gained = max(base_xp // 2, bonus)

    u.register_activity(xp_gained=xp_gained)
    db.session.add(StudyLog(user_id=u.id, deck_id=deck.id, mode=mode, score=score, xp_gained=xp_gained))
    db.session.commit()
    newly = evaluate_and_unlock_achievements(u)

    new_level = u.level_data["level"]
    return jsonify(
        xp_gained=xp_gained,
        level_up=new_level > old_level,
        lvl=u.level_data,
        streak=u.streak_count,
        new_badges=[BADGE_BY_KEY[k] for k in newly],
    )


@app.route("/api/decks/<int:deck_id>/match_score", methods=["POST"])
@login_required
def api_match_score(deck_id):
    u = current_user()
    deck = _accessible_deck_or_none(deck_id, u)
    if deck is None:
        return jsonify(error="not_found"), 404
    payload = request.get_json(silent=True) or {}
    try:
        time_seconds = float(payload.get("time_seconds", 0))
    except (TypeError, ValueError):
        time_seconds = 0.0
    db.session.add(StudyLog(user_id=u.id, deck_id=deck.id, mode="match", score=time_seconds, xp_gained=0))
    db.session.commit()
    best = deck.best_match_time()
    return jsonify(best_time=best, is_new_best=(best is not None and time_seconds <= best))


if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "0") == "1"
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=debug_mode)