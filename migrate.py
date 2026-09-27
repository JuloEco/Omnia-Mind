"""
migrate_v2.py — Met à niveau une base OmniaMind existante (créée avant les
colonnes `tags`, `share_token`, `image_url`, `lapses_in_a_row`,
`consecutive_easy`) sans perdre de données.

`db.create_all()` ne modifie JAMAIS une table déjà existante : il ne crée
que les tables absentes. Comme les tables omniamind_* existaient déjà chez
toi, les nouvelles colonnes n'ont jamais été ajoutées, d'où l'erreur
`UndefinedColumn: column omniamind_decks.tags does not exist`.

Utilisation (une seule fois, avec le même DATABASE_URL que l'app) :

    python migrate_v2.py

Fonctionne avec SQLite et PostgreSQL (Render). Ré-exécutable sans risque :
chaque colonne/index n'est ajouté que s'il manque encore.
"""
import secrets

from sqlalchemy import inspect, text

from app import app
from models import db, Deck


def _existing_columns(inspector, table):
    return {c["name"] for c in inspector.get_columns(table)}


def _existing_index_names(inspector, table):
    return {i["name"] for i in inspector.get_indexes(table)} | {
        i["name"] for i in inspector.get_unique_constraints(table)
    }


# Colonnes à ajouter : (table, colonne, définition SQL)
COLUMNS_TO_ADD = [
    ("omniamind_decks", "tags", "VARCHAR(300) DEFAULT ''"),
    ("omniamind_decks", "share_token", "VARCHAR(32)"),
    ("omniamind_cards", "image_url", "VARCHAR(500) DEFAULT ''"),
    ("omniamind_card_progress", "lapses_in_a_row", "INTEGER DEFAULT 0"),
    ("omniamind_card_progress", "consecutive_easy", "INTEGER DEFAULT 0"),
    ("omniamind_decks", "language", "VARCHAR(5) DEFAULT 'auto'"),
]


def run():
    with app.app_context():
        # 1) Crée les tables qui n'existeraient pas encore du tout
        #    (nouvelle installation partielle, sans toucher aux tables déjà là).
        db.create_all()

        inspector = inspect(db.engine)
        existing_tables = set(inspector.get_table_names())

        with db.engine.begin() as conn:
            for table, column, sql_type in COLUMNS_TO_ADD:
                if table not in existing_tables:
                    continue  # la table vient d'être créée par create_all(), déjà à jour
                cols = _existing_columns(inspector, table)
                if column in cols:
                    print(f"= {table}.{column} déjà présente, on passe.")
                    continue
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))
                print(f"+ {table}.{column} ajoutée.")

        # 2) Index unique sur share_token (nécessaire pour /d/<token>)
        inspector = inspect(db.engine)  # ré-inspecte après les ALTER TABLE
        if "omniamind_decks" in existing_tables:
            index_names = _existing_index_names(inspector, "omniamind_decks")
            if "ix_omniamind_decks_share_token" not in index_names:
                with db.engine.begin() as conn:
                    conn.execute(text(
                        "CREATE UNIQUE INDEX ix_omniamind_decks_share_token "
                        "ON omniamind_decks (share_token)"
                    ))
                print("+ index unique sur omniamind_decks.share_token créé.")

            # index de perf sur (is_public, created_at) et category — optionnels,
            # on les ajoute s'ils manquent mais une absence ne bloque rien.
            if "ix_omniamind_decks_public_created" not in index_names:
                with db.engine.begin() as conn:
                    conn.execute(text(
                        "CREATE INDEX ix_omniamind_decks_public_created "
                        "ON omniamind_decks (is_public, created_at)"
                    ))
                print("+ index de perf (is_public, created_at) créé.")
            if "ix_omniamind_decks_category" not in index_names:
                with db.engine.begin() as conn:
                    conn.execute(text(
                        "CREATE INDEX ix_omniamind_decks_category ON omniamind_decks (category)"
                    ))
                print("+ index de perf (category) créé.")

        # 3) Backfill : les decks déjà existants n'ont pas de share_token
        #    (colonne ajoutée sans valeur) → on leur en génère un, sinon le
        #    bouton "Lien de partage" produirait une URL /d/None.
        decks_sans_token = Deck.query.filter(
            db.or_(Deck.share_token.is_(None), Deck.share_token == "")
        ).all()
        for d in decks_sans_token:
            d.share_token = secrets.token_urlsafe(16)
        if decks_sans_token:
            db.session.commit()
            print(f"+ share_token généré pour {len(decks_sans_token)} deck(s) existant(s).")

        print("Migration terminée avec succès.")


if __name__ == "__main__":
    run()