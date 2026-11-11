import json
from datetime import datetime, timezone
from pathlib import Path

import requests


API_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"

# Retrouver la racine du projet à partir du fichier du script
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIRECTORY = PROJECT_ROOT / "data" / "raw"


def main():
    print("Récupération des données FPL...")

    response = requests.get(API_URL, timeout=30)
    response.raise_for_status()

    data = response.json()

    # Vérifier les trois collections nécessaires au projet
    if not isinstance(data, dict):
        raise ValueError("La réponse FPL doit être un objet JSON.")

    for key in ("elements", "teams", "events"):
        if key not in data or not isinstance(data[key], list):
            raise ValueError(f"Collection absente ou invalide : {key}")

    players = data["elements"]
    teams = data["teams"]
    events = data["events"]

    # Conserver un instantané complet de la réponse brute
    extracted_at = datetime.now(timezone.utc)
    timestamp = extracted_at.strftime("%Y%m%dT%H%M%S_%fZ")

    RAW_DIRECTORY.mkdir(parents=True, exist_ok=True)
    output_path = RAW_DIRECTORY / f"bootstrap_static_{timestamp}.json"

    with output_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)

    print(f"Joueurs récupérés : {len(players)}")
    print(f"Équipes récupérées : {len(teams)}")
    print(f"Journées récupérées : {len(events)}")
    print(f"Fichier sauvegardé : {output_path}")


if __name__ == "__main__":
    main()