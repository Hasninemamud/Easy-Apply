import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.environ.get('FLASK_SECRET_KEY')
    OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY')
    UPLOAD_FOLDER = 'uploads'
    ALLOWED_EXTENSIONS = {'pdf', 'docx'}
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max file size
    API_URL = "https://openrouter.ai/api/v1/chat/completions"
    MODEL = "openai/gpt-oss-20b:free"
    
    # API retry configuration
    API_MAX_RETRIES = 3
    API_BASE_DELAY = 2  # Base delay in seconds for exponential backoff