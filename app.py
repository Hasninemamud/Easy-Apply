import os
import re
import time
from flask import Flask, render_template, request, redirect, url_for, flash, session
import requests
import PyPDF2
import docx2txt
from bs4 import BeautifulSoup
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from config import Config

app = Flask(__name__)
app.config.from_object(Config)

# Validate configuration
def validate_config():
    if not app.config['SECRET_KEY']:
        raise RuntimeError("Flask secret key not configured")
    if not app.config['OPENROUTER_API_KEY']:
        raise RuntimeError("OpenRouter API key not configured")
    if len(app.config['OPENROUTER_API_KEY']) < 10:
        raise RuntimeError("Invalid OpenRouter API key")

validate_config()

# Setup rate limiting
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def extract_text_from_resume(file_obj, filename):
    """Extract text from PDF or DOCX resume from file object in memory"""
    if filename.lower().endswith('.pdf'):
        reader = PyPDF2.PdfReader(file_obj)
        text = ""
        for page in reader.pages:
            text += page.extract_text()
        return text
    elif filename.lower().endswith('.docx'):
        return docx2txt.process(file_obj)
    return ""

def clean_text(text):
    """Clean and normalize text"""
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def make_api_request_with_retry(payload, max_retries=3, base_delay=1):
    """Make API request with exponential backoff retry for rate limits"""
    headers = {
        "Authorization": f"Bearer {app.config['OPENROUTER_API_KEY']}",
        "Content-Type": "application/json"
    }
    
    for attempt in range(max_retries):
        try:
            response = requests.post(app.config['API_URL'], json=payload, headers=headers)
            
            if response.status_code == 429:  # Rate limited
                if attempt < max_retries - 1:  # Don't sleep on last attempt
                    wait_time = base_delay * (2 ** attempt)  # Exponential backoff
                    print(f"Rate limited. Waiting {wait_time} seconds before retry {attempt + 1}/{max_retries}...")
                    time.sleep(wait_time)
                    continue
                else:
                    return None, "Rate limit exceeded. Please try again in a few minutes."
            
            response.raise_for_status()
            return response.json(), None
            
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                wait_time = base_delay * (2 ** attempt)
                print(f"API request failed: {e}. Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
                continue
            else:
                return None, f"API request failed after {max_retries} attempts: {str(e)}"
    
    return None, "Maximum retry attempts exceeded"

def extract_job_description_from_url(url):
    """Extract job description from LinkedIn job URL"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        selectors = [
            'div.show-more-less-html__markup',
            '.jobs-description-content__text',
            '.jobs-description__content',
            '.description__text',
        ]
        
        for selector in selectors:
            element = soup.select_one(selector)
            if element:
                return element.get_text(strip=True)
        
        paragraphs = soup.find_all('p')
        if paragraphs:
            return '\n'.join([p.get_text(strip=True) for p in paragraphs])
        
        return ""
    
    except Exception as e:
        print(f"Error extracting job description from URL: {e}")
        return ""

def generate_cover_letter(resume_text, job_description):
    """Generate a tailored cover letter using OpenRouter API"""
    prompt = f"""
    Write a professional cover letter with proper business letter formatting. 
    
    IMPORTANT: Return ONLY the cover letter text with proper formatting. Do not include any analysis, explanations, or commentary.
    
    Format requirements:
    - Start with "Dear Hiring Manager," (or specific name if mentioned in job description)
    - Use proper paragraph structure with line breaks between paragraphs
    - Include 3-4 well-structured paragraphs:
      1. Opening paragraph: Express interest and mention the position
      2. Body paragraph(s): Highlight relevant experience and skills from resume that match job requirements
      3. Closing paragraph: Express enthusiasm and next steps
    - End with professional closing: "Sincerely," followed by "[Your Name]"
    - Keep concise (250-300 words total)
    - Use professional, confident tone
    
    Resume:
    {resume_text}
    
    Job Description:
    {job_description}
    
    Return only the properly formatted cover letter:
    """
    
    payload = {
        "model": app.config['MODEL'],
        "messages": [
            {"role": "system", "content": "You are a professional career coach specializing in business correspondence. Write ONLY a properly formatted cover letter using standard business letter format. Use clear paragraph breaks. Each paragraph should be well-structured and professional. Do not include ANY analysis, explanations, commentary, word counts, or planning text. Output must start with the greeting and follow proper business letter structure."},
            {"role": "user", "content": prompt}
        ]
    }
    
    result, error = make_api_request_with_retry(payload)
    
    if error:
        print(f"Error generating cover letter: {error}")
        return f"Error generating cover letter: {error}"
    
    if not result:
        return "Error generating cover letter. Please try again."
    
    try:
        content = result['choices'][0]['message']['content'].strip()
        
        # Clean up any extra text that might be included
        lines = content.split('\n')
        cleaned_lines = []
        
        for line in lines:
            line = line.strip()
            # Skip lines that contain analysis or commentary
            if any(skip_phrase in line.lower() for skip_phrase in [
                'analysis', 'we need to', 'the candidate', 'candidate lacks',
                'let\'s draft', 'provide bullet', 'just cover letter',
                'here is', 'cover letter:', 'draft within', 'max 300 words',
                'let\'s aim', 'let\'s craft', 'we should', 'make it concise',
                'assistantfinal', 'word letter', 'background:', 'skills.',
                'attach research', 'candidate\'s background', 'front-end skills',
                'backend, but', 'let\'s do a', 'format requirements', 'return only'
            ]):
                continue
            # Skip lines that are just numbers or very short
            if len(line) < 10 and (line.isdigit() or 'word' in line.lower()):
                continue
            # Keep the line (including empty lines for paragraph breaks)
            cleaned_lines.append(line)
        
        content = '\n'.join(cleaned_lines)
        
        # Remove any remaining commentary patterns
        content = re.sub(r'\b\d{2,3}[- ]?words?\b', '', content, flags=re.IGNORECASE)
        content = re.sub(r'Let\'s [^.!?]*[.!?]', '', content, flags=re.IGNORECASE)
        content = re.sub(r'We should [^.!?]*[.!?]', '', content, flags=re.IGNORECASE)
        content = re.sub(r'assistantfinal', '', content, flags=re.IGNORECASE)
        
        # Clean up multiple consecutive newlines but preserve paragraph breaks
        content = re.sub(r'\n{3,}', '\n\n', content)
        
        # Additional cleanup
        if content.startswith("Cover Letter:"):
            content = content.replace("Cover Letter:", "", 1).strip()
        if content.startswith("Here is your cover letter:"):
            content = content.replace("Here is your cover letter:", "", 1).strip()
            
        return content
    except Exception as e:
        print(f"Error processing cover letter response: {e}")
        return "Error processing cover letter. Please try again."

def generate_email(cover_letter, tone):
    """Generate an email with the specified tone"""
    tone_instructions = {
        'formal': "Write a formal, professional email that is polite and concise.",
        'enthusiastic': "Write an enthusiastic email that conveys excitement and passion for the position.",
        'short_direct': "Write a short, direct email that gets straight to the point."
    }
    
    prompt = f"""
    {tone_instructions.get(tone, tone_instructions['formal'])}
    
    IMPORTANT: Return ONLY the email content. Do not include any analysis, explanations, or commentary.
    
    The email should:
    - Be concise (under 150 words)
    - Include a brief introduction
    - Mention that a tailored cover letter is attached
    - Have a professional closing
    - Start with a subject line if appropriate
    
    Based on this cover letter context:
    {cover_letter[:200]}...
    
    Return only the email content:
    """
    
    payload = {
        "model": app.config['MODEL'],
        "messages": [
            {"role": "system", "content": "You are an expert in professional communication. Write ONLY the email content. Do not include ANY analysis, explanations, commentary, word counts, or planning text. Do not mention word limits. Do not use phrases like 'Let's', 'We should', 'assistantfinal', or any meta-commentary. Output must start directly with the email content."},
            {"role": "user", "content": prompt}
        ]
    }
    
    headers = {
        "Authorization": f"Bearer {app.config['OPENROUTER_API_KEY']}",
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.post(app.config['API_URL'], json=payload, headers=headers)
        response.raise_for_status()
        result = response.json()
        content = result['choices'][0]['message']['content'].strip()
        
        # Clean up any extra text that might be included
        lines = content.split('\n')
        cleaned_lines = []
        
        for line in lines:
            line = line.strip()
            # Skip lines that contain analysis or commentary
            if any(skip_phrase in line.lower() for skip_phrase in [
                'analysis', 'here is', 'email:', 'based on',
                'this email', 'the email should', 'let\'s', 'we should',
                'make it', 'assistantfinal', 'word email', 'under 150 words'
            ]):
                continue
            # Skip lines that are just numbers or very short
            if len(line) < 5 and (line.isdigit() or 'word' in line.lower()):
                continue
            if line:  # Only add non-empty lines
                cleaned_lines.append(line)
        
        content = '\n'.join(cleaned_lines)
        
        # Remove any remaining commentary patterns
        content = re.sub(r'\b\d{2,3}[- ]?words?\b', '', content, flags=re.IGNORECASE)
        content = re.sub(r'Let\'s [^.!?]*[.!?]', '', content, flags=re.IGNORECASE)
        content = re.sub(r'assistantfinal', '', content, flags=re.IGNORECASE)
        
        # Additional cleanup
        if content.startswith("Email:"):
            content = content.replace("Email:", "", 1).strip()
        if content.startswith("Here is your email:"):
            content = content.replace("Here is your email:", "", 1).strip()
        if content.startswith("Subject:"):
            # Keep subject line if it's properly formatted
            pass
            
        return content
    except Exception as e:
        print(f"Error generating email: {e}")
        return "Error generating email. Please try again."

@app.route('/')
def index():
    return render_template('home.html')

@app.route('/index')
def index_page():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
@limiter.limit("10 per minute")
def upload_file():
    if 'resume' not in request.files:
        flash('No file part')
        return redirect(request.url)
    
    file = request.files['resume']
    job_description = request.form.get('job_description', '')
    job_url = request.form.get('job_url', '')
    email_tone = request.form.get('email_tone', 'formal')
    
    if file.filename == '':
        flash('No selected file')
        return redirect(request.url)
    
    if file and allowed_file(file.filename):
        # Check file size (optional security measure)
        file.seek(0, 2)  # Seek to end of file
        file_size = file.tell()
        file.seek(0)  # Reset to beginning
        
        if file_size > app.config['MAX_CONTENT_LENGTH']:
            flash('File too large. Please upload a file smaller than 16MB.')
            return redirect(request.url)
        
        # Extract text from resume directly from memory - no file saving
        resume_text = extract_text_from_resume(file, file.filename)
        if not resume_text:
            flash('Could not extract text from resume. Please ensure it\'s a valid PDF or DOCX file.')
            return redirect(request.url)
        
        # If job URL is provided and job description is empty, try to extract from URL
        if job_url and not job_description:
            job_description = extract_job_description_from_url(job_url)
            if not job_description:
                flash('Could not extract job description from the provided URL. Please paste the job description manually.')
                return redirect(request.url)
        elif not job_description:
            flash('Please provide either a job description or a job URL.')
            return redirect(request.url)
        
        # Clean texts
        resume_text = clean_text(resume_text)
        job_description = clean_text(job_description)
        
        # Generate cover letter
        cover_letter = generate_cover_letter(resume_text, job_description)
        
        # Generate email
        email_text = generate_email(cover_letter, email_tone)
        
        # Store results in session for display
        session['cover_letter'] = cover_letter
        session['email_text'] = email_text
        
        return redirect(url_for('results'))
    
    flash('Invalid file type. Please upload a PDF or DOCX file.')
    return redirect(request.url)

@app.route('/results')
def results():
    cover_letter = session.get('cover_letter', '')
    email_text = session.get('email_text', '')
    return render_template('results.html', cover_letter=cover_letter, email_text=email_text)

if __name__ == '__main__':
    app.run(debug=True)  # For development only

# WSGI application for Vercel
application = app