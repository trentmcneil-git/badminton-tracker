import streamlit as st
from supabase import create_client, Client
import pandas as pd
import json
from pathlib import Path

st.set_page_config(page_title="Coach Tool", page_icon="🏸", layout="centered")

# ── Supabase client ───────────────────────────────────────────────────────────
@st.cache_resource
def get_supabase() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

supabase = get_supabase()

# ── Session state init ────────────────────────────────────────────────────────
for key, default in [
    ("coach_user",        None),
    ("match_session_id",  None),
    ("match_stage",       "pre_game"),
]:
    if key not in st.session_state:
        st.session_state[key] = default

# ── Data loaders ──────────────────────────────────────────────────────────────
@st.cache_data
def load_tournaments():
    path = Path("data/tournaments.json")
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}

@st.cache_data
def load_drive_sports_players():
    path = Path("data/players.csv")
    if path.exists():
        df = pd.read_csv(path)
        ds = df[df["club"] == "Drive Sports Badminton Club"]["player_name"].dropna().unique()
        return sorted(ds.tolist())
    return []

@st.cache_data
def load_all_known_players():
    """All players seen across all tournaments — used for opponent dropdowns."""
    path = Path("data/players.csv")
    if path.exists():
        df = pd.read_csv(path)
        return sorted(df["player_name"].dropna().unique().tolist())
    return []

tournaments      = load_tournaments()
ds_players       = load_drive_sports_players()
all_players      = load_all_known_players()
MANUAL_ENTRY     = "➕ Enter manually..."
player_options   = all_players + [MANUAL_ENTRY]

tourn_options = {
    v["name"]: k
    for k, v in tournaments.items()
    if isinstance(v, dict) and "name" in v
}
tourn_names = sorted(tourn_options.keys())

EVENTS = (
    ["GS U11","GS U13","GS U15","GS U17","GS U19"] +
    ["GD U11","GD U13","GD U15","GD U17","GD U19"] +
    ["BS U11","BS U13","BS U15","BS U17","BS U19"] +
    ["BD U11","BD U13","BD U15","BD U17","BD U19"] +
    ["XD U11","XD U13","XD U15","XD U17","XD U19"]
)
ROUNDS = ["R1","R2","R3","Round of 32","Round of 16","QF","SF","F","3rd Place"]

# ── Reusable player picker widget ─────────────────────────────────────────────
def player_picker(label: str, key: str, required: bool = True) -> str:
    """Searchable dropdown from registry with manual-entry fallback."""
    choice = st.selectbox(
        label + (" *" if required else " (doubles/XD only)"),
        options=[None] + player_options,
        index=0,
        format_func=lambda x: "" if x is None else x,
        placeholder="Search by name...",
        key=f"pick_{key}",
    )
    if choice == MANUAL_ENTRY:
        manual = st.text_input(
            f"Enter {label.lower()} name manually",
            key=f"manual_{key}",
            placeholder="First Last",
        )
        return manual.strip()
    return choice or ""

# ── Auth helpers ──────────────────────────────────────────────────────────────
def login(email: str, password: str):
    try:
        resp = supabase.auth.sign_in_with_password({"email": email, "password": password})
        st.session_state.coach_user = resp.user
        return True, None
    except Exception as e:
        return False, str(e)

def logout():
    supabase.auth.sign_out()
    st.session_state.coach_user       = None
    st.session_state.match_session_id = None
    st.session_state.match_stage      = "pre_game"

def get_coach_name(user_id: str) -> str:
    r = supabase.table("coach_profiles").select("name").eq("id", user_id).execute()
    return r.data[0]["name"] if r.data else ""

def ensure_coach_profile(user_id: str, name: str):
    existing = supabase.table("coach_profiles").select("id").eq("id", user_id).execute()
    if not existing.data:
        supabase.table("coach_profiles").insert({"id": user_id, "name": name}).execute()

# ── Login screen ──────────────────────────────────────────────────────────────
if not st.session_state.coach_user:
    st.title("🏸 Coach Tool")
    st.subheader("Sign in")
    with st.form("login_form"):
        email    = st.text_input("Email")
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Sign In", use_container_width=True, type="primary"):
            if email and password:
                ok, err = login(email, password)
                if ok:
                    st.rerun()
                else:
                    st.error(f"Login failed: {err}")
            else:
                st.warning("Please enter email and password.")
    st.stop()

# ── First-time name setup ─────────────────────────────────────────────────────
user       = st.session_state.coach_user
coach_name = get_coach_name(user.id)

if not coach_name:
    st.title("🏸 Welcome!")
    st.subheader("Please enter your name to get started.")
    with st.form("profile_form"):
        name_input = st.text_input("Your name")
        if st.form_submit_button("Save", use_container_width=True, type="primary"):
            if name_input:
                ensure_coach_profile(user.id, name_input)
                st.rerun()
    st.stop()

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(f"👤 **{coach_name}**")
    st.caption(user.email)
    st.divider()
    if st.button("Sign Out", use_container_width=True):
        logout()
        st.rerun()

st.title("🏸 Coach Tool")

tab_new, tab_history, tab_athletes, tab_scouting = st.tabs([
    "🆕 New Match", "📋 Past Sessions", "👤 Athlete Notes", "🔍 Opponent Scouting"
])

# ═══════════════════════════════════════════════════════════════════════════════
# NEW MATCH tab
# ═══════════════════════════════════════════════════════════════════════════════
with tab_new:

    # ── Match setup (no st.form so dropdowns can react dynamically) ───────────
    if not st.session_state.match_session_id:
        st.subheader("Match Setup")

        tournament_name = st.selectbox("Tournament *", [None] + tourn_names,
                                       format_func=lambda x: "" if x is None else x,
                                       placeholder="Select tournament...")
        athlete = st.selectbox("Our Athlete *", [None] + ds_players,
                               format_func=lambda x: "" if x is None else x,
                               placeholder="Select athlete...")
        event   = st.selectbox("Event *", [None] + EVENTS,
                               format_func=lambda x: "" if x is None else x,
                               placeholder="Select event...")
        round_  = st.selectbox("Round *", [None] + ROUNDS,
                               format_func=lambda x: "" if x is None else x,
                               placeholder="Select round...")

        st.markdown("---")
        st.markdown("**Partner & Opponents**")

        partner   = player_picker("Partner",    "partner",   required=False)
        opponent1 = player_picker("Opponent 1", "opponent1", required=True)
        opponent2 = player_picker("Opponent 2", "opponent2", required=False)

        st.markdown("")
        if st.button("▶ Start Match", use_container_width=True, type="primary"):
            if tournament_name and athlete and event and round_ and opponent1:
                tid  = tourn_options.get(tournament_name, "")
                resp = supabase.table("match_sessions").insert({
                    "coach_id":        user.id,
                    "tournament_id":   tid,
                    "tournament_name": tournament_name,
                    "athlete_name":    athlete,
                    "event":           event,
                    "round":           round_,
                    "partner_name":    partner   or None,
                    "opponent1_name":  opponent1,
                    "opponent2_name":  opponent2 or None,
                }).execute()
                if resp.data:
                    st.session_state.match_session_id = resp.data[0]["id"]
                    st.session_state.match_stage      = "pre_game"
                    st.rerun()
            else:
                st.warning("Please fill in Tournament, Athlete, Event, Round and at least Opponent 1.")

    # ── Active match ──────────────────────────────────────────────────────────
    else:
        sess_id   = st.session_state.match_session_id
        sess_resp = supabase.table("match_sessions").select("*").eq("id", sess_id).execute()

        if not sess_resp.data:
            st.error("Session not found.")
            st.session_state.match_session_id = None
            st.rerun()

        sess = sess_resp.data[0]

        opp_str     = sess["opponent1_name"]
        if sess.get("opponent2_name"):
            opp_str += f" / {sess['opponent2_name']}"
        partner_str = f" + {sess['partner_name']}" if sess.get("partner_name") else ""

        st.markdown(f"### {sess['athlete_name']}{partner_str}  vs  {opp_str}")
        st.caption(f"{sess['tournament_name']} · {sess['event']} · {sess['round']}")
        st.divider()

        stages = {
            "pre_game":      "📋 Pre-Game",
            "between_games": "⏱ Between Games",
            "post_match":    "✅ Post-Match",
        }
        col1, col2, col3 = st.columns(3)
        for col, (key, label) in zip([col1, col2, col3], stages.items()):
            btn_type = "primary" if st.session_state.match_stage == key else "secondary"
            if col.button(label, use_container_width=True, type=btn_type, key=f"stage_{key}"):
                st.session_state.match_stage = key
                st.rerun()

        st.divider()

        def save_notes(stage_key: str, text: str):
            existing = supabase.table("match_notes") \
                .select("id").eq("session_id", sess_id).eq("stage", stage_key).execute()
            if existing.data:
                supabase.table("match_notes") \
                    .update({"notes": text, "updated_at": "now()"}) \
                    .eq("id", existing.data[0]["id"]).execute()
            else:
                supabase.table("match_notes") \
                    .insert({"session_id": sess_id, "stage": stage_key, "notes": text}).execute()

        def get_notes(stage_key: str) -> str:
            r = supabase.table("match_notes").select("notes") \
                .eq("session_id", sess_id).eq("stage", stage_key).execute()
            return r.data[0]["notes"] if r.data and r.data[0]["notes"] else ""

        if st.session_state.match_stage == "pre_game":
            st.subheader("📋 Pre-Game Plan")
            for opp_key in ["opponent1_name", "opponent2_name"]:
                opp_name = sess.get(opp_key)
                if opp_name:
                    opp_notes = supabase.table("athlete_notes").select("notes") \
                        .eq("player_name", opp_name).eq("is_opponent", True).execute()
                    if opp_notes.data and opp_notes.data[0]["notes"]:
                        with st.expander(f"📁 Scouting notes: {opp_name}", expanded=True):
                            st.markdown(opp_notes.data[0]["notes"])
            existing = get_notes("pre_game")
            notes = st.text_area("Pre-game tips & game plan", value=existing, height=220,
                                 placeholder="Key tactics, opponent weaknesses, game plan...")
            if st.button("💾 Save Pre-Game Notes", use_container_width=True, type="primary"):
                save_notes("pre_game", notes)
                st.success("Saved!")

        elif st.session_state.match_stage == "between_games":
            st.subheader("⏱ Between Games")
            existing = get_notes("between_games")
            notes = st.text_area("Coaching notes (between game 2 & 3)", value=existing,
                                 height=220,
                                 placeholder="What's working, adjustments needed, key message...")
            if st.button("💾 Save Notes", use_container_width=True, type="primary"):
                save_notes("between_games", notes)
                st.success("Saved!")

        elif st.session_state.match_stage == "post_match":
            st.subheader("✅ Post-Match Debrief")
            existing = get_notes("post_match")
            notes = st.text_area("Post-match debrief", value=existing, height=220,
                                 placeholder="What went well, what to improve, key learnings...")
            if st.button("💾 Save Post-Match Notes", use_container_width=True, type="primary"):
                save_notes("post_match", notes)
                st.success("Saved!")

        st.divider()
        if st.button("🏁 End Session", use_container_width=True):
            st.session_state.match_session_id = None
            st.session_state.match_stage      = "pre_game"
            st.rerun()

# ═══════════════════════════════════════════════════════════════════════════════
# PAST SESSIONS tab
# ═══════════════════════════════════════════════════════════════════════════════
with tab_history:
    st.subheader("Past Match Sessions")

    resp = supabase.table("match_sessions").select("*") \
        .order("created_at", desc=True).execute()

    if resp.data:
        for sess in resp.data:
            opp = sess["opponent1_name"]
            if sess.get("opponent2_name"):
                opp += f" / {sess['opponent2_name']}"
            partner_str = f" + {sess['partner_name']}" if sess.get("partner_name") else ""
            date_str    = sess["created_at"][:10]

            label = f"{date_str} · {sess['athlete_name']}{partner_str} vs {opp} · {sess['event']} {sess['round']}"
            with st.expander(label):
                st.caption(sess["tournament_name"])
                notes_resp = supabase.table("match_notes").select("stage,notes") \
                    .eq("session_id", sess["id"]).execute()
                stage_labels = {
                    "pre_game":      "📋 Pre-Game",
                    "between_games": "⏱ Between Games",
                    "post_match":    "✅ Post-Match",
                }
                if notes_resp.data:
                    for note in notes_resp.data:
                        st.markdown(f"**{stage_labels.get(note['stage'], note['stage'])}**")
                        st.markdown(note["notes"] or "_No notes recorded_")
                        st.divider()
                else:
                    st.info("No notes recorded for this session.")
    else:
        st.info("No past sessions yet.")

# ═══════════════════════════════════════════════════════════════════════════════
# ATHLETE NOTES tab
# ═══════════════════════════════════════════════════════════════════════════════
with tab_athletes:
    st.subheader("Athlete Notes")
    st.caption("Persistent development notes on Drive Sports athletes — visible to all coaches")

    sel_athlete = st.selectbox(
        "Select athlete", [None] + ds_players,
        format_func=lambda x: "" if x is None else x,
        placeholder="Choose athlete...", key="athlete_notes_select"
    )

    if sel_athlete:
        notes_resp = supabase.table("athlete_notes").select("*") \
            .eq("player_name", sel_athlete).eq("is_opponent", False).execute()
        existing      = notes_resp.data[0] if notes_resp.data else None
        existing_text = existing["notes"]  if existing         else ""

        ath_notes = st.text_area("Notes", value=existing_text, height=250,
                                 placeholder="Strengths, areas to develop, coaching history, match tendencies...")
        if st.button("💾 Save Athlete Notes", use_container_width=True, type="primary"):
            if existing:
                supabase.table("athlete_notes") \
                    .update({"notes": ath_notes, "updated_at": "now()"}) \
                    .eq("id", existing["id"]).execute()
            else:
                supabase.table("athlete_notes").insert({
                    "coach_id":    user.id,
                    "player_name": sel_athlete,
                    "is_opponent": False,
                    "notes":       ath_notes,
                }).execute()
            st.success("Saved!")

        sessions_resp = supabase.table("match_sessions") \
            .select("tournament_name,event,round,opponent1_name,opponent2_name,created_at") \
            .eq("athlete_name", sel_athlete).order("created_at", desc=True).execute()
        if sessions_resp.data:
            st.divider()
            st.markdown(f"**Recent match sessions ({len(sessions_resp.data)} total)**")
            for s in sessions_resp.data[:8]:
                opp = s["opponent1_name"]
                if s.get("opponent2_name"):
                    opp += f" / {s['opponent2_name']}"
                st.caption(f"{s['created_at'][:10]} · {s['tournament_name']} · {s['event']} {s['round']} vs {opp}")

# ═══════════════════════════════════════════════════════════════════════════════
# OPPONENT SCOUTING tab
# ═══════════════════════════════════════════════════════════════════════════════
with tab_scouting:
    st.subheader("Opponent Scouting Notes")
    st.caption("Notes are shared across all coaches")

    # ── Search existing scouting notes ────────────────────────────────────────
    opp_search = st.text_input("🔍 Search opponent", placeholder="Type opponent name...")
    if opp_search and len(opp_search) >= 2:
        all_opp = supabase.table("athlete_notes").select("player_name,notes,updated_at") \
            .eq("is_opponent", True).execute()
        matches = [n for n in (all_opp.data or []) if opp_search.lower() in n["player_name"].lower()]
        if matches:
            for n in matches:
                with st.expander(f"**{n['player_name']}**  ·  last updated {n['updated_at'][:10]}"):
                    st.markdown(n["notes"] or "_No notes_")
        else:
            st.info(f"No scouting notes found for '{opp_search}'.")

    st.divider()

    # ── Add / update scouting notes ───────────────────────────────────────────
    st.subheader("Add / Update Scouting Notes")

    opp_choice = st.selectbox(
        "Select opponent",
        options=[None] + player_options,
        format_func=lambda x: "" if x is None else x,
        placeholder="Search by name...",
        key="scouting_opp_select",
    )
    if opp_choice == MANUAL_ENTRY:
        opp_name = st.text_input("Enter opponent name manually",
                                 placeholder="First Last", key="scouting_manual")
        opp_name = opp_name.strip()
    else:
        opp_name = opp_choice or ""

    if opp_name:
        existing_resp = supabase.table("athlete_notes").select("*") \
            .eq("player_name", opp_name).eq("is_opponent", True).execute()
        existing      = existing_resp.data[0] if existing_resp.data else None
        existing_text = existing["notes"]      if existing         else ""

        opp_notes = st.text_area(
            "Scouting notes", value=existing_text, height=200,
            placeholder="Playing style, weaknesses, preferred shots, how to play against them..."
        )
        if st.button("💾 Save Scouting Notes", use_container_width=True, type="primary"):
            if existing:
                supabase.table("athlete_notes") \
                    .update({"notes": opp_notes, "updated_at": "now()"}) \
                    .eq("id", existing["id"]).execute()
            else:
                supabase.table("athlete_notes").insert({
                    "coach_id":    user.id,
                    "player_name": opp_name,
                    "is_opponent": True,
                    "notes":       opp_notes,
                }).execute()
            st.success("Saved!")
