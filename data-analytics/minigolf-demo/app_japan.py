import streamlit as st
from google.cloud import storage
from google.cloud import bigquery


trace_bucket_name = 'image_gml_a'
BQ_COMMENTARY = "`gml-seoul-2024-demo-00.minigolf_a.commentary`"


def load_file_to_raw_bytes(file_name):
    with open(file_name, "rb") as src_file:
        raw_string = src_file.read()
    return raw_string


@st.cache_data
def fetch_commentary_and_rating(user_id):
    client = bigquery.Client(location='asia-northeast3')
    query_string = f"""
        SELECT commentary
        FROM {BQ_COMMENTARY}
        WHERE user_id = '{user_id}'
    """

    query_job = client.query(query_string)
    results = query_job.result()

    for row in results:
        commentary = row.commentary

    return commentary


@st.cache_data
def display_image(user_id):
    storage_client = storage.Client()

    # Get a reference to your bucket and blob
    blob_name = f'{user_id}.png'
    bucket = storage_client.bucket(trace_bucket_name)
    blob = bucket.blob(blob_name)

    # Download the image content as bytes
    image_bytes = blob.download_as_bytes()

    return image_bytes


def change_player_onclick():
    st.session_state.editing_player_number = True


def submit_onclick():
    try:
        st.session_state.player_number = edited_player_number
        st.session_state.player_nick_name = player_nick_name
    except ValueError:
        st.warning("Please enter reasonable number")
    st.session_state.editing_player_number = False


model_name = "gemini-1.5-pro-001"

# Init session state
if "player_name" not in st.session_state:
    st.session_state['player_name'] = None


# Initialize session state for the player number
if 'player_number' not in st.session_state:
    st.session_state.player_number = 'minigolf_0013'
if "player_nick_name" not in st.session_state:
    st.session_state.player_nick_name = "Jeff Dean"

# Flag to indicate if the player number is being edited
if 'editing_player_number' not in st.session_state:
    st.session_state.editing_player_number = False

# st.text(f"正在查看选手的{st.session_state['player_nick_name']}结果")
# Display the player number section
# Initialize session state for the player number and nickname
if 'player_number' not in st.session_state:
    st.session_state.player_number = 1
if 'player_nick_name' not in st.session_state:
    st.session_state.player_nick_name = "Player 1"  # Default nickname

# Flag to indicate if the player number is being edited
if 'editing_player_number' not in st.session_state:
    st.session_state.editing_player_number = False


st.markdown(f'<p style="color:purple;font-size:48px;">Current Player: {st.session_state.player_nick_name}</p>', unsafe_allow_html=True)

# Display the player number section
if st.session_state.editing_player_number:
    # If editing, display text inputs for modification in the same row
    col1, col2 = st.columns(2)
    with col1:
        edited_player_number = st.text_input("Change player number", value=st.session_state.player_number, on_change=None) # Disable on_change
    with col2:
        player_nick_name = st.text_input("modify nickname", value=st.session_state.player_nick_name, on_change=None) # Disable on_change

    # Update the session state and reset the editing flag only if Enter is pressed
    st.button("Submit", on_click=submit_onclick)


else:
    # If not editing, display the player information and a button to trigger editing
    st.button("Change player", on_click=change_player_onclick)


commentary = fetch_commentary_and_rating(st.session_state.player_number)

image_bytes = display_image(st.session_state.player_number)

st.image(image_bytes, 'Trace', use_column_width=True)


# Setting the sidebar
with st.sidebar:
    # Setting the Summary commentary
    st.header('Commentary', divider='rainbow')
    st.markdown(commentary)
