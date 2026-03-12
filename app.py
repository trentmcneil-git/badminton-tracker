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
    """Convert 'LAST, First' (tournament format) or 'First Last' to 'first last'."""
    name = str(name).strip()
    if ',' in name:
        parts = name.split(',', 1)
        return (parts[1].strip() + ' ' + parts[0].strip()).lower()
    return name.lower()


def get_birth_year(player_name: str, registry: pd.DataFrame) -> int | None:
    """Look up a player's birth year from the registry by normalized name."""
    if registry.empty:
        return None
    norm = normalize_name_for_lookup(player_name)
    match = registry[registry["_norm"] == norm]
    return int(match["birth_year"].iloc[0]) if not match.empty else None


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

if all_matches.empty:
    st.info("No data yet — add a tournament URL in the sidebar to get started.")
    st.stop()

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_overview, tab_player, tab_club, tab_matches = st.tabs([
    "Overview", "Player Analytics", "Club Leaderboard", "Match Results"
])


# ── Overview tab ─────────────────────────────────────────────────────────────
with tab_overview:
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Tournaments", len(load_tournaments()))
    col2.metric("Players", all_players["player_name"].nunique())
    col3.metric("Matches", len(all_matches))
    col4.metric("Clubs", all_players["club"].nunique())

    st.subheader("Matches by Event")
    event_counts = all_matches["event"].value_counts().reset_index()
    event_counts.columns = ["Event", "Matches"]
    fig = px.bar(event_counts, x="Event", y="Matches", color="Matches",
                 color_continuous_scale="Reds")
    fig.update_layout(showlegend=False, coloraxis_showscale=False)
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Top Clubs by Player Count")
    club_counts = all_players.groupby("club")["player_name"].nunique().reset_index()
    club_counts.columns = ["Club", "Players"]
    club_counts = club_counts.sort_values("Players", ascending=False).head(15)
    fig2 = px.bar(club_counts, x="Club", y="Players", color="Players",
                  color_continuous_scale="Blues")
    fig2.update_layout(showlegend=False, coloraxis_showscale=False,
                       xaxis_tickangle=-30)
    st.plotly_chart(fig2, use_container_width=True)


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

            # ── Player summary cards ───────────────────────────────────────────
            col1, col2, col3, col4, col5 = st.columns(5)
            col1.metric("Club", stats["club"])
            col2.metric("Birth Year", birth_year if birth_year else "—")
            col3.metric("Total Matches", stats["total_matches"])
            col4.metric("Wins / Losses", f"{stats['wins']} / {stats['losses']}")
            col5.metric("Win Rate", f"{stats['win_rate']}%")

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
