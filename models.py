"""
models.py — Schémas SQLAlchemy pour OmniaMind

Important : il n'y a PAS de mot de passe stocké ici. L'authentification est
entièrement déléguée au service Octix (voir octix_auth.py). Ce fichier ne
contient qu'un profil applicatif local, indexé par le pseudo Octix
(`octix_username`), qui est créé automatiquement à la première connexion.
"""
from datetime import datetime, date, timedelta
import math
import secrets

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def _new_share_token():
    return secrets.token_urlsafe(16)

# ----------------------------------------------------------------------
# Constantes SRS (Système de Révision Espacée)
# ----------------------------------------------------------------------
DIFFICULTY_EASY = "facile"
DIFFICULTY_MEDIUM = "moyen"
DIFFICULTY_HARD = "a_revoir"

# Plafond de l'intervalle SRS, même pour une carte maîtrisée avec un ease
# factor élevé. Sans ce plafond, une carte "facile" plusieurs fois de suite
# peut voir son intervalle grimper à plusieurs mois voire années et ne
# jamais redevenir "due" en pratique : la mémoire s'effondre en silence.
# 90 jours force une "piqûre de rappel" de consolidation périodique, même
# sur les cartes déjà maîtrisées.
MAX_INTERVAL_DAYS = 90.0

STATUS_NEW = "nouvelle"
STATUS_LEARNING = "apprentissage"
STATUS_REVIEW = "revision"
STATUS_MASTERED = "maitrisee"


class User(db.Model):
    """Profil applicatif local. Une ligne = un pseudo Octix ayant déjà
    utilisé OmniaMind au moins une fois. Aucune donnée d'authentification
    (mot de passe, hash...) n'est stockée ici : tout passe par Octix."""
    __tablename__ = "omniamind_users"

    id = db.Column(db.Integer, primary_key=True)
    octix_username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    display_name = db.Column(db.String(80), nullable=False)
    avatar_emoji = db.Column(db.String(8), default="🦉")
    xp = db.Column(db.Integer, default=0, nullable=False)
    streak_count = db.Column(db.Integer, default=0, nullable=False)
    last_active_date = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    decks = db.relationship("Deck", backref="owner", lazy="dynamic", cascade="all, delete-orphan")
    progresses = db.relationship("CardProgress", backref="user", lazy="dynamic", cascade="all, delete-orphan")
    favorites = db.relationship("Favorite", backref="user", lazy="dynamic", cascade="all, delete-orphan")
    logs = db.relationship("StudyLog", backref="user", lazy="dynamic", cascade="all, delete-orphan")

    # --- Niveau / XP ---
    @property
    def level_data(self):
        level = (self.xp // 100) + 1
        progress = self.xp % 100
        return {"level": level, "progress": progress, "next": 100}

    def register_activity(self, xp_gained=0):
        """Met à jour l'XP et la série de jours (streak) lors d'une session d'étude."""
        today = date.today()
        if self.last_active_date is None:
            self.streak_count = 1
        elif self.last_active_date == today:
            pass  # déjà actif aujourd'hui, on ne recompte pas le streak
        elif self.last_active_date == today - timedelta(days=1):
            self.streak_count += 1
        else:
            self.streak_count = 1
        self.last_active_date = today
        self.xp += max(0, xp_gained)

    def heatmap_data(self, days=119):
        """Retourne {date_iso: nb_sessions} pour les `days` derniers jours."""
        since = date.today() - timedelta(days=days)
        rows = (
            db.session.query(StudyLog.created_at)
            .filter(StudyLog.user_id == self.id, StudyLog.created_at >= datetime.combine(since, datetime.min.time()))
            .all()
        )
        counts = {}
        for (dt,) in rows:
            k = dt.date().isoformat()
            counts[k] = counts.get(k, 0) + 1
        return counts

    def average_retention(self):
        """Rétention moyenne (%) sur toutes les cartes déjà révisées au
        moins une fois par cet utilisateur. Renvoie None si aucune carte
        n'a encore été révisée (évite d'afficher un 0% trompeur)."""
        progresses = (
            CardProgress.query
            .filter(CardProgress.user_id == self.id, CardProgress.last_reviewed_at.isnot(None))
            .all()
        )
        if not progresses:
            return None
        return round(sum(p.retention_pct for p in progresses) / len(progresses))


class Deck(db.Model):
    __tablename__ = "omniamind_decks"
    __table_args__ = (
        # Accélère la requête d'Explorer (filtre is_public, tri par date).
        db.Index("ix_omniamind_decks_public_created", "is_public", "created_at"),
        db.Index("ix_omniamind_decks_category", "category"),
    )

    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("omniamind_users.id"), nullable=False)
    title = db.Column(db.String(140), nullable=False)
    description = db.Column(db.Text, default="")
    category = db.Column(db.String(60), default="Général")
    tags = db.Column(db.String(300), default="")  # liste de tags séparés par des virgules
    is_public = db.Column(db.Boolean, default=False, nullable=False)
    cover_emoji = db.Column(db.String(8), default="📚")
    # Langue forcée pour la synthèse vocale (Dictée, Podcast) : "auto" pour
    # une détection automatique par carte, ou un code fixe (fr/en/es/de/it)
    # si tout le deck est dans une seule langue et que la détection se trompe.
    language = db.Column(db.String(5), default="auto", nullable=False)
    share_token = db.Column(db.String(32), unique=True, index=True, default=_new_share_token)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    cards = db.relationship("Card", backref="deck", lazy="dynamic",
                             order_by="Card.position", cascade="all, delete-orphan")
    favorited_by = db.relationship("Favorite", backref="deck", lazy="dynamic", cascade="all, delete-orphan")
    logs = db.relationship("StudyLog", backref="deck", lazy="dynamic", cascade="all, delete-orphan")

    @property
    def card_count(self):
        return self.cards.count()

    @property
    def favorite_count(self):
        return self.favorited_by.count()

    @property
    def tag_list(self):
        return [t.strip() for t in (self.tags or "").split(",") if t.strip()]

    @staticmethod
    def normalize_tags(raw):
        """Nettoie une saisie libre 'tag1, Tag2,, tag1' -> 'tag1,tag2'."""
        seen, out = set(), []
        for t in (raw or "").split(","):
            t = t.strip().lower()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
        return ",".join(out[:15])  # limite raisonnable

    def best_match_time(self):
        best = (
            db.session.query(db.func.min(StudyLog.score))
            .filter(StudyLog.deck_id == self.id, StudyLog.mode == "match")
            .scalar()
        )
        return best

    def to_export_dict(self):
        return {
            "title": self.title,
            "description": self.description,
            "category": self.category,
            "cards": [{"term": c.term, "definition": c.definition} for c in self.cards],
        }


class Card(db.Model):
    __tablename__ = "omniamind_cards"

    id = db.Column(db.Integer, primary_key=True)
    deck_id = db.Column(db.Integer, db.ForeignKey("omniamind_decks.id"), nullable=False)
    term = db.Column(db.String(500), nullable=False)
    definition = db.Column(db.String(1000), nullable=False)
    image_url = db.Column(db.String(500), default="")
    position = db.Column(db.Integer, default=0)

    progresses = db.relationship("CardProgress", backref="card", lazy="dynamic", cascade="all, delete-orphan")


class CardProgress(db.Model):
    """Une ligne par (utilisateur, carte) : c'est le cœur du moteur SRS."""
    __tablename__ = "omniamind_card_progress"
    __table_args__ = (db.UniqueConstraint("user_id", "card_id", name="uq_user_card"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("omniamind_users.id"), nullable=False)
    card_id = db.Column(db.Integer, db.ForeignKey("omniamind_cards.id"), nullable=False)

    status = db.Column(db.String(20), default=STATUS_NEW, nullable=False)
    ease_factor = db.Column(db.Float, default=2.3, nullable=False)
    interval_days = db.Column(db.Float, default=0.0, nullable=False)
    times_seen = db.Column(db.Integer, default=0, nullable=False)
    times_correct = db.Column(db.Integer, default=0, nullable=False)
    # Nombre d'échecs ("à revoir") consécutifs depuis la dernière réussite —
    # sert à pénaliser progressivement une carte qui échoue en boucle plutôt
    # que de toujours la remettre à ~30 min peu importe l'historique.
    lapses_in_a_row = db.Column(db.Integer, default=0, nullable=False)
    # Nombre de "facile" consécutifs — la maîtrise exige 2 réussites de
    # suite avec un intervalle suffisant, pas une seule réponse chanceuse.
    consecutive_easy = db.Column(db.Integer, default=0, nullable=False)
    last_reviewed_at = db.Column(db.DateTime, nullable=True)
    next_review_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def apply_review(self, difficulty):
        """Met à jour l'état SRS selon la difficulté choisie par l'utilisateur."""
        now = datetime.utcnow()
        self.times_seen += 1
        self.last_reviewed_at = now

        if difficulty == DIFFICULTY_HARD:
            self.lapses_in_a_row += 1
            self.consecutive_easy = 0
            self.ease_factor = max(1.3, self.ease_factor - 0.25)
            # Chaque échec consécutif retarde un peu plus le retour (30 min,
            # puis 45, 60...), plafonné à 2h pour rester dans la session.
            self.interval_days = min(0.083, 0.02 + 0.015 * (self.lapses_in_a_row - 1))
            self.status = STATUS_LEARNING
        elif difficulty == DIFFICULTY_MEDIUM:
            self.times_correct += 1
            self.lapses_in_a_row = 0
            self.consecutive_easy = 0
            self.ease_factor = max(1.3, self.ease_factor - 0.02)
            base = self.interval_days if self.interval_days >= 1 else 1
            self.interval_days = min(MAX_INTERVAL_DAYS, round(base * 1.35, 2))
            self.status = STATUS_REVIEW
        else:  # facile
            self.times_correct += 1
            self.lapses_in_a_row = 0
            self.consecutive_easy += 1
            self.ease_factor = min(3.2, self.ease_factor + 0.15)
            base = self.interval_days if self.interval_days >= 1 else 1
            self.interval_days = min(MAX_INTERVAL_DAYS, round(base * self.ease_factor, 2))
            mastered = self.interval_days >= 21 and self.consecutive_easy >= 2
            self.status = STATUS_MASTERED if mastered else STATUS_REVIEW

        self.next_review_at = now + timedelta(days=self.interval_days)

    @property
    def retention_pct(self):
        """Courbe de l'oubli simplifiée : 100% juste après révision, décroît
        exponentiellement vers l'échéance next_review_at."""
        if self.last_reviewed_at is None:
            return 0
        now = datetime.utcnow()
        elapsed_days = max(0.0, (now - self.last_reviewed_at).total_seconds() / 86400)
        half_life = max(0.5, self.interval_days)
        pct = 100 * math.exp(-elapsed_days / (half_life * 1.44))
        return max(0, min(100, round(pct)))

    @property
    def is_due(self):
        return self.next_review_at <= datetime.utcnow()


class Achievement(db.Model):
    """Un succès débloqué par un utilisateur. Le catalogue des succès
    possibles (clé -> libellé/description/icône) vit dans BADGE_CATALOG
    ci-dessous ; cette table ne stocke que les clés déjà obtenues."""
    __tablename__ = "omniamind_achievements"
    __table_args__ = (db.UniqueConstraint("user_id", "key", name="uq_user_achievement"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("omniamind_users.id"), nullable=False)
    key = db.Column(db.String(40), nullable=False)
    unlocked_at = db.Column(db.DateTime, default=datetime.utcnow)


# ----------------------------------------------------------------------
# Succès (badges) — apprentissage ludique
# ----------------------------------------------------------------------
BADGE_CATALOG = [
    {"key": "first_deck", "label": "Premier pas", "desc": "Crée ton premier deck", "icon": "sprout"},
    {"key": "first_card", "label": "Collectionneur", "desc": "Ajoute ta première carte", "icon": "layers"},
    {"key": "streak_3", "label": "Sur la lancée", "desc": "3 jours de série d'affilée", "icon": "flame"},
    {"key": "streak_7", "label": "Flamme ardente", "desc": "7 jours de série d'affilée", "icon": "flame"},
    {"key": "streak_30", "label": "Inarrêtable", "desc": "30 jours de série d'affilée", "icon": "flame"},
    {"key": "level_5", "label": "Apprenti confirmé", "desc": "Atteins le niveau 5", "icon": "star"},
    {"key": "level_10", "label": "Expert", "desc": "Atteins le niveau 10", "icon": "gem"},
    {"key": "mastered_10", "label": "Mémoire d'éléphant", "desc": "Maîtrise 10 cartes", "icon": "brain"},
    {"key": "mastered_50", "label": "Encyclopédie vivante", "desc": "Maîtrise 50 cartes", "icon": "graduation-cap"},
    {"key": "sessions_25", "label": "Marathonien", "desc": "Termine 25 sessions de révision", "icon": "medal"},
    {"key": "public_deck", "label": "Partageur", "desc": "Publie un deck dans la communauté", "icon": "share-2"},
    {"key": "favorites_5", "label": "Curieux", "desc": "Ajoute 5 decks en favoris", "icon": "heart"},
]
BADGE_BY_KEY = {b["key"]: b for b in BADGE_CATALOG}


def evaluate_and_unlock_achievements(user):
    """Vérifie les critères de tous les succès et débloque ceux qui
    viennent d'être atteints. Renvoie la liste des clés nouvellement
    débloquées (pour afficher un toast/overlay côté client)."""
    already = {a.key for a in Achievement.query.filter_by(user_id=user.id).all()}
    mastered = CardProgress.query.filter_by(user_id=user.id, status=STATUS_MASTERED).count()
    deck_count = user.decks.count()
    card_count = sum(d.card_count for d in user.decks)
    level = user.level_data["level"]

    checks = {
        "first_deck": deck_count >= 1,
        "first_card": card_count >= 1,
        "streak_3": user.streak_count >= 3,
        "streak_7": user.streak_count >= 7,
        "streak_30": user.streak_count >= 30,
        "level_5": level >= 5,
        "level_10": level >= 10,
        "mastered_10": mastered >= 10,
        "mastered_50": mastered >= 50,
        "sessions_25": user.logs.count() >= 25,
        "public_deck": user.decks.filter_by(is_public=True).count() >= 1,
        "favorites_5": user.favorites.count() >= 5,
    }
    newly = [key for key, ok in checks.items() if ok and key not in already]
    for key in newly:
        db.session.add(Achievement(user_id=user.id, key=key))
    if newly:
        db.session.commit()
    return newly


class Favorite(db.Model):
    __tablename__ = "omniamind_favorites"
    __table_args__ = (db.UniqueConstraint("user_id", "deck_id", name="uq_user_deck_fav"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("omniamind_users.id"), nullable=False)
    deck_id = db.Column(db.Integer, db.ForeignKey("omniamind_decks.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class StudyLog(db.Model):
    """Une ligne par session d'étude terminée : sert au streak, à la heatmap,
    au résumé de fin de session et au classement du mode Match."""
    __tablename__ = "omniamind_study_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("omniamind_users.id"), nullable=False)
    deck_id = db.Column(db.Integer, db.ForeignKey("omniamind_decks.id"), nullable=False)
    mode = db.Column(db.String(20), nullable=False)  # flashcards | match | swipe | quiz | dictee | focus
    score = db.Column(db.Float, default=0)  # % pour quiz/swipe, secondes pour match
    xp_gained = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)