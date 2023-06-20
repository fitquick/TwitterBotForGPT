import os
import tweepy
from google.cloud import firestore
from datetime import datetime, time, timedelta
import pytz
from flask import Flask, request, render_template, session, redirect, url_for, jsonify, abort
from langchainagent import langchain_agent

API_KEY = os.getenv('API_KEY')
API_KEY_SECRET = os.getenv('API_KEY_SECRET')
ACCESS_TOKEN = os.getenv('ACCESS_TOKEN')
ACCESS_TOKEN_SECRET = os.getenv('ACCESS_TOKEN_SECRET')
admin_password = os.environ["ADMIN_PASSWORD"]

REQUIRED_ENV_VARS = [
    "ORDER",
]

DEFAULT_ENV_VARS = {
    'ORDER': ' {nowDateStr}のニュースを検索してください。',
}

client = tweepy.Client(
    consumer_key = API_KEY,
    consumer_secret = API_KEY_SECRET,
    access_token = ACCESS_TOKEN,
    access_token_secret = ACCESS_TOKEN_SECRET
)

db = firestore.Client()

def reload_settings():
    global ORDER, nowDate, nowDateStr, jst
    jst = pytz.timezone('Asia/Tokyo')
    nowDate = datetime.now(jst)
    nowDateStr = nowDate.strftime('%Y年%m月%d日')
    ORDER = get_setting('ORDER').format(nowDateStr=nowDateStr)

def get_setting(key):
    doc_ref = db.collection(u'settings').document('app_settings')
    doc = doc_ref.get()

    if doc.exists:
        doc_dict = doc.to_dict()
        if key not in doc_dict:
            # If the key does not exist in the document, use the default value
            default_value = DEFAULT_ENV_VARS.get(key, "")
            doc_ref.set({key: default_value}, merge=True)  # Add the new setting to the database
            return default_value
        else:
            return doc_dict.get(key)
    else:
        # If the document does not exist, create it using the default settings
        save_default_settings()
        return DEFAULT_ENV_VARS.get(key, "")
  
def save_default_settings():
    doc_ref = db.collection(u'settings').document('app_settings')
    doc_ref.set(DEFAULT_ENV_VARS, merge=True)

def update_setting(key, value):
    doc_ref = db.collection(u'settings').document('app_settings')
    doc_ref.update({key: value})
    
reload_settings()    

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', default='YOUR-DEFAULT-SECRET-KEY')

from flask_executor import Executor
executor = Executor(app)

@app.route('/login', methods=['GET', 'POST'])
def login():
    attempts_doc_ref = db.collection(u'settings').document('admin_attempts')
    attempts_doc = attempts_doc_ref.get()
    attempts_info = attempts_doc.to_dict() if attempts_doc.exists else {}

    attempts = attempts_info.get('attempts', 0)
    lockout_time = attempts_info.get('lockout_time', None)

    # ロックアウト状態をチェック
    if lockout_time:
        if datetime.now(jst) < lockout_time:
            return render_template('login.html', message='Too many failed attempts. Please try again later.')
        else:
            # ロックアウト時間が過ぎたらリセット
            attempts = 0
            lockout_time = None

    if request.method == 'POST':
        password = request.form.get('password')

        if password == admin_password:
            session['is_admin'] = True
            # ログイン成功したら試行回数とロックアウト時間をリセット
            attempts_doc_ref.set({'attempts': 0, 'lockout_time': None})
            return redirect(url_for('settings'))
        else:
            attempts += 1
            lockout_time = datetime.now(jst) + timedelta(minutes=10) if attempts >= 5 else None
            attempts_doc_ref.set({'attempts': attempts, 'lockout_time': lockout_time})
            return render_template('login.html', message='Incorrect password. Please try again.')
        
    return render_template('login.html')

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    if 'is_admin' not in session or not session['is_admin']:
        return redirect(url_for('login'))
    current_settings = {key: get_setting(key) or DEFAULT_ENV_VARS.get(key, '') for key in REQUIRED_ENV_VARS}

    if request.method == 'POST':
        for key in REQUIRED_ENV_VARS:
            value = request.form.get(key)
            if value:
                update_setting(key, value)
        return redirect(url_for('settings'))
    return render_template(
    'settings.html', 
    settings=current_settings, 
    default_settings=DEFAULT_ENV_VARS, 
    required_env_vars=REQUIRED_ENV_VARS
    )

def trim_tweet_text(text, max_length=140):
    if len(text) > max_length:
        text = text[:max_length]
    return text
@app.route('/tweet')
def create_tweet():
    reload_settings()
    future = executor.submit(_create_tweet, 0)  # Futureオブジェクトを受け取ります
    try:
        future.result()  
    except Exception as e:
        print(f"Error: {e}")  # エラーメッセージを表示します
    return jsonify({"status": "Tweet creation started"}), 200

def _create_tweet(retry_count):
    if retry_count >= 5:  # 最大リトライ回数を超えたらエラーメッセージを表示し終了
        print("Exceeded maximum retry attempts.")
        return

    result = langchain_agent(ORDER)
    if len(result) <= 140 and len(result) > 3:
        try:
            response = client.create_tweet(text = result)
            print(f"Response: {response}")
        except tweepy.TweepError as e:
            print(f"An error occurred: {e.response.text}")
    else:
        character_count = len(result) 
        print(f"character_count is {character_count} retrying...")
        _create_tweet(retry_count + 1)  # 文字数オーバーの場合はリトライカウンタを増やして再度試行

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
