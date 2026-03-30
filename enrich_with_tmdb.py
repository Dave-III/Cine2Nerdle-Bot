import requests
import json
import time
import re

API_KEY = "c8004837d09a5d36aa9d2c50aa9fa9a2"
BASE = "https://api.themoviedb.org/3"
TOP_CAST = 10
SLEEP = 0.02  # be nice to TMDB

test = requests.get(f"{BASE}/configuration", params={"api_key": API_KEY})
print("TMDB key check:", test.status_code)
def split_title_year(title_with_year):
    match = re.match(r"^(.*)\s\((\d{4})\)$", title_with_year)
    if not match:
        return None, None
    return match.group(1), int(match.group(2))


def tmdb_search(title, year):
    r = requests.get(
        f"{BASE}/search/movie",
        params={
            "api_key": API_KEY,
            "query": title,
            "year": year
        },
        timeout=30
    )
    r.raise_for_status()
    results = r.json().get("results", [])
    return results[0] if results else None


def tmdb_credits(movie_id):
    r = requests.get(
        f"{BASE}/movie/{movie_id}/credits",
        params={"api_key": API_KEY},
        timeout=30
    )
    r.raise_for_status()
    data = r.json()

    # Top cast
    cast = data.get("cast", [])
    actors = [c["name"] for c in cast[:TOP_CAST]]

    # Producers from crew
    crew = data.get("crew", [])
    producers = [
        c["name"]
        for c in crew
        if c.get("job") in {
            "Producer",
            "Executive Producer",
            "Co-Producer",
            "Associate Producer"
        }
    ]

    # Remove duplicates while preserving order
    producers = list(dict.fromkeys(producers))

    return actors, producers


def enrich_dataset(input_json, output_json="cine2nerdle_master.json"):
    with open(input_json, encoding="utf-8") as f:
        movies = json.load(f)

    final = {}

    for i, movie in enumerate(movies):
        title_with_year = movie["title_with_year"]
        title, year = split_title_year(title_with_year)

        if not title:
            continue

        print(f"[{i+1}/{len(movies)}] Resolving: {title_with_year}")

        try:
            result = tmdb_search(title, year)
            if not result:
                print("   ❌ No TMDB match")
                continue

            actors, producers = tmdb_credits(result["id"])

            final[title_with_year] = {
                "rank": movie["rank"],
                "submissions": movie["submissions"],
                "tmdb_id": result["id"],
                "actors": actors,
                "producers": producers
            }

            time.sleep(SLEEP)

        except Exception as e:
            print("   ⚠ Error:", e)
            continue

        # Optional: save progress every 200 movies
        if (i + 1) % 200 == 0:
            with open(output_json, "w", encoding="utf-8") as f:
                json.dump(final, f, indent=2, ensure_ascii=False)

    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(final, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Saved {len(final)} enriched movies to {output_json}")


if __name__ == "__main__":
    enrich_dataset("cine2nerdle_leaderboard.json")