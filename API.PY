import os
import base64
from io import BytesIO
import json
import uuid
import time
import datetime
import psutil
import socket
import requests
from PIL import Image
from functools import wraps
from cryptography.fernet import Fernet
from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, request, jsonify, render_template, redirect, url_for, session, send_from_directory, abort
from werkzeug.utils import secure_filename
import pymongo
import yt_dlp 

app = Flask(__name__)
app.secret_key = os.urandom(24)

# --- Configuration ---
API_URL = "https://apikaede.nexcord.pro"
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}
DATABASE_NAME = "kaede_bot" 
DEFAULT_USER = "username"
DEFAULT_PASSWORD = "password"
MONGODB_URI = "your mongodb uri"  # Replace with your MongoDB URI

# --- MongoDB Connection ---
client_mongodb = pymongo.MongoClient(MONGODB_URI)
db = client_mongodb[DATABASE_NAME]

# --- Encryption Key ---
ENCRYPTION_KEY = b'2MwntYv2lfnc82zWMfDPhB5r23veWoo5u6rZjYUJhBU='  # Replace with your secure key!
f = Fernet(ENCRYPTION_KEY)

# --- Functions ---

def encrypt_data(data):
    """Encrypts data using Fernet."""
    return f.encrypt(data.encode()).decode()

def decrypt_data(data):
    """Decrypts data using Fernet."""
    return f.decrypt(data.encode()).decode()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- API Token Management ---

def generate_api_token(user_id):
    """Generates a new API token (never expires) and saves it to MongoDB."""
    token = str(uuid.uuid4())
    new_api_token = {
        'token': token,
        'user_id': user_id,
        'created_at': datetime.datetime.utcnow()
    }
    db.api_tokens.insert_one(new_api_token)
    return token

# --- Authentication Decorator ---

def require_api_token(func):
    @wraps(func)
    def decorated_function(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header:
            abort(401, description="Missing Authorization header.")

        try:
            token_type, api_token = auth_header.split(" ")
            if token_type.lower() != "bearer":
                raise ValueError("Invalid token type.")
        except ValueError:
            abort(401, description="Invalid Authorization header format.")

        api_token_record = db.api_tokens.find_one({'token': api_token})
        if not api_token_record:
            abort(401, description="Invalid API token.")

        # Token is valid, you can access user_id:
        current_user_id = api_token_record.get('user_id')

        return func(*args, **kwargs)
    return decorated_function

# --- Error Handling ---

@app.errorhandler(400)
def bad_request(error):
    return jsonify({'error': 'Bad Request', 'message': error.description}), 400

@app.errorhandler(401)
def unauthorized(error):
    return jsonify({'error': 'Unauthorized', 'message': error.description}), 401

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not Found', 'message': error.description}), 404

@app.errorhandler(500)
def internal_server_error(error):
    return jsonify({'error': 'Internal Server Error',
                    'message': 'An unexpected error occurred.'}), 500

# --- Initialize Database ---

def initialize_database():
    """Initializes the database with essential data if not present."""
    if db.users.find_one({'username': DEFAULT_USER}) is None:
        hashed_password = generate_password_hash(DEFAULT_PASSWORD)
        default_user = {
            'username': DEFAULT_USER,
            'password_hash': hashed_password
        }
        db.users.insert_one(default_user)

# --- Function to get status data ---

def get_status_data():
    cpu_percent = psutil.cpu_percent()
    memory = psutil.virtual_memory()
    network = psutil.net_io_counters()

    # --- Perform API Health Checks ---
    api_health_checks = []

    try:
        db.command("ping")
        api_health_checks.append({"name": "Database Connection", "status": "OK"})
    except Exception as e:
        api_health_checks.append({"name": "Database Connection", "status": f"Error: {e}"})

    try:
        socket.create_connection(("google.com", 80), 2)
        api_health_checks.append({"name": "Network Connectivity", "status": "OK"})
    except Exception as e:
        api_health_checks.append({"name": "Network Connectivity", "status": f"Error: {e}"})

    try:
        disk = psutil.disk_usage('/')
        if disk.percent >= 90:
            api_health_checks.append({"name": "Disk Space", "status": f"Warning: {disk.percent}% used"})
        else:
            api_health_checks.append({"name": "Disk Space", "status": "OK"})
    except Exception as e:
        api_health_checks.append({"name": "Disk Space", "status": f"Error: {e}"})

    # Get connected bots from MongoDB
    connected_bots = db.bots.distinct('bot_name', {'connected': True})

    return {
        'cpu_percent': cpu_percent,
        'memory': memory._asdict(),
        'network': network._asdict(),
        'api_health_checks': api_health_checks,
        'connected_bots': list(connected_bots)
    }

# --- API Endpoints ---

@app.route('/')
@require_api_token
def index():
    return jsonify({'message': 'Welcome to the API (Protected)'})

@app.route('/status', methods=['GET'])
@require_api_token
def status():
    status_data = get_status_data()
    return jsonify(status_data)

# --- Bot Management Routes ---

@app.route('/register', methods=['POST'])
def register():
    """Registers a new user and generates an API token."""
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        abort(400, description="Missing username or password.")

    if db.users.find_one({'username': username}):
        abort(400, description="Username already taken.")

    hashed_password = generate_password_hash(password)
    new_user = {
        'username': username,
        'password_hash': hashed_password
    }
    result = db.users.insert_one(new_user)
    new_user_id = str(result.inserted_id) # Get the ID of the newly created user

    # Generate an API token for the user
    api_token = generate_api_token(new_user_id)

    return jsonify({'message': 'User registered successfully!', 'api_token': api_token}), 201

@app.route('/apiV7/bot/<bot_name>/connect', methods=['POST'])
@require_api_token
def connect_bot(bot_name):
    """Marks a bot as connected in MongoDB."""
    result = db.bots.update_one({'bot_name': bot_name}, {'$set': {'connected': True}})
    if result.modified_count == 1:
        return jsonify({'message': 'Bot connected'}), 200
    else:
        abort(404, description="Bot not found.")

@app.route('/apiV7/bot/<bot_name>/disconnect', methods=['POST'])
@require_api_token
def disconnect_bot(bot_name):
    """Marks a bot as disconnected in MongoDB."""
    result = db.bots.update_one({'bot_name': bot_name}, {'$set': {'connected': False}})
    if result.modified_count == 1:
        return jsonify({'message': 'Bot disconnected'}), 200
    else:
        abort(404, description="Bot not found.")

@app.route('/apiV7/bot/<bot_name>/token', methods=['GET', 'POST'])
@require_api_token
def get_bot_api_token(bot_name):
    """Retrieves or sets the API token for a registered bot."""
    bot = db.bots.find_one({'bot_name': bot_name})
    if not bot:
        abort(404, description="Bot not found.")

    if request.method == 'POST':
        data = request.get_json()
        new_token = data.get('token')
        if not new_token:
            abort(400, description="Missing 'token' in request data.")
        db.bots.update_one({'bot_name': bot_name}, {'$set': {'token': new_token}})
        return jsonify({'message': 'Bot API token updated.', 'token': new_token}), 200

    elif request.method == 'GET':
        token = bot.get('token')
        if not token:
            abort(404, description="Bot API token not set.")
        return jsonify({'token': token}), 200


# --- User Authentication Routes --- 

@app.route('/register', methods=['POST'])
def register():
    """Registers a new user."""
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        abort(400, description="Missing username or password.")

    if db.users.find_one({'username': username}):
        abort(400, description="Username already taken.")

    hashed_password = generate_password_hash(password)
    new_user = {
        'username': username,
        'password_hash': hashed_password
    }
    db.users.insert_one(new_user)
    return jsonify({'message': 'User registered successfully!'}), 201

@app.route('/login', methods=['POST'])
def login():
    """Logs in a user and returns an API token."""
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')

    user = db.users.find_one({'username': username})
    if not user or not check_password_hash(user['password_hash'], password):
        abort(401, description="Invalid username or password.")

    # Generate API token (never expires)
    api_token = generate_api_token(str(user['_id'])) 
    return jsonify({'api_token': api_token}), 200

# --- /surf Route ---
@app.route('/surf', methods=['POST'])
@require_api_token
def surf():
    """Retrieves web page content using requests."""
    data = request.get_json()
    url = data.get('url')
    if not url:
        abort(400, description="Missing 'url' in request data.")

    try:
        response = requests.get(url)
        response.raise_for_status()
        return jsonify({'content': response.text}), 200
    except requests.exceptions.RequestException as e:
        return jsonify({'error': f"Error fetching URL: {str(e)}"}), 500

# --- /play_youtube Route ---
@app.route('/play_youtube', methods=['POST'])
@require_api_token
def play_youtube():
    """Downloads and returns YouTube video information using yt-dlp."""
    data = request.get_json()
    video_url = data.get('video_url')
    if not video_url:
        abort(400, description="Missing 'video_url' in request data.")

    ydl_opts = {
        'format': 'best',
        # Add other yt-dlp options as needed
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            return jsonify(info), 200
    except yt_dlp.utils.DownloadError as e:
        return jsonify({'error': f"Error downloading video info: {str(e)}"}), 500


# --- Initialize Database on Startup ---
initialize_database()
 
# --- Run the App ---
if __name__ == '__main__':
    app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
    app.run(debug=True, host='0.0.0.0', port=11403)