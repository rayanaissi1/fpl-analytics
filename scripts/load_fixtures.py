import json
import os

from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Integer,
    String,
    Table,
    URL,
    create_engine,
    delete,
    select,
)

from load_fpl import PROJECT_ROOT, SEASON, dim_team, metadata


fact_team_fixture = Table(
    "fact_team_fixture",
    metadata,
    Column("season", String, nullable=False),
    Column("fixture_id", Integer, primary_key=True),
    Column(
        "team_key",
        String,
        ForeignKey("fpl.dim_team.team_key"),
        primary_key=True,
    ),
    Column(
        "event_key",
        String,
        ForeignKey("fpl.dim_event.event_key"),
        nullable=False,
    ),
    Column("opponent_short_name", String, nullable=False),
    Column("is_home", Boolean, nullable=False),
    Column("fdr", Integer, nullable=False),
)


def main():
    raw_directory = PROJECT_ROOT / "data" / "raw"
    fixture_files = sorted(raw_directory.glob("history_*/fixtures.json"))

    if not fixture_files:
        raise FileNotFoundError(
            "Aucun fixtures.json trouvé. Exécute d'abord load_fpl.py."
        )

    source_path = fixture_files[-1]

    with source_path.open(encoding="utf-8") as file:
        fixtures = json.load(file)

    database_url = URL.create(
        "postgresql+psycopg2",
        username=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        host="127.0.0.1",
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        database=os.environ["POSTGRES_DB"],
    )
    engine = create_engine(database_url)

    try:
        with engine.begin() as connection:
            team_names = dict(
                connection.execute(
                    select(dim_team.c.team_key, dim_team.c.team_short_name)
                    .where(dim_team.c.season == SEASON)
                ).all()
            )

            rows = []

            for fixture in fixtures:
                event_id = fixture["event"]

                # Une rencontre sans journée attribuée est hors calendrier.
                if event_id is None:
                    continue

                perspectives = (
                    (
                        fixture["team_h"],
                        fixture["team_a"],
                        True,
                        fixture["team_h_difficulty"],
                    ),
                    (
                        fixture["team_a"],
                        fixture["team_h"],
                        False,
                        fixture["team_a_difficulty"],
                    ),
                )

                for team_id, opponent_id, is_home, difficulty in perspectives:
                    if difficulty not in (1, 2, 3, 4, 5):
                        raise ValueError(
                            f"FDR invalide pour le match {fixture['id']}"
                        )

                    rows.append(
                        {
                            "season": SEASON,
                            "fixture_id": fixture["id"],
                            "team_key": f"{SEASON}:{team_id}",
                            "event_key": f"{SEASON}:{event_id}",
                            "opponent_short_name": team_names[
                                f"{SEASON}:{opponent_id}"
                            ],
                            "is_home": is_home,
                            "fdr": difficulty,
                        }
                    )

            if not rows:
                raise ValueError("Aucune rencontre avec une journée attribuée.")

            fact_team_fixture.create(connection, checkfirst=True)

            connection.execute(
                delete(fact_team_fixture)
                .where(fact_team_fixture.c.season == SEASON)
            )
            connection.execute(fact_team_fixture.insert(), rows)
    finally:
        engine.dispose()

    print(f"Source : {source_path}")
    print(f"Saison : {SEASON}")
    print(f"Matchs chargés : {len(rows) // 2}")
    print(f"Lignes équipe-match : {len(rows)}")


if __name__ == "__main__":
    main()