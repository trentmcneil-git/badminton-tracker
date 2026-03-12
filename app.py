"""
Badminton Canada Tournament Tracker
A Streamlit app for coaches to track junior athlete performance.
"""

import os
import json
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from scraper import scrape_tournament, extract_tournament_id, scrape_player_registry

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Badminton Tracker",
    page_icon="🏸",
    layout="wide",
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)

TOURNAMENTS_FILE = os.path.join(DATA_DIR, "tournaments.json")
PLAYERS_FILE = os.path.join(DATA_DIR, "players.csv")
MATCHES_FILE = os.path.join(DATA_DIR, "matches.csv")
REGISTRY_FILE = os.path.join(DATA_DIR, "player_registry.csv")


# ── Persistence helpers ───────────────────────────────────────────────────────
def load_tournaments() -> dict:
    if os.path.exists(TOURNAMENTS_FILE):
        with open(TOURNAMENTS_FILE) as f:
            return json.load(f)
    return {}


def save_tournament_info(info: dict):
    tournaments = load_tournaments()
    tournaments[info["id"]] = info
    with open(TOURNAMENTS_FILE, "w") as f:
        json.dump(tournaments, f, indent=2)


def load_players() -> pd.DataFrame:
    if os.path.exists(PLAYERS_FILE):
        return pd.read_csv(PLAYERS_FILE)
    return pd.DataFrame(columns=["player_name", "club", "player_url", "tournament_id"])


def load_matches() -> pd.DataFrame:
    if os.path.exists(MATCHES_FILE):
        return pd.read_csv(MATCHES_FILE)
    return pd.DataFrame(columns=["tournament_id", "date", "event", "round", "player1", "player2", "winner", "score"])


def load_registry() -> pd.DataFrame:
    if os.path.exists(REGISTRY_FILE):
        return pd.read_csv(REGISTRY_FILE)
    return pd.DataFrame(columns=["player_name", "member_id", "birth_year"])


def normalize_name_for_lookup(name: str) -> str:
    """Lowercase a title-cased 'First Last' name for registry lookup."""
    return str(name).strip().lower()


def get_birth_year(player_name: str, registry: pd.DataFrame) -> int | None:
    """Look up a player's birth year from the registry by normalized name."""
    if registry.empty:
        return None
    norm = normalize_name_for_lookup(player_name)
    match = registry[registry["_norm"] == norm]
    return int(match["birth_year"].iloc[0]) if not match.empty else None


# U-age category -> birth year range for 2025-26 season
_AGE_TO_BIRTH_RANGE = {
    "U11": (2015, 2016), "U13": (2013, 2014), "U15": (2011, 2012),
    "U17": (2009, 2010), "U19": (2007, 2008),
}

def infer_birth_year_range(player_name: str, matches: pd.DataFrame) -> tuple | None:
    """
    Infer birth year range from the youngest age category a player has competed in.
    Returns (min_year, max_year) or None.
    """
    import re
    involved = matches[
        (matches["player1"] == player_name) | (matches["player2"] == player_name)
    ]
    if involved.empty:
        return None
    # Find the youngest age group (smallest U number = youngest age = most recent birth year)
    best = None
    for event in involved["event"].dropna().unique():
        m = re.search(r'U(\d+)', str(event), re.IGNORECASE)
        if m:
            u_age = int(m.group(1))
            rng = _AGE_TO_BIRTH_RANGE.get(f"U{u_age}")
            if rng:
                # Youngest category (highest U number) gives us the broadest range,
                # but lowest U number gives us the tightest birth year constraint
                if best is None or u_age < best[0]:
                    best = (u_age, rng)
    return best[1] if best else None


def build_gender_index(matches: pd.DataFrame) -> dict:
    """
    Infer gender for every player from their event names.
    BS/BD prefix → Male, GS/GD prefix → Female.
    XD-only players are left out (unknown).
    """
    all_names = pd.concat([matches["player1"], matches["player2"]]).dropna().unique()
    index = {}
    for name in all_names:
        involved = matches[
            (matches["player1"] == name) | (matches["player2"] == name)
        ]["event"].dropna()
        male_events   = involved.str.upper().str.startswith(("BS", "BD")).sum()
        female_events = involved.str.upper().str.startswith(("GS", "GD")).sum()
        if male_events > 0 and female_events == 0:
            index[name] = "Male"
        elif female_events > 0 and male_events == 0:
            index[name] = "Female"
        # players in both (shouldn't happen) or XD-only are excluded
    return index


def build_birth_year_index(matches: pd.DataFrame, registry: pd.DataFrame) -> dict:
    """
    Build a dict of player_name -> birth_year (exact from registry, or
    midpoint of inferred range as fallback).
    """
    reg_lookup = {}
    if not registry.empty and "_norm" in registry.columns:
        reg_lookup = dict(zip(registry["_norm"], registry["birth_year"]))

    all_names = pd.concat([matches["player1"], matches["player2"]]).dropna().unique()
    index = {}
    for name in all_names:
        norm = normalize_name_for_lookup(name)
        if norm in reg_lookup:
            index[name] = int(reg_lookup[norm])
        else:
            rng = infer_birth_year_range(name, matches)
            if rng:
                # Use the later year (younger end) so U13 players born 2013 or 2014
                # both appear when filtering by either year
                index[name] = rng[1]  # store the latest (youngest) birth year
    return index


def save_data(players: pd.DataFrame, matches: pd.DataFrame, tournament_id: str):
    """Append new data, avoiding duplicates for the same tournament."""
    # Players
    existing_players = load_players()
    existing_players = existing_players[existing_players["tournament_id"] != tournament_id]
    combined_players = pd.concat([existing_players, players], ignore_index=True)
    combined_players.to_csv(PLAYERS_FILE, index=False)

    # Matches
    existing_matches = load_matches()
    existing_matches = existing_matches[existing_matches["tournament_id"] != tournament_id]
    combined_matches = pd.concat([existing_matches, matches], ignore_index=True)
    combined_matches.to_csv(MATCHES_FILE, index=False)


# ── Analytics helpers ─────────────────────────────────────────────────────────
def get_player_stats(player_name: str, matches: pd.DataFrame, players: pd.DataFrame) -> dict:
    """Compute win/loss and other stats for a single player."""
    involved = matches[
        (matches["player1"] == player_name) | (matches["player2"] == player_name)
    ].copy()

    if involved.empty:
        return {}

    involved["won"] = involved["winner"] == player_name
    wins = involved["won"].sum()
    losses = len(involved) - wins
    win_rate = round(wins / len(involved) * 100, 1) if len(involved) > 0 else 0

    # Club
    club_row = players[players["player_name"] == player_name]
    club = club_row["club"].iloc[0] if not club_row.empty else "Unknown"

    # Events played
    events = involved["event"].unique().tolist()

    # Performance by event
    by_event = (
        involved.groupby("event")["won"]
        .agg(["sum", "count"])
        .rename(columns={"sum": "wins", "count": "matches"})
        .reset_index()
    )
    by_event["losses"] = by_event["matches"] - by_event["wins"]
    by_event["win_rate"] = (by_event["wins"] / by_event["matches"] * 100).round(1)

    # Tournaments played
    tourney_ids = involved["tournament_id"].unique().tolist()

    return {
        "player_name": player_name,
        "club": club,
        "total_matches": len(involved),
        "wins": int(wins),
        "losses": int(losses),
        "win_rate": win_rate,
        "events": events,
        "by_event": by_event,
        "tournament_ids": tourney_ids,
        "match_history": involved,
    }


def classify_discipline(event: str) -> str:
    """Classify an event name into Singles, Doubles, or Mixed Doubles."""
    e = str(event).lower()
    if "mixed" in e:
        return "Mixed Doubles"
    if "double" in e:
        return "Doubles"
    if "single" in e:
        return "Singles"
    return "Other"


def get_club_discipline_stats(matches: pd.DataFrame, players: pd.DataFrame) -> pd.DataFrame:
    """Win rate per club broken down by Singles, Doubles, and Mixed Doubles."""
    if matches.empty or players.empty:
        return pd.DataFrame()

    m = matches.copy()
    m["discipline"] = m["event"].apply(classify_discipline)
    m = m[m["discipline"] != "Other"]

    rows = []
    for club in sorted(players["club"].dropna().unique()):
        club_players = players[players["club"] == club]["player_name"].unique()
        for disc in ["Singles", "Doubles", "Mixed Doubles"]:
            disc_matches = m[
                (m["discipline"] == disc) &
                (m["player1"].isin(club_players) | m["player2"].isin(club_players))
            ]
            total = len(disc_matches)
            if total == 0:
                continue
            wins = disc_matches["winner"].isin(club_players).sum()
            rows.append({
                "Club": club,
                "Discipline": disc,
                "Matches": total,
                "Wins": int(wins),
                "Losses": total - int(wins),
                "Win Rate %": round(wins / total * 100, 1),
            })

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["Discipline", "Win Rate %"], ascending=[True, False])


def get_club_stats(club_name: str, matches: pd.DataFrame, players: pd.DataFrame) -> pd.DataFrame:
    club_players = players[players["club"] == club_name]["player_name"].unique()
    rows = []
    for p in club_players:
        stats = get_player_stats(p, matches, players)
        if stats:
            rows.append({
                "Player": p,
                "Club": club_name,
                "Matches": stats["total_matches"],
                "Wins": stats["wins"],
                "Losses": stats["losses"],
                "Win Rate %": stats["win_rate"],
                "Events": ", ".join(stats["events"]),
            })
    return pd.DataFrame(rows).sort_values("Win Rate %", ascending=False) if rows else pd.DataFrame()


# ── UI ────────────────────────────────────────────────────────────────────────
st.title("🏸 Badminton Canada Tournament Tracker")
st.caption("Track junior athlete performance across Badminton Canada tournaments")

# Sidebar — add tournaments
with st.sidebar:
    st.header("Add Tournament")
    url_input = st.text_input(
        "Tournament URL",
        placeholder="https://badmintoncanada.tournamentsoftware.com/tournament/...",
    )
    force_rescrape = st.checkbox("Re-scrape if already loaded", value=False)

    if st.button("Scrape Tournament", type="primary"):
        if url_input.strip():
            try:
                tid = extract_tournament_id(url_input.strip())
            except ValueError:
                st.error("Could not find a tournament ID in that URL. Please check and try again.")
                tid = None

            if tid:
                already_loaded = tid.upper() in {k.upper() for k in load_tournaments().keys()}
                if already_loaded and not force_rescrape:
                    st.warning("This tournament is already loaded. Check 'Re-scrape if already loaded' if you want to refresh it.")
                else:
                    with st.spinner("Scraping tournament data..."):
                        try:
                            result = scrape_tournament(url_input.strip())
                            save_tournament_info(result["info"])
                            save_data(result["players"], result["matches"], result["info"]["id"])
                            st.success(f"Loaded: {result['info']['name']} — {len(result['players'])} players, {len(result['matches'])} matches")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error: {e}")
        else:
            st.warning("Please enter a tournament URL.")

    st.divider()
    st.header("Player Registry")
    registry_exists = os.path.exists(REGISTRY_FILE)
    if registry_exists:
        reg = pd.read_csv(REGISTRY_FILE)
        st.caption(f"{len(reg)} ranked players with birth years")
    else:
        st.caption("Not yet loaded")
    if st.button("Refresh Player Registry"):
        with st.spinner("Fetching Alberta Junior rankings..."):
            try:
                reg_df = scrape_player_registry()
                reg_df.to_csv(REGISTRY_FILE, index=False)
                st.success(f"Loaded {len(reg_df)} players with birth years")
                st.rerun()
            except Exception as e:
                st.error(f"Error: {e}")

    st.divider()
    st.header("Loaded Tournaments")
    tournaments = load_tournaments()
    if tournaments:
        for tid, info in tournaments.items():
            st.write(f"**{info['name']}**")
            if info.get("date"):
                st.caption(info["date"])
    else:
        st.caption("No tournaments loaded yet.")

# Load data
all_players = load_players()
all_matches = load_matches()
registry = load_registry()
if not registry.empty:
    registry["_norm"] = registry["player_name"].apply(normalize_name_for_lookup)
# Birth year index covers all match players (exact from registry + inferred from age category)
birth_year_index = build_birth_year_index(all_matches, registry)
# Gender index inferred from event prefixes (BS/BD=Male, GS/GD=Female)
gender_index = build_gender_index(all_matches)

if all_matches.empty:
    st.info("No data yet — add a tournament URL in the sidebar to get started.")
    st.stop()

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_overview, tab_player, tab_club, tab_trend, tab_cohort, tab_h2h, tab_matches = st.tabs([
    "Overview", "Player Analytics", "Club Leaderboard",
    "Season Trend", "Birth Year Cohort", "Head-to-Head", "Match Results"
])


# ── Overview tab ─────────────────────────────────────────────────────────────
with tab_overview:
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Tournaments", len(load_tournaments()))
    col2.metric("Players", all_players["player_name"].nunique())
    col3.metric("Matches", len(all_matches))
    col4.metric("Clubs", all_players["club"].nunique())

    # ── Build club summary table (used by all three sections below) ────────────
    _ov_rows = []
    for _club in all_players["club"].dropna().unique():
        _cp = all_players[all_players["club"] == _club]["player_name"].unique()
        _cm = all_matches[all_matches["player1"].isin(_cp) | all_matches["player2"].isin(_cp)]
        _wins = int(_cm["winner"].isin(_cp).sum())
        _total = len(_cm)
        _ov_rows.append({
            "Club": _club,
            "Players": int(len(_cp)),
            "Matches": _total,
            "Wins": _wins,
            "Win Rate %": round(_wins / _total * 100, 1) if _total else 0,
        })
    ov_clubs = pd.DataFrame(_ov_rows).sort_values("Win Rate %", ascending=False)

    # ── Section 1: Top Clubs by Player Count ──────────────────────────────────
    st.subheader("Top Clubs by Player Count")
    top_by_players = ov_clubs.sort_values("Players", ascending=False).head(15)
    fig_pc = px.bar(
        top_by_players.sort_values("Players", ascending=True),
        x="Players", y="Club", orientation="h",
        color="Players", color_continuous_scale="Blues",
        hover_data=["Matches", "Wins", "Win Rate %"],
    )
    fig_pc.update_layout(coloraxis_showscale=False,
                         margin=dict(l=0, r=0, t=20, b=0),
                         height=max(300, len(top_by_players) * 32))
    st.plotly_chart(fig_pc, use_container_width=True)

    st.divider()

    # ── Section 2: Top Clubs by Overall Win Rate ───────────────────────────────
    st.subheader("Top Clubs by Overall Win Rate")
    st.caption("Minimum 30 matches to qualify")
    top_wr = ov_clubs[ov_clubs["Matches"] >= 30].head(15)
    col_tbl, col_chart = st.columns([1, 2])
    with col_tbl:
        st.dataframe(
            top_wr[["Club", "Players", "Matches", "Wins", "Win Rate %"]],
            hide_index=True, use_container_width=True
        )
    with col_chart:
        fig_wr = px.bar(
            top_wr.sort_values("Win Rate %", ascending=True),
            x="Win Rate %", y="Club", orientation="h",
            color="Win Rate %", color_continuous_scale="RdYlGn",
            range_color=[30, 70],
            hover_data=["Players", "Matches", "Wins"],
        )
        fig_wr.update_layout(
            coloraxis_showscale=False,
            margin=dict(l=0, r=0, t=20, b=0),
            height=max(300, len(top_wr) * 32),
        )
        st.plotly_chart(fig_wr, use_container_width=True)

    st.divider()

    # ── Section 3: Club Win Rate by Discipline (GS/GD/BS/BD/XD) ──────────────
    st.subheader("Club Win Rate by Event")
    st.caption("Which clubs develop the strongest players in each discipline — minimum 20 matches to qualify")

    _EVENT_PREFIXES = {
        "GS": "GS — Girls Singles",
        "GD": "GD — Girls Doubles",
        "BS": "BS — Boys Singles",
        "BD": "BD — Boys Doubles",
        "XD": "XD — Mixed Doubles",
    }
    _COLOURS = {"GS": "#3498db", "GD": "#9b59b6", "BS": "#2ecc71", "BD": "#1abc9c", "XD": "#e67e22"}

    ev_rows = []
    for _club in all_players["club"].dropna().unique():
        _cp = set(all_players[all_players["club"] == _club]["player_name"].unique())
        for _prefix, _label in _EVENT_PREFIXES.items():
            _evm = all_matches[
                all_matches["event"].str.upper().str.startswith(_prefix, na=False) &
                (all_matches["player1"].isin(_cp) | all_matches["player2"].isin(_cp))
            ]
            if len(_evm) < 20:
                continue
            _w = int(_evm["winner"].isin(_cp).sum())
            ev_rows.append({
                "Club": _club,
                "Event": _prefix,
                "Event Label": _label,
                "Matches": len(_evm),
                "Wins": _w,
                "Win Rate %": round(_w / len(_evm) * 100, 1),
            })

    if ev_rows:
        ev_df = pd.DataFrame(ev_rows)
        for _prefix, _label in _EVENT_PREFIXES.items():
            sub = ev_df[ev_df["Event"] == _prefix].sort_values("Win Rate %", ascending=False).head(12)
            if sub.empty:
                continue
            with st.expander(f"**{_label}**", expanded=True):
                c_tbl, c_chart = st.columns([1, 2])
                with c_tbl:
                    st.dataframe(
                        sub[["Club", "Matches", "Wins", "Win Rate %"]],
                        hide_index=True, use_container_width=True
                    )
                with c_chart:
                    fig_ev = px.bar(
                        sub.sort_values("Win Rate %", ascending=True),
                        x="Win Rate %", y="Club", orientation="h",
                        color="Win Rate %", color_continuous_scale="RdYlGn",
                        range_color=[30, 70],
                        hover_data=["Matches", "Wins"],
                    )
                    fig_ev.update_layout(
                        coloraxis_showscale=False,
                        margin=dict(l=0, r=0, t=10, b=0),
                        height=max(200, len(sub) * 30),
                    )
                    st.plotly_chart(fig_ev, use_container_width=True)


# ── Player Analytics tab ──────────────────────────────────────────────────────
with tab_player:
    player_names = sorted(all_players["player_name"].unique().tolist())
    selected_player = st.selectbox("Select a player", player_names, index=None,
                                   placeholder="Start typing a name...")

    if selected_player:
        stats = get_player_stats(selected_player, all_matches, all_players)
        if not stats:
            st.warning("No match data found for this player.")
        else:
            birth_year = get_birth_year(selected_player, registry)
            gender = gender_index.get(selected_player, "—")

            # ── Player summary cards ───────────────────────────────────────────
            col1, col2, col3, col4, col5, col6 = st.columns(6)
            col1.metric("Club", stats["club"])
            col2.metric("Gender", gender)
            col3.metric("Birth Year", birth_year if birth_year else "—")
            col4.metric("Total Matches", stats["total_matches"])
            col5.metric("Wins / Losses", f"{stats['wins']} / {stats['losses']}")
            col6.metric("Win Rate", f"{stats['win_rate']}%")

            # ── Performance by event ───────────────────────────────────────────
            st.subheader("Results by Event")
            be = stats["by_event"]
            if not be.empty:
                display_be = be.rename(columns={
                    "event": "Event", "wins": "Wins",
                    "losses": "Losses", "matches": "Matches",
                    "win_rate": "Win Rate %"
                })[["Event", "Matches", "Wins", "Losses", "Win Rate %"]]
                st.dataframe(display_be, use_container_width=True, hide_index=True)

                fig = go.Figure()
                fig.add_bar(name="Wins", x=be["event"], y=be["wins"],
                            marker_color="#2ecc71")
                fig.add_bar(name="Losses", x=be["event"], y=be["losses"],
                            marker_color="#e74c3c")
                fig.update_layout(barmode="stack", xaxis_title="Event",
                                  yaxis_title="Matches", height=350)
                st.plotly_chart(fig, use_container_width=True)

            # ── Match history ──────────────────────────────────────────────────
            st.subheader("Match History")
            history = stats["match_history"][
                ["date", "event", "round", "player1", "player2", "winner", "score"]
            ].copy()
            history["Result"] = history["winner"].apply(
                lambda w: "Win" if w == selected_player else "Loss"
            )
            history["Opponent"] = history.apply(
                lambda r: r["player2"] if r["player1"] == selected_player else r["player1"],
                axis=1,
            )
            history = history.rename(columns={
                "date": "Date", "event": "Event",
                "round": "Round", "score": "Score"
            })
            st.dataframe(
                history[["Date", "Event", "Round", "Opponent", "Result", "Score"]],
                use_container_width=True,
                hide_index=True,
            )


# ── Club Leaderboard tab ──────────────────────────────────────────────────────
with tab_club:
    clubs = sorted(all_players["club"].dropna().unique().tolist())
    selected_club = st.selectbox("Select a club", clubs, index=None,
                                 placeholder="Choose a club...")

    if selected_club:
        club_df = get_club_stats(selected_club, all_matches, all_players)
        if club_df.empty:
            st.info("No match data for players from this club.")
        else:
            st.subheader(f"{selected_club} — Player Leaderboard")
            st.dataframe(club_df, use_container_width=True, hide_index=True)

            fig = px.bar(
                club_df.sort_values("Win Rate %", ascending=True),
                x="Win Rate %", y="Player", orientation="h",
                color="Win Rate %", color_continuous_scale="RdYlGn",
                range_color=[0, 100],
            )
            fig.update_layout(coloraxis_showscale=False)
            st.plotly_chart(fig, use_container_width=True)

    st.subheader("All Clubs Summary")
    all_club_rows = []
    for club in all_players["club"].dropna().unique():
        cp = all_players[all_players["club"] == club]["player_name"].unique()
        club_matches = all_matches[
            all_matches["player1"].isin(cp) | all_matches["player2"].isin(cp)
        ]
        wins = (club_matches["winner"].isin(cp)).sum()
        total = len(club_matches)
        all_club_rows.append({
            "Club": club,
            "Players": len(cp),
            "Total Matches": total,
            "Wins": int(wins),
            "Win Rate %": round(wins / total * 100, 1) if total > 0 else 0,
        })
    all_clubs_df = pd.DataFrame(all_club_rows)
    if not all_clubs_df.empty:
        all_clubs_df = all_clubs_df.sort_values("Players", ascending=False)
    st.dataframe(all_clubs_df, use_container_width=True, hide_index=True)

    # ── Win Rate by Discipline ─────────────────────────────────────────────────
    st.subheader("Top Clubs by Win Rate — by Discipline")
    disc_df = get_club_discipline_stats(all_matches, all_players)

    if not disc_df.empty:
        min_matches = st.slider(
            "Minimum matches to qualify", min_value=1, max_value=50, value=10,
            help="Exclude clubs with fewer than this many matches in a discipline"
        )
        disc_filtered = disc_df[disc_df["Matches"] >= min_matches]

        for discipline in ["Singles", "Doubles", "Mixed Doubles"]:
            sub = disc_filtered[disc_filtered["Discipline"] == discipline].head(15)
            if sub.empty:
                continue
            st.markdown(f"**{discipline}**")
            col_table, col_chart = st.columns([1, 2])
            with col_table:
                st.dataframe(
                    sub[["Club", "Matches", "Wins", "Losses", "Win Rate %"]],
                    hide_index=True, use_container_width=True
                )
            with col_chart:
                fig_d = px.bar(
                    sub.sort_values("Win Rate %", ascending=True),
                    x="Win Rate %", y="Club", orientation="h",
                    color="Win Rate %", color_continuous_scale="RdYlGn",
                    range_color=[0, 100],
                )
                fig_d.update_layout(
                    coloraxis_showscale=False,
                    margin=dict(l=0, r=0, t=20, b=0),
                    height=max(200, len(sub) * 30),
                )
                st.plotly_chart(fig_d, use_container_width=True)


# ── Season Trend tab ──────────────────────────────────────────────────────────
with tab_trend:
    st.subheader("Win Rate Trend Over the Season")
    st.caption("Win rate per tournament, broken down by event type.")

    trend_player = st.selectbox(
        "Select a player", sorted(all_players["player_name"].unique()),
        index=None, placeholder="Start typing a name...", key="trend_player"
    )

    if trend_player:
        tid_info = load_tournaments()

        involved = all_matches[
            (all_matches["player1"] == trend_player) |
            (all_matches["player2"] == trend_player)
        ].copy()
        involved["won"] = involved["winner"] == trend_player

        # Classify each match into an event category based on event prefix
        def event_category(event):
            e = str(event).upper().strip()
            for prefix in ("GS", "GD", "BS", "BD", "XD"):
                if e.startswith(prefix):
                    return prefix
            return "Other"

        involved["category"] = involved["event"].apply(event_category)

        # Which categories does this player actually have?
        player_categories = [c for c in ["GS", "GD", "BS", "BD", "XD"]
                             if c in involved["category"].values]

        # Full display labels for each abbreviation
        cat_labels = {
            "GS": "GS — Girls Singles",
            "GD": "GD — Girls Doubles",
            "BS": "BS — Boys Singles",
            "BD": "BD — Boys Doubles",
            "XD": "XD — Mixed Doubles",
        }

        if not player_categories:
            st.info("No match data found for this player.")
        else:
            # Filter controls
            show_overall = st.checkbox("Show Overall (all events combined)", value=True)
            selected_cats = st.multiselect(
                "Show individual event types",
                options=player_categories,
                format_func=lambda c: cat_labels.get(c, c),
                default=player_categories,
            )

            # Colour palette per series
            colours = {
                "Overall": "#95a5a6",
                "GS": "#3498db",
                "GD": "#9b59b6",
                "BS": "#2ecc71",
                "BD": "#1abc9c",
                "XD": "#e67e22",
            }

            def build_trend(df_subset, label):
                rows = []
                for tid, grp in df_subset.groupby("tournament_id"):
                    info = tid_info.get(tid, tid_info.get(tid.upper(), {}))
                    t_name = info.get("name", tid) if isinstance(info, dict) else tid
                    t_date = info.get("date", "") if isinstance(info, dict) else ""
                    wins = grp["won"].sum()
                    total = len(grp)
                    rows.append({
                        "Tournament": t_name,
                        "Date": t_date,
                        "Matches": total,
                        "Wins": int(wins),
                        "Losses": total - int(wins),
                        "Win Rate %": round(wins / total * 100, 1),
                        "Series": label,
                    })
                return pd.DataFrame(rows).sort_values("Date") if rows else pd.DataFrame()

            fig_trend = go.Figure()
            all_series = {}

            if show_overall:
                df_overall = build_trend(involved, "Overall")
                if not df_overall.empty:
                    all_series["Overall"] = df_overall
                    season_avg = round(df_overall["Wins"].sum() / df_overall["Matches"].sum() * 100, 1)
                    fig_trend.add_scatter(
                        x=df_overall["Tournament"], y=df_overall["Win Rate %"],
                        mode="lines+markers", name=f"Overall (avg {season_avg}%)",
                        marker=dict(size=9, color=colours["Overall"]),
                        line=dict(color=colours["Overall"], width=2, dash="dot"),
                        hovertemplate="<b>%{x}</b><br>Overall: %{y}%<extra></extra>",
                    )

            for cat in selected_cats:
                df_cat = build_trend(involved[involved["category"] == cat], cat)
                if not df_cat.empty:
                    all_series[cat] = df_cat
                    cat_avg = round(df_cat["Wins"].sum() / df_cat["Matches"].sum() * 100, 1)
                    label = cat_labels.get(cat, cat)
                    fig_trend.add_scatter(
                        x=df_cat["Tournament"], y=df_cat["Win Rate %"],
                        mode="lines+markers", name=f"{label} (avg {cat_avg}%)",
                        marker=dict(size=9, color=colours.get(cat, "#aaaaaa")),
                        line=dict(color=colours.get(cat, "#aaaaaa"), width=2),
                        hovertemplate=f"<b>%{{x}}</b><br>{label}: %{{y}}%<extra></extra>",
                    )

            fig_trend.update_layout(
                yaxis=dict(title="Win Rate %", range=[0, 105]),
                xaxis=dict(title="", tickangle=-30),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
                height=420,
            )
            st.plotly_chart(fig_trend, use_container_width=True)

            # Detail tables per selected series
            for label, df_s in all_series.items():
                display_label = cat_labels.get(label, label)
                with st.expander(f"{display_label} — tournament detail"):
                    st.dataframe(
                        df_s[["Tournament", "Matches", "Wins", "Losses", "Win Rate %"]],
                        use_container_width=True, hide_index=True
                    )

            # First-half vs second-half split (overall only)
            if "Overall" in all_series and len(all_series["Overall"]) >= 2:
                df_overall = all_series["Overall"]
                mid = len(df_overall) // 2
                fh = df_overall.iloc[:mid]
                sh = df_overall.iloc[mid:]
                fh_rate = round(fh["Wins"].sum() / fh["Matches"].sum() * 100, 1)
                sh_rate = round(sh["Wins"].sum() / sh["Matches"].sum() * 100, 1)
                delta = round(sh_rate - fh_rate, 1)
                st.subheader("Season Split (Overall)")
                c1, c2, c3 = st.columns(3)
                c1.metric("First Half Win Rate", f"{fh_rate}%")
                c2.metric("Second Half Win Rate", f"{sh_rate}%")
                c3.metric("Change", f"{'+' if delta >= 0 else ''}{delta}%",
                          delta=delta, delta_color="normal")


# ── Birth Year Cohort tab ──────────────────────────────────────────────────────
with tab_cohort:
    st.subheader("Birth Year Cohort Leaderboard")
    st.caption(
        "Compare all players born in the same year. "
        "Birth years come from the player registry (exact) or are inferred from "
        "the youngest age category each player has competed in (approximate)."
    )

    available_years = sorted(set(birth_year_index.values()), reverse=True)
    if not available_years:
        st.warning("No birth year data available. Load tournaments and refresh the Player Registry.")
    else:
        selected_year = st.selectbox("Select birth year", available_years, index=None,
                                     placeholder="Choose a year...")

        if selected_year:
            gender_filter = st.radio("Gender", ["All", "Male", "Female"],
                                     horizontal=True, key="cohort_gender")

            cohort_players = [p for p, y in birth_year_index.items() if y == selected_year]
            if gender_filter != "All":
                cohort_players = [p for p in cohort_players
                                  if gender_index.get(p) == gender_filter]

            # Flag which players have exact vs inferred birth year
            reg_norms = set(registry["_norm"]) if not registry.empty and "_norm" in registry.columns else set()

            cohort_rows = []
            for p in cohort_players:
                stats = get_player_stats(p, all_matches, all_players)
                if stats and stats["total_matches"] > 0:
                    exact = normalize_name_for_lookup(p) in reg_norms
                    cohort_rows.append({
                        "Player": p,
                        "Club": stats["club"],
                        "Gender": gender_index.get(p, "—"),
                        "Matches": stats["total_matches"],
                        "Wins": stats["wins"],
                        "Losses": stats["losses"],
                        "Win Rate %": stats["win_rate"],
                        "Birth Year Source": "Registry" if exact else "Inferred",
                    })

            if cohort_rows:
                cohort_df = pd.DataFrame(cohort_rows).sort_values("Win Rate %", ascending=False)
                exact_count = (cohort_df["Birth Year Source"] == "Registry").sum()
                st.caption(
                    f"{len(cohort_df)} players born in {selected_year} "
                    f"({exact_count} confirmed from registry, "
                    f"{len(cohort_df) - exact_count} inferred from age category)"
                )

                st.dataframe(cohort_df, use_container_width=True, hide_index=True)

                fig_cohort = px.bar(
                    cohort_df.sort_values("Win Rate %", ascending=True),
                    x="Win Rate %", y="Player", orientation="h",
                    color="Win Rate %", color_continuous_scale="RdYlGn",
                    range_color=[0, 100],
                    hover_data=["Club", "Matches", "Wins", "Losses", "Birth Year Source"],
                )
                fig_cohort.update_layout(
                    coloraxis_showscale=False,
                    height=max(300, len(cohort_df) * 28),
                )
                st.plotly_chart(fig_cohort, use_container_width=True)
            else:
                st.info(f"No match data found for players born in {selected_year}.")


# ── Head-to-Head tab ──────────────────────────────────────────────────────────
with tab_h2h:
    st.subheader("Head-to-Head")
    st.caption("All matches between two players across every tournament.")

    all_player_names = sorted(all_players["player_name"].unique())
    col1, col2 = st.columns(2)
    with col1:
        h2h_p1 = st.selectbox("Player 1", all_player_names, index=None,
                               placeholder="Select player 1...", key="h2h_p1")
    with col2:
        h2h_p2 = st.selectbox("Player 2", all_player_names, index=None,
                               placeholder="Select player 2...", key="h2h_p2")

    if h2h_p1 and h2h_p2 and h2h_p1 != h2h_p2:
        h2h_matches = all_matches[
            ((all_matches["player1"] == h2h_p1) & (all_matches["player2"] == h2h_p2)) |
            ((all_matches["player1"] == h2h_p2) & (all_matches["player2"] == h2h_p1))
        ].copy()

        if h2h_matches.empty:
            st.info("These two players have never met in the data.")
        else:
            p1_wins = (h2h_matches["winner"] == h2h_p1).sum()
            p2_wins = (h2h_matches["winner"] == h2h_p2).sum()
            total = len(h2h_matches)

            # Score cards
            c1, c2, c3 = st.columns(3)
            c1.metric(h2h_p1, f"{p1_wins} wins")
            c2.metric("Total Matches", total)
            c3.metric(h2h_p2, f"{p2_wins} wins")

            # Visual win bar
            if total > 0:
                p1_pct = p1_wins / total
                p2_pct = p2_wins / total
                fig_h2h = go.Figure()
                fig_h2h.add_bar(
                    x=[p1_wins], y=["Record"], orientation="h",
                    name=h2h_p1, marker_color="#3498db",
                    text=[f"{h2h_p1}: {p1_wins}"], textposition="inside",
                )
                fig_h2h.add_bar(
                    x=[p2_wins], y=["Record"], orientation="h",
                    name=h2h_p2, marker_color="#e74c3c",
                    text=[f"{h2h_p2}: {p2_wins}"], textposition="inside",
                )
                fig_h2h.update_layout(
                    barmode="stack", height=120,
                    showlegend=False,
                    xaxis=dict(visible=False),
                    margin=dict(l=0, r=0, t=10, b=0),
                )
                st.plotly_chart(fig_h2h, use_container_width=True)

            # Individual match log
            tournaments = load_tournaments()
            h2h_matches["Tournament"] = h2h_matches["tournament_id"].map(
                lambda tid: tournaments.get(tid, {}).get("name", tid)
            )
            h2h_matches["Winner"] = h2h_matches["winner"]
            st.dataframe(
                h2h_matches[["Date", "Tournament", "Event", "Round", "Winner", "Score"]].rename(
                    columns={"date": "Date", "event": "Event", "round": "Round", "score": "Score"}
                ) if "Date" in h2h_matches.columns else
                h2h_matches[["date", "Tournament", "event", "round", "Winner", "score"]].rename(
                    columns={"date": "Date", "event": "Event", "round": "Round", "score": "Score"}
                ),
                use_container_width=True, hide_index=True
            )
    elif h2h_p1 and h2h_p2 and h2h_p1 == h2h_p2:
        st.warning("Please select two different players.")


# ── Match Results tab ─────────────────────────────────────────────────────────
with tab_matches:
    st.subheader("All Match Results")

    col1, col2, col3 = st.columns(3)
    with col1:
        event_filter = st.multiselect("Filter by Event",
                                      sorted(all_matches["event"].dropna().unique()))
    with col2:
        round_filter = st.multiselect("Filter by Round",
                                      sorted(all_matches["round"].dropna().unique()))
    with col3:
        player_filter = st.text_input("Search player name")

    filtered = all_matches.copy()
    if event_filter:
        filtered = filtered[filtered["event"].isin(event_filter)]
    if round_filter:
        filtered = filtered[filtered["round"].isin(round_filter)]
    if player_filter:
        mask = (
            filtered["player1"].str.contains(player_filter, case=False, na=False) |
            filtered["player2"].str.contains(player_filter, case=False, na=False)
        )
        filtered = filtered[mask]

    st.caption(f"Showing {len(filtered)} matches")
    st.dataframe(
        filtered[["date", "event", "round", "player1", "player2", "winner", "score"]].rename(
            columns={
                "date": "Date", "event": "Event", "round": "Round",
                "player1": "Player 1", "player2": "Player 2",
                "winner": "Winner", "score": "Score"
            }
        ),
        use_container_width=True,
        hide_index=True,
    )
