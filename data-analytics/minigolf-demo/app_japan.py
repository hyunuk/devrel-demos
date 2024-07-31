import streamlit as st
import firebase_admin
from firebase_admin import firestore
from google.cloud import storage
from google.cloud import bigquery
from PIL import Image
import io
import matplotlib.pyplot as plt


# Constants
IMAGE_BUCKET = 'image_tokyo'
PROJECT_ID = "next-tokyo-2024-golf-demo-01"
BQ_DATASET = "minigolf"
BQ_PREFIX = f"{PROJECT_ID}.{BQ_DATASET}"
BQ_COMMENTARY = f"`{BQ_PREFIX}.commentary`"


# Initialize clients
bq_client = bigquery.Client(location='asia-northeast1')
storage_client = storage.Client()
if not firebase_admin._apps:
    firebase_admin.initialize_app(options={'projectId': f'{PROJECT_ID}'})
db = firestore.client()


@st.cache_data
def fetch_commentary(user_id):
    query = f"SELECT commentary FROM {BQ_COMMENTARY} WHERE user_id='{user_id}'"
    query_job = bq_client.query(query)
    results = list(query_job)
    return results[0].commentary if results else None


@st.cache_data
def display_image(user_id):
    blob_name = f'{user_id}.png'
    bucket = storage_client.bucket(IMAGE_BUCKET)
    blob = bucket.blob(blob_name)
    image_bytes = blob.download_as_bytes()
    image = Image.open(io.BytesIO(image_bytes))
    return image


def get_user_status(user_id):
    """Retrieves the status of a user from Firestore."""
    user_doc_ref = db.collection('users').document(user_id)
    user_doc = user_doc_ref.get()
    if not user_doc.exists:
        return None
    return user_doc.to_dict().get('status')


def get_tracking_data():
    query = f"SELECT * FROM {BQ_PREFIX}.tracking"
    df = bq_client.query(query).to_dataframe()
    last_frame_per_user = df.groupby('user_id')['frame_number'].transform(max)
    df_filtered = df[df['frame_number'] == last_frame_per_user]
    df_filtered = df_filtered[df_filtered['distance'] < 30]
    return df_filtered


def get_num_users(df):
    return df['user_id'].nunique()


def get_user_stats(df, user_id):
    user_shot_counts = df.groupby('user_id')['shot_number'].first()
    user_shot_counts = user_shot_counts[user_shot_counts > 0]
    user_shots = user_shot_counts.get(user_id, 0)
    avg = user_shot_counts.mean()
    shot_number_freq = user_shot_counts.value_counts()
    return user_shots, avg, shot_number_freq


form = st.form(key='user-id')
user_id = form.text_input('ユーザーID（minigolf_xxxx）を入力してください。')
submit = form.form_submit_button('Submit')

if submit:
    status = get_user_status(user_id)
    df = get_tracking_data()
    num_users = get_num_users(df)
    st.markdown(f"* これまで {num_users}人がプレイしました。")
    if status == "completed":
        user_shots, avg_shots_per_user, shot_number_freq = get_user_stats(df, user_id)
        stat = f"""
        * {user_id}のショット数: {user_shots}回 \n
        * 平均ショット数: {avg_shots_per_user:.2f}回
        """
        st.markdown(stat)

        commentary = fetch_commentary(user_id)
        image = display_image(user_id)
        st.image(image, 'ボールの弾道')

        # Bar Chart
        fig, ax = plt.subplots()  # Create a Matplotlib figure and axes
        barlist = ax.bar(shot_number_freq.index, shot_number_freq.values, color='#4285F4')
        ax.set_xlabel('Number of Shots')
        ax.set_ylabel('Number of Users')
        ax.set_title('Distribution of Number of Shots per User')
        ax.set_xlim(0, 9)
        ax.set_xticks(range(9))
        if user_shots in shot_number_freq.index:
            barlist[shot_number_freq.index.get_loc(user_shots)].set_color('#34A853')
            ax.legend([barlist[shot_number_freq.index.get_loc(user_shots)]],
                      [f'Shot Number of {user_id}'])

        st.pyplot(fig)  # Display the Matplotlib figure in Streamlit

        # Setting the sidebar
        with st.sidebar:
            st.header('Commentary', divider='rainbow')
            st.markdown(commentary)
    else:
        st.write(f"{user_id}さんのショット数は 集計中")
