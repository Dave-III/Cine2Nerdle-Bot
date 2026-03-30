from tmdbv3api import TMDb, Movie
import json
import time

# --- Setup ---
tmdb = TMDb()
tmdb.api_key = "c8004837d09a5d36aa9d2c50aa9fa9a2"
tmdb.language = "en"

movie_api = Movie()

def fetch_movies_with_cast(max_pages=500):
    movies_data = {}

    for page in range(1, max_pages + 1):
        try:
            results = movie_api.popular(page=page)
            print(f"Fetching page {page}...")

            for m in results:
                try:
                    print(f"Processing: {m.title} (ID: {m.id})")

                    credits = movie_api.credits(m.id)

                    raw_cast = getattr(credits, "cast", [])
                    cast_list = list(raw_cast) if raw_cast else []

                    TOP_CAST = 10
                    actors = [c["name"] for c in cast_list[:TOP_CAST] if "name" in c]

                    if not actors:
                        continue

                    movies_data[m.title] = {
                        "id": m.id,
                        "popularity": m.popularity,
                        "release_date": m.release_date,
                        "actors": actors
                    }

                except Exception as e:
                    print(f"Cast error for {m.title}: {e}")

                time.sleep(0.01)

        except Exception as e:
            print("Page error:", page, e)
            break

    return movies_data


# --- Run it ---
movies = fetch_movies_with_cast(max_pages=500)

with open("tmdb_movies_10K.json", "w") as f:
    json.dump(movies, f, indent=2)

print(f"Saved {len(movies)} movies to tmdb_movies_10K.json")