import json
import re
import time
import random
from playwright.sync_api import sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
TITLE_RE = re.compile(r".+\(\d{4}\)$")

DATA_PATH = "cine2nerdle_master.json"  # <-- your enriched output file name


# ---------------------------
# Bot
# ---------------------------

class Bot:
    def __init__(self, json_path: str):
        raw = self._load_json(json_path)

        # Support either:
        # 1) dict keyed by "Title (YYYY)"
        # 2) list of objects with "title_with_year"
        if isinstance(raw, dict):
            self.movies = raw
        elif isinstance(raw, list):
            self.movies = {m["title_with_year"]: m for m in raw if "title_with_year" in m}
        else:
            raise ValueError("Unsupported JSON format. Must be dict or list.")

        # Case-insensitive lookup
        self.title_lookup = {k.lower(): k for k in self.movies.keys()}

        # Build "person" graph: person -> set(movies)
        # Person = actor OR producer
        self.person_to_movies = {}
        for movie, data in self.movies.items():
            for person in self._get_people(data):
                self.person_to_movies.setdefault(person, set()).add(movie)

        # Degree (hubiness)
        self.person_degree = {p: len(ms) for p, ms in self.person_to_movies.items()}

        # Usage rule tracking (actors + producers)
        self.person_usage = {}  # {person: times_used_as_connection}

        # No-repeats rule
        self.played_movies = set()
        self.move_count = 0

    def is_top_5k(self, movie_title_with_year: str) -> bool:
        d = self.movies.get(movie_title_with_year, {})
        r = d.get("rank")
        return isinstance(r, int) and r <= 5000


    def _load_json(self, path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def _get_people(self, data: dict):
        actors = data.get("actors") or []
        producers = data.get("producers") or []

        # ✅ Only top 5 actors
        actors = actors[:5]

        # If you want producers too, keep them; if not, remove "+ producers"
        people = actors + producers

        seen = set()
        out = []
        for x in people:
            if isinstance(x, str):
                x = x.strip()
                if x and x not in seen:
                    seen.add(x)
                    out.append(x)
        return out

    def resolve_title(self, title_with_year: str):
        if not title_with_year:
            return None
        return self.title_lookup.get(title_with_year.strip().lower())

    def one_move_connections(self, movie: str, max_degree=80, max_usage=3):
        """
        Returns: {neighbor_movie: [people_used_for_connection]}
        """
        connections = {}
        data = self.movies.get(movie)
        if not data:
            return connections

        for person in self._get_people(data):
            # filter mega hubs
            if self.person_degree.get(person, 0) > max_degree:
                continue
            # rule: max 3 uses
            if self.person_usage.get(person, 0) >= max_usage:
                continue

            for other in self.person_to_movies.get(person, ()):
                if other == movie:
                    continue
                if other in self.played_movies:
                    continue
                connections.setdefault(other, []).append(person)

        return connections
    
    def choose_random_move(self, movie, max_degree=80, max_usage=3):
        connections = self.one_move_connections(movie, max_degree=max_degree, max_usage=max_usage)
        if not connections:
            return None, None

        candidates = list(connections.keys())

        # 🚨 Secret rule: first bot move must be top 5k
        if self.move_count == 0:
            top5k = [m for m in candidates if self.is_top_5k(m)]
            if top5k:
                candidates = top5k
            else:
                # If none available, fall back (or return None to force a re-think)
                return None, None

        chosen = random.choice(candidates)
        return chosen, connections[chosen]
    
    def choose_best_move(self, movie: str, max_degree=80, max_usage=3, top_k=25):
        """
        Picks a niche move:
        - primary: low submissions (Cine2Nerdle popularity proxy)
        - tie-break: low rank
        - penalty: using many people in one connection burns usage faster
        Returns (chosen_movie, people_used)
        """
        connections = self.one_move_connections(movie, max_degree=max_degree, max_usage=max_usage)
        if not connections:
            return None, None

        scored = []
        for nxt, people in connections.items():
            d = self.movies[nxt]
            subs = d.get("submissions")
            rank = d.get("rank")

            # Treat missing submissions as "worse"
            subs_score = subs if isinstance(subs, int) else 10**12
            rank_score = rank if isinstance(rank, int) else 10**9

            # small penalty for burning multiple people
            penalty = 500 * max(0, len(people) - 1)

            score = subs_score + penalty + rank_score
            scored.append((score, nxt, people))

        scored.sort(key=lambda x: x[0])
        # choose from top_k to add a bit of variety (still niche)
        pick = scored[0] if len(scored) == 1 else scored[min(top_k, len(scored)) - 1]
        # better: choose random from top_k; keeping deterministic for now:
        best = scored[0]

        return best[1], best[2]

    def use_connection(self, people_used):
        for p in people_used:
            self.person_usage[p] = self.person_usage.get(p, 0) + 1

    

# ---------------------------
# Playwright helpers
# ---------------------------

def get_current_title(page) -> str:
    # Most reliable: the current movie title block uses text-pretty
    loc = page.locator("div.text-pretty")
    for i in range(min(loc.count(), 20)):
        t = loc.nth(i).inner_text().strip()
        if TITLE_RE.fullmatch(t):
            return t

    # Fallback (limited scan)
    spans = page.locator("span")
    for i in range(min(spans.count(), 200)):
        t = spans.nth(i).inner_text().strip()
        if TITLE_RE.fullmatch(t):
            return t

    return ""

def check_invalid_connection(page, timeout_ms=1500) -> bool:
    """
    Returns True if the site shows an invalid connection message.
    """
    try:
        notif = page.locator("#notification-message")
        notif.wait_for(state="visible", timeout=timeout_ms)

        text = notif.inner_text().strip()
        if "No links were found" in text:
            return True

    except PlaywrightTimeoutError:
        pass

    return False

INVALID_SUBSTRINGS = [
    "No links were found",           # the one you showed
    "No link was found",
    "No connections were found",
]

def get_notification_text(page) -> str:
    loc = page.locator("#notification-message")
    if loc.count() == 0:
        return ""
    try:
        return loc.first.inner_text().strip()
    except Exception:
        return ""

INVALID_RE = re.compile(r"no links were found", re.IGNORECASE)

def saw_invalid_notification(page, timeout_s=1.2, poll_s=0.05) -> bool:
    end = time.time() + timeout_s
    loc = page.locator("#notification-message")
    while time.time() < end:
        if loc.count():
            try:
                txt = loc.first.inner_text().strip()
                if txt and INVALID_RE.search(txt):
                    return True
            except Exception:
                pass
        time.sleep(poll_s)
    return False

def movie_rank(bot: Bot, title_with_year: str) -> int:
    d = bot.movies.get(title_with_year, {})
    r = d.get("rank")
    return r if isinstance(r, int) else 10**9  # unknown rank = very niche


def submit_with_autocomplete(page, title: str):
    input_box = page.locator("input").first
    input_box.click()
    input_box.press("Control+A")
    input_box.press("Backspace")

    input_box.type(title, delay=20)
    page.wait_for_timeout(1500)

    # IMPORTANT: select suggestion
    input_box.press("ArrowDown")
    page.wait_for_timeout(60)

    input_box.press("Enter")


def try_submit_candidate(page, title: str, current_title: str) -> bool:
    input_box = page.locator("input").first

    # Focus + clear
    input_box.click()
    input_box.press("Control+A")
    input_box.press("Backspace")

    # Type + select suggestion
    input_box.type(title, delay=20)
    page.wait_for_timeout(1500)
    input_box.press("ArrowDown")
    page.wait_for_timeout(60)
    # Submit
    input_box.press("Enter")

    # Give the site time to update
    page.wait_for_timeout(500)  # keep your timing vibe

    # ✅ Accepted if title changed
    new_title = get_current_title(page)
    return bool(new_title) and new_title != current_title

def try_play_candidate(page, cand: str, timeout_s=1.2, poll_s=0.05) -> bool:
    """
    Returns True if accepted, False if rejected.
    Acceptance rule:
      - If notification changes to 'No links were found...' after submit => reject
      - Otherwise => accept
    """
    before_notif = get_notification_text(page)

    submit_with_autocomplete(page, cand)
    time.sleep(0.5)  # keep your timing vibe

    end = time.time() + timeout_s
    while time.time() < end:
        now_notif = get_notification_text(page)

        # Only act if it CHANGED after this submit (prevents stale messages)
        if now_notif != before_notif and now_notif:
            if INVALID_RE.search(now_notif):
                return False
            else:
                # Some other message (or success toast) => accept
                return True

        time.sleep(poll_s)

    # If no new invalid message appeared, treat as accepted
    return True

def title_changed(page, before_title: str, timeout_s=1.2, poll_s=0.05) -> bool:
    end = time.time() + timeout_s
    while time.time() < end:
        now = get_current_title(page)  # <-- anchored title
        if now and now != before_title:
            return True
        time.sleep(poll_s)
    return False
# ---------------------------
# Main loop
# ---------------------------

if __name__ == "__main__":
    bot = Bot(DATA_PATH)

    print("\n🤖 Cine2Nerdle MK.2 — Final Bot")
    print("Ctrl+C to stop.\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()

        page.goto("https://www.cinenerdle2.app/battle")
        page.wait_for_timeout(1000)

        last_title = None

        try:
            while True:
                # Always read the website’s current title
                current = get_current_title(page)

                if not current:
                    time.sleep(0.4)
                    continue

                # Only print when it changes (but we still check every loop)
                if current != last_title:
                    print(f"\n🎬 Current: {current}")
                    last_title = current

                real = bot.resolve_title(current)
                if not real:
                    print("❌ Not in dataset, skipping.")
                    time.sleep(1.0)
                    continue

                # Mark current as played (no repeats)
                bot.played_movies.add(real)

                # Build candidate list from legal moves
                connections = bot.one_move_connections(real, max_degree=80, max_usage=3)
                if not connections:
                    print("❌ No legal moves left.")
                    break

                candidates = list(connections.keys())

                # 🚨 Secret rule: first bot move must be top 5k
                if bot.move_count == 0:
                    candidates = [m for m in candidates if bot.is_top_5k(m)]
                    if not candidates:
                        print("❌ No top-5k moves available on the first move.")
                        continue

                random.shuffle(candidates)

                played = None
                people_used = None

                # Start by trying a random candidate, then if it fails,
                # bias toward MORE COMMON (lower rank) candidates than the last try.
                remaining = candidates[:]  # already shuffled
                last_tried_rank = None

                attempts = 0
                MAX_ATTEMPTS = 40

                while attempts < MAX_ATTEMPTS and remaining:
                    attempts += 1

                    # If we failed before, only consider movies MORE common than last tried
                    if last_tried_rank is not None:
                        more_common = [m for m in remaining if movie_rank(bot, m) < last_tried_rank]
                        pool = more_common if more_common else remaining
                    else:
                        pool = remaining

                    cand = random.choice(pool)
                    remaining.remove(cand)

                    print(f"🎯 Trying: {cand} (rank={movie_rank(bot, cand)})")

                    before = current
                    ok = try_play_candidate(page, cand)

                    if ok:
                        played = cand
                        people_used = connections[cand]
                        print(f"✅ Accepted: {cand}")
                        break
                    else:
                        print(f"❌ Rejected: {cand}")
                        last_tried_rank = movie_rank(bot, cand)
                        time.sleep(0.5)

        except KeyboardInterrupt:
            print("\n🛑 Stopped.")

        input("\nPress Enter to close browser...")
        browser.close()
