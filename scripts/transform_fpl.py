import json
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIRECTORY = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIRECTORY = PROJECT_ROOT / "data" / "processed"

PLAYER_COLUMNS = {
    "id": "player_id",
    "web_name": "player_name",
    "team": "team_id",
    "element_type": "position_id",
    "now_cost": "cost_million",
    "total_points": "total_points",
    "expected_goals": "xg",
    "expected_assists": "xa",
    "minutes": "minutes",
    "status": "availability_status",
    "news": "availability_news",
}


def main():
    # Les noms horodatés de l'étape 2 permettent ce tri chronologique.
    raw_files = sorted(RAW_DIRECTORY.glob("bootstrap_static_*.json"))

    if not raw_files:
        raise FileNotFoundError(
            "Aucun fichier brut trouvé. Lance d'abord extract_fpl.py."
        )

    source_path = raw_files[-1]

    with source_path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    # Transformer les trois collections en tableaux Pandas.
    players = pd.DataFrame(data["elements"])
    teams = pd.DataFrame(data["teams"])
    events = pd.DataFrame(data["events"])

    if players.empty or teams.empty or events.empty:
        raise ValueError("Une des trois collections est vide.")

    # Sélectionner et renommer les colonnes des joueurs.
    players = players[list(PLAYER_COLUMNS)].rename(columns=PLAYER_COLUMNS)

    integer_columns = [
        "player_id",
        "team_id",
        "position_id",
        "total_points",
        "minutes",
    ]

    for column in integer_columns:
        players[column] = pd.to_numeric(
            players[column], errors="raise"
        ).astype("int64")

    for column in ["cost_million", "xg", "xa"]:
        players[column] = pd.to_numeric(
            players[column], errors="raise"
        ).astype("float64")

    # Vérifier les données avant de calculer les métriques.
    if players["player_id"].duplicated().any():
        raise ValueError("Plusieurs joueurs ont le même player_id.")

    if players[["cost_million", "xg", "xa"]].isna().any().any():
        raise ValueError("Une valeur numérique nécessaire est manquante.")

    if (players["cost_million"] <= 0).any():
        raise ValueError("Le prix d'un joueur est nul ou négatif.")

    if (players["minutes"] < 0).any():
        raise ValueError("Les minutes jouées doivent être positives ou nulles.")

    # Un prix FPL de 65 correspond à 6,5 millions.
    players["cost_million"] = players["cost_million"] / 10

    players["player_name"] = (
        players["player_name"].astype("string").str.strip()
    )
    players["availability_status"] = (
        players["availability_status"].astype("string").str.strip()
    )
    players["availability_news"] = (
        players["availability_news"].fillna("").astype("string").str.strip()
    )

    # Préparer les équipes.
    teams = teams[["id", "name", "short_name"]].rename(
        columns={
            "id": "team_id",
            "name": "team_name",
            "short_name": "team_short_name",
        }
    )

    teams["team_id"] = pd.to_numeric(
        teams["team_id"], errors="raise"
    ).astype("int64")

    # Vérifier que chaque joueur appartient à une équipe connue.
    if not players["team_id"].isin(teams["team_id"]).all():
        raise ValueError("Un joueur référence une équipe inconnue.")

    if teams["team_id"].duplicated().any():
        raise ValueError("Plusieurs équipes ont le même team_id.")

    # Préparer le calendrier des journées FPL.
    events = events[
        ["id", "name", "deadline_time", "finished", "is_current", "is_next"]
    ].rename(
        columns={
            "id": "event_id",
            "name": "event_name",
        }
    )

    events["event_id"] = pd.to_numeric(
        events["event_id"], errors="raise"
    ).astype("int64")

    events["deadline_time"] = pd.to_datetime(
        events["deadline_time"], utc=True, errors="raise"
    )

    if events["event_id"].duplicated().any():
        raise ValueError("Plusieurs journées ont le même event_id.")

    # Calculer les métriques sans arrondir les données stockées.
    players["points_per_million"] = (
        players["total_points"] / players["cost_million"]
    )
    players["expected_goal_involvements"] = players["xg"] + players["xa"]

    # Garder la provenance de chaque ligne.
    for dataframe in (players, teams, events):
        dataframe["source_file"] = source_path.name

    PROCESSED_DIRECTORY.mkdir(parents=True, exist_ok=True)

    players.to_csv(PROCESSED_DIRECTORY / "players.csv", index=False)
    teams.to_csv(PROCESSED_DIRECTORY / "teams.csv", index=False)
    events.to_csv(PROCESSED_DIRECTORY / "events.csv", index=False)

    print(f"Source utilisée : {source_path.name}")
    print(f"Joueurs préparés : {len(players)}")
    print(f"Équipes préparées : {len(teams)}")
    print(f"Journées préparées : {len(events)}")

    preview_columns = [
        "player_name",
        "cost_million",
        "total_points",
        "minutes",
        "points_per_million",
    ]

    print("\nTop 10 par points par million :")
    print(
        players.sort_values(
            "points_per_million", ascending=False
        )[preview_columns]
        .head(10)
        .round(2)
        .to_string(index=False)
    )

    print(f"\nFichiers sauvegardés dans : {PROCESSED_DIRECTORY}")


if __name__ == "__main__":
    main()