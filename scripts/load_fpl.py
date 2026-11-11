import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    URL,
    create_engine,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.schema import CreateSchema
from urllib3.util.retry import Retry


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIRECTORY = PROJECT_ROOT / "data" / "processed"
API_BASE = "https://fantasy.premierleague.com/api"

load_dotenv(PROJECT_ROOT / ".env")

SEASON = os.environ["FPL_SEASON"]

metadata = MetaData(schema="fpl")

dim_player = Table(
    "dim_player",
    metadata,
    Column("player_key", String, primary_key=True),
    Column("season", String, nullable=False),
    Column("player_id", Integer, nullable=False),
    Column("player_name", String, nullable=False),
    Column("position_id", Integer, nullable=False),
    Column("current_cost_million", Float, nullable=False),
    Column("availability_status", String, nullable=False),
    Column("availability_news", Text),
    Column("source_file", String, nullable=False),
)

dim_team = Table(
    "dim_team",
    metadata,
    Column("team_key", String, primary_key=True),
    Column("season", String, nullable=False),
    Column("team_id", Integer, nullable=False),
    Column("team_name", String, nullable=False),
    Column("team_short_name", String, nullable=False),
    Column("source_file", String, nullable=False),
)

dim_event = Table(
    "dim_event",
    metadata,
    Column("event_key", String, primary_key=True),
    Column("season", String, nullable=False),
    Column("event_id", Integer, nullable=False),
    Column("event_name", String, nullable=False),
    Column("deadline_time", DateTime(timezone=True)),
    Column("finished", Boolean, nullable=False),
    Column("is_current", Boolean, nullable=False),
    Column("is_next", Boolean, nullable=False),
    Column("source_file", String, nullable=False),
)

fact_player_match = Table(
    "fact_player_match",
    metadata,
    Column(
        "player_key",
        String,
        ForeignKey("fpl.dim_player.player_key"),
        primary_key=True,
    ),
    Column("fixture_id", Integer, primary_key=True),
    Column(
        "team_key",
        String,
        ForeignKey("fpl.dim_team.team_key"),
        nullable=False,
    ),
    Column(
        "event_key",
        String,
        ForeignKey("fpl.dim_event.event_key"),
        nullable=False,
    ),
    Column("kickoff_time", DateTime(timezone=True), nullable=False),
    Column("was_home", Boolean, nullable=False),
    Column("opponent_team_id", Integer, nullable=False),
    Column("total_points", Integer, nullable=False),
    Column("minutes", Integer, nullable=False),
    Column("cost_million_at_match", Float, nullable=False),
    Column("xg", Float, nullable=False),
    Column("xa", Float, nullable=False),
    Column("source_file", String, nullable=False),
    Column("extracted_at", DateTime(timezone=True), nullable=False),
)


def fetch_json(session, endpoint, output_path):
    response = session.get(f"{API_BASE}/{endpoint}", timeout=30)
    response.raise_for_status()
    payload = response.json()

    with output_path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)

    return payload


def upsert_rows(connection, table, rows):
    if not rows:
        return

    primary_keys = [column.name for column in table.primary_key.columns]

    # Limiter la taille de chaque requête SQL.
    for start in range(0, len(rows), 500):
        statement = insert(table).values(rows[start:start + 500])

        updates = {
            column.name: statement.excluded[column.name]
            for column in table.columns
            if column.name not in primary_keys
        }

        statement = statement.on_conflict_do_update(
            index_elements=primary_keys,
            set_=updates,
        )

        connection.execute(statement)


def main():
    players = pd.read_csv(
        PROCESSED_DIRECTORY / "players.csv",
        keep_default_na=False,
    )
    teams = pd.read_csv(
        PROCESSED_DIRECTORY / "teams.csv",
        keep_default_na=False,
    )
    events = pd.read_csv(
        PROCESSED_DIRECTORY / "events.csv",
        keep_default_na=False,
    )

    # Les trois fichiers doivent provenir du même instantané.
    sources = pd.concat(
        [players["source_file"], teams["source_file"], events["source_file"]]
    ).unique()

    if len(sources) != 1:
        raise ValueError("Les CSV proviennent de plusieurs extractions.")

    players["player_key"] = SEASON + ":" + players["player_id"].astype(str)
    players["season"] = SEASON
    players = players.rename(
        columns={"cost_million": "current_cost_million"}
    )

    teams["team_key"] = SEASON + ":" + teams["team_id"].astype(str)
    teams["season"] = SEASON

    events["event_key"] = SEASON + ":" + events["event_id"].astype(str)
    events["season"] = SEASON
    events["deadline_time"] = pd.to_datetime(
        events["deadline_time"], utc=True, errors="raise"
    )

    valid_event_ids = set(events["event_id"])
    valid_team_ids = set(teams["team_id"])

    run_started_at = datetime.now(timezone.utc)
    run_name = run_started_at.strftime("%Y%m%dT%H%M%S_%fZ")
    raw_directory = PROJECT_ROOT / "data" / "raw" / f"history_{run_name}"
    raw_directory.mkdir(parents=True, exist_ok=True)

    retry_policy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        respect_retry_after_header=True,
    )

    performance_rows = []

    with requests.Session() as session:
        session.mount(
            "https://",
            HTTPAdapter(max_retries=retry_policy),
        )

        fixtures = fetch_json(
            session,
            "fixtures/",
            raw_directory / "fixtures.json",
        )
        fixtures_by_id = {fixture["id"]: fixture for fixture in fixtures}

        for index, player in enumerate(
            players.to_dict(orient="records"), start=1
        ):
            player_id = int(player["player_id"])
            history_path = raw_directory / f"player_{player_id}.json"

            payload = fetch_json(
                session,
                f"element-summary/{player_id}/",
                history_path,
            )
            extracted_at = datetime.now(timezone.utc)

            for match in payload["history"]:
                event_id = match["round"]
                fixture = fixtures_by_id[match["fixture"]]

                # Retrouver l'équipe du joueur au moment du match.
                team_id = (
                    fixture["team_h"]
                    if match["was_home"]
                    else fixture["team_a"]
                )

                if event_id not in valid_event_ids:
                    raise ValueError(f"Journée inconnue : {event_id}")

                if team_id not in valid_team_ids:
                    raise ValueError(f"Équipe inconnue : {team_id}")

                performance_rows.append(
                    {
                        "player_key": player["player_key"],
                        "fixture_id": int(match["fixture"]),
                        "team_key": f"{SEASON}:{team_id}",
                        "event_key": f"{SEASON}:{event_id}",
                        "kickoff_time": datetime.fromisoformat(
                            match["kickoff_time"].replace("Z", "+00:00")
                        ),
                        "was_home": match["was_home"],
                        "opponent_team_id": int(match["opponent_team"]),
                        "total_points": int(match["total_points"]),
                        "minutes": int(match["minutes"]),
                        "cost_million_at_match": float(match["value"]) / 10,
                        "xg": float(match["expected_goals"]),
                        "xa": float(match["expected_assists"]),
                        "source_file": str(
                            history_path.relative_to(PROJECT_ROOT)
                        ),
                        "extracted_at": extracted_at,
                    }
                )

            if index % 50 == 0:
                print(f"Historiques récupérés : {index}/{len(players)}")

            time.sleep(0.3)

    database_url = URL.create(
        drivername="postgresql+psycopg2",
        username=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        host="127.0.0.1",
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        database=os.environ["POSTGRES_DB"],
    )

    engine = create_engine(database_url, pool_pre_ping=True)

    try:
        with engine.begin() as connection:
            connection.execute(CreateSchema("fpl", if_not_exists=True))
            metadata.create_all(connection)

            for table, dataframe in (
                (dim_team, teams),
                (dim_event, events),
                (dim_player, players),
            ):
                # Charger uniquement les colonnes prévues par la table.
                column_names = [column.name for column in table.columns]
                records = dataframe[column_names].to_dict(orient="records")
                upsert_rows(connection, table, records)

            upsert_rows(connection, fact_player_match, performance_rows)
    finally:
        engine.dispose()

    print(f"Saison chargée : {SEASON}")
    print(f"Joueurs traités : {len(players)}")
    print(f"Équipes traitées : {len(teams)}")
    print(f"Journées traitées : {len(events)}")
    print(f"Performances traitées : {len(performance_rows)}")
    print("Transaction PostgreSQL validée.")


if __name__ == "__main__":
    main()