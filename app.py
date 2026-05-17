import os
import sys
import json
import uuid
import csv
import qrcode
import logging
from io import BytesIO
from base64 import b64encode
from datetime import datetime, timedelta
from threading import Lock

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)
logger.info("🚀 Starting Quiz Platform Server")

from flask import Flask, render_template, request, session, redirect, url_for, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
from sqlalchemy import create_engine, Column, String, Integer, DateTime, Boolean, JSON, Float
from sqlalchemy.orm import declarative_base, sessionmaker

# ─────────────────────────────────────────────────────────────────────────────
# INIT
# ─────────────────────────────────────────────────────────────────────────────
app = Flask(__name__, template_folder='.')
app.secret_key = os.environ.get('SECRET_KEY', 'cs671-viva-g27')
socketio = SocketIO(app, cors_allowed_origins="*")

ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'samiscrazy')

# DB
DB_PATH = 'quiz_platform.db'
engine = create_engine(f'sqlite:///{DB_PATH}')
Base = declarative_base()
SessionLocal = sessionmaker(bind=engine)

# ─────────────────────────────────────────────────────────────────────────────
# MODELS
# ─────────────────────────────────────────────────────────────────────────────
class Quiz(Base):
    __tablename__ = 'quizzes'
    id = Column(String(36), primary_key=True)
    title = Column(String(255), nullable=False)
    pin = Column(String(6), unique=True, nullable=False)
    admin_id = Column(String(36), nullable=False)
    questions_data = Column(JSON, nullable=False)  # list of Q dicts
    active_question_id = Column(Integer, nullable=True)  # which Q is open (null=none)
    active_question_open_time = Column(DateTime, nullable=True)
    state = Column(String(50), default='idle')  # idle, running, closed
    created_at = Column(DateTime, default=datetime.utcnow)

class QuizSession(Base):
    __tablename__ = 'quiz_sessions'
    id = Column(String(36), primary_key=True)
    quiz_id = Column(String(36), nullable=False)
    username = Column(String(255), nullable=False)
    score = Column(Integer, default=0)
    strikes = Column(Integer, default=0)
    attended = Column(Boolean, default=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

class Answer(Base):
    __tablename__ = 'answers'
    id = Column(String(36), primary_key=True)
    quiz_id = Column(String(36), nullable=False)
    session_id = Column(String(36), nullable=False)
    question_id = Column(Integer, nullable=False)
    user_answer = Column(String, nullable=True)
    is_correct = Column(Boolean, nullable=False)
    time_taken = Column(Float, nullable=True)  # seconds
    marked_as_strike = Column(Boolean, default=False)
    answered_at = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(engine)

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────
db_lock = Lock()

def gen_pin():
    return str(uuid.uuid4())[:6].upper()

def gen_qr(data):
    """Generate QR code → base64."""
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return b64encode(buf.getvalue()).decode()

def load_questions_from_file():
    """Load questions.json → dict keyed by quiz_id (default 'default')."""
    if os.path.exists('questions.json'):
        with open('questions.json', 'r') as f:
            return json.load(f)
    return []

def check_answer(q, user_answer):
    """Validate user_answer vs question q."""
    q_type = q.get('type', 'mcq')
    if q_type == 'mcq':
        return user_answer == str(q.get('correct_option'))
    elif q_type == 'true_false':
        correct = q.get('correct_answer')
        user_bool = user_answer.lower() == 'true'
        return user_bool == correct
    elif q_type == 'fill_up':
        return user_answer.lower().strip() == q.get('correct_answer', '').lower().strip()
    elif q_type == 'multi_correct':
        try:
            user_selections = set(int(x) for x in user_answer.split(','))
            correct_set = set(q.get('correct_options', []))
            return user_selections == correct_set
        except:
            return False
    return False

def get_leaderboard(quiz_id):
    """Rank sessions by score desc, speed asc."""
    db = SessionLocal()
    sessions = db.query(QuizSession).filter(QuizSession.quiz_id == quiz_id).all()
    
    leaderboard = []
    for sess in sessions:
        answers = db.query(Answer).filter(Answer.session_id == sess.id).all()
        speed = sum(a.time_taken or 0 for a in answers if a.is_correct)
        leaderboard.append({
            'username': sess.username,
            'score': sess.score,
            'speed': speed,
            'strikes': sess.strikes,
            'attended': sess.attended
        })
    
    leaderboard.sort(key=lambda x: (-x['score'], x['speed']))
    db.close()
    return leaderboard

# ─────────────────────────────────────────────────────────────────────────────
# ROUTES - STUDENT
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    if 'session_id' in session:
        return redirect(url_for('quiz'))
    
    # Pre-fill PIN if passed as query param (from QR code)
    pin = request.args.get('pin', '')
    logger.info(f"📱 Student accessing login page (PIN prefill: {pin if pin else 'none'})")
    return render_template('login.html', prefill_pin=pin)

@app.route('/join', methods=['GET', 'POST'])
def join():
    # Handle GET requests (from QR codes) → redirect to login with PIN prefilled
    if request.method == 'GET':
        pin = request.args.get('pin', '').strip().upper()
        logger.debug(f"🔗 QR code redirect to login with PIN: {pin}")
        if pin:
            return redirect(url_for('index', pin=pin))
        return redirect(url_for('index'))
    
    quiz_pin = request.form.get('quiz_pin', '').strip().upper()
    username = request.form.get('username', '').strip()
    
    logger.info(f"👤 Join attempt: PIN={quiz_pin}, Username={username}")
    
    if not quiz_pin or not username:
        logger.warning(f"❌ Invalid join data: PIN={quiz_pin}, Username={username}")
        return render_template('login.html', error='PIN and username required.')
    
    db = SessionLocal()
    quiz = db.query(Quiz).filter(Quiz.pin == quiz_pin).first()
    db.close()
    
    if not quiz:
        logger.warning(f"❌ Invalid PIN: {quiz_pin}")
        return render_template('login.html', error='Invalid quiz PIN.')
    
    logger.info(f"✓ Quiz found: {quiz.title} (ID: {quiz.id})")
    
    # Check if username already in this quiz
    db = SessionLocal()
    existing = db.query(QuizSession).filter(
        QuizSession.quiz_id == quiz.id,
        QuizSession.username == username
    ).first()
    
    if existing and not existing.completed_at:
        # Resume
        logger.info(f"🔄 Resuming session for {username} in quiz {quiz.title}")
        session['session_id'] = existing.id
        session['quiz_id'] = quiz.id
        session['username'] = username
        session['resuming'] = True
        db.close()
        return redirect(url_for('quiz'))
    
    db.close()
    
    # New session
    sess_id = str(uuid.uuid4())
    logger.info(f"✨ New session created: {sess_id} for {username} in {quiz.title}")
    db = SessionLocal()
    quiz_sess = QuizSession(
        id=sess_id,
        quiz_id=quiz.id,
        username=username
    )
    db.add(quiz_sess)
    db.commit()
    db.close()
    
    session['session_id'] = sess_id
    session['quiz_id'] = quiz.id
    session['username'] = username
    session['resuming'] = False
    
    return redirect(url_for('quiz'))

@app.route('/quiz')
def quiz():
    if 'session_id' not in session:
        return redirect(url_for('index'))
    
    logger.info(f"📖 Loading quiz interface for {session.get('username')} in session {session.get('session_id')}")
    
    db = SessionLocal()
    quiz = db.query(Quiz).filter(Quiz.id == session['quiz_id']).first()
    quiz_sess = db.query(QuizSession).filter(QuizSession.id == session['session_id']).first()
    
    if not quiz or not quiz_sess:
        db.close()
        return redirect(url_for('index'))
    
    if quiz_sess.completed_at:
        db.close()
        return redirect(url_for('result'))
    
    active_q_id = quiz.active_question_id
    resuming = session.pop('resuming', False)
    
    db.close()
    
    return render_template(
        'viva.html',
        quiz_id=quiz.id,
        username=session['username'],
        active_question_id=active_q_id,
        resuming=resuming
    )

@app.route('/submit', methods=['POST'])
def submit():
    if 'session_id' not in session:
        return redirect(url_for('index'))
    
    data = request.get_json()
    q_id = int(data.get('question_id'))
    user_answer = data.get('answer', '')
    time_taken = float(data.get('time_taken', 0))
    
    db = SessionLocal()
    quiz = db.query(Quiz).filter(Quiz.id == session['quiz_id']).first()
    quiz_sess = db.query(QuizSession).filter(QuizSession.id == session['session_id']).first()
    
    if not quiz or not quiz_sess or q_id >= len(quiz.questions_data):
        db.close()
        return {'error': 'Invalid question'}, 400
    
    q = quiz.questions_data[q_id]
    is_correct = check_answer(q, user_answer)
    
    # Capture quiz_id BEFORE closing session
    quiz_id = quiz.id
    
    answer_record = Answer(
        id=str(uuid.uuid4()),
        quiz_id=quiz_id,
        session_id=quiz_sess.id,
        question_id=q_id,
        user_answer=user_answer,
        is_correct=is_correct,
        time_taken=time_taken
    )
    db.add(answer_record)
    
    if is_correct:
        quiz_sess.score += 1
    
    db.commit()
    db.close()
    
    # Broadcast leaderboard update - use captured quiz_id
    socketio.emit('leaderboard_update', {'leaderboard': get_leaderboard(quiz_id)}, room=quiz_id)
    
    return {'ok': True, 'is_correct': is_correct}

@app.route('/strike', methods=['POST'])
def log_strike():
    if 'session_id' not in session:
        return '', 401
    
    db = SessionLocal()
    quiz_sess = db.query(QuizSession).filter(QuizSession.id == session['session_id']).first()
    quiz = db.query(Quiz).filter(Quiz.id == session['quiz_id']).first()
    
    if quiz_sess:
        quiz_sess.strikes += 1
        if quiz_sess.strikes >= 3:  # Threshold: 3 strikes = terminate
            quiz_sess.attended = False
            quiz_sess.completed_at = datetime.utcnow()
            db.commit()
            db.close()
            session.clear()
            return {'terminate': True}
    
    db.commit()
    db.close()
    
    socketio.emit('leaderboard_update', {'leaderboard': get_leaderboard(session['quiz_id'])}, room=session['quiz_id'])
    return {'strikes': quiz_sess.strikes if quiz_sess else 0}

@app.route('/result')
def result():
    if 'session_id' not in session:
        return redirect(url_for('index'))
    
    db = SessionLocal()
    quiz_sess = db.query(QuizSession).filter(QuizSession.id == session['session_id']).first()
    quiz = db.query(Quiz).filter(Quiz.id == session['quiz_id']).first()
    
    if not quiz_sess or quiz_sess.completed_at:
        score = quiz_sess.score if quiz_sess else 0
        total = len(quiz.questions_data) if quiz else 0
    else:
        score = quiz_sess.score
        total = len(quiz.questions_data)
        quiz_sess.completed_at = datetime.utcnow()
        db.commit()
    
    db.close()
    session.clear()
    
    return f"""
    <div style='font-family: sans-serif; text-align: center; margin-top: 50px;'>
        <h1>Quiz Complete!</h1>
        <p>Score: {score}/{total}</p>
        <p>Results recorded. You may close this tab.</p>
    </div>
    """

# ─────────────────────────────────────────────────────────────────────────────
# ROUTES - ADMIN
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            session['admin_id'] = str(uuid.uuid4())
            session['admin'] = True
            return redirect(url_for('admin_dashboard'))
        return render_template('admin_login.html', error='Incorrect password.')
    return render_template('admin_login.html')

@app.route('/admin')
def admin_dashboard():
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    
    db = SessionLocal()
    quizzes = db.query(Quiz).all()
    quizzes_data = []
    for q in quizzes:
        quizzes_data.append({
            'id': q.id,
            'title': q.title,
            'pin': q.pin,
            'state': q.state,
            'questions_data': q.questions_data,
            'active_question_id': q.active_question_id
        })
    db.close()
    
    return render_template('admin.html', quizzes=quizzes_data)

@app.route('/admin/quiz/new', methods=['POST'])
def create_quiz():
    if not session.get('admin'):
        return '', 403
    
    title = request.form.get('title', 'Untitled Quiz')
    questions_json_str = request.form.get('questions_json', '')
    
    # Try to parse questions from form, fallback to file
    try:
        if questions_json_str:
            questions = json.loads(questions_json_str)
        else:
            questions = load_questions_from_file()
    except:
        questions = load_questions_from_file()
    
    pin = gen_pin()
    quiz_id = str(uuid.uuid4())
    
    db = SessionLocal()
    quiz = Quiz(
        id=quiz_id,
        title=title,
        pin=pin,
        admin_id=session.get('admin_id', 'unknown'),
        questions_data=questions,
        state='idle'
    )
    db.add(quiz)
    db.commit()
    db.close()
    
    return jsonify({
        'quiz_id': quiz_id,
        'pin': pin,
        'qr': gen_qr(request.base_url.replace('/admin/quiz/new', f'/?pin={pin}')),
        'qr_url': request.base_url.replace('/admin/quiz/new', f'/?pin={pin}')
    })

@app.route('/admin/quiz/<quiz_id>/question/open/<int:q_id>', methods=['POST'])
def open_question(quiz_id, q_id):
    if not session.get('admin'):
        return '', 403
    
    logger.info(f"📂 Opening question {q_id} in quiz {quiz_id}")
    db = SessionLocal()
    quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
    
    if not quiz or q_id >= len(quiz.questions_data):
        logger.warning(f"⚠️ Invalid question ID {q_id} for quiz {quiz_id}")
        db.close()
        return '', 400
    
    # Capture question data BEFORE closing session
    question_data = quiz.questions_data[q_id]
    
    quiz.active_question_id = q_id
    quiz.active_question_open_time = datetime.utcnow()
    quiz.state = 'running'
    db.commit()
    db.close()
    
    # Broadcast to all in quiz
    logger.info(f"📢 Broadcasting question {q_id} to room {quiz_id}")
    socketio.emit('question_opened', {
        'question_id': q_id,
        'question': question_data
    }, room=quiz_id)
    
    return jsonify({'ok': True})

@app.route('/admin/quiz/<quiz_id>/question/close', methods=['POST'])
def close_question(quiz_id):
    if not session.get('admin'):
        return '', 403
    
    logger.info(f"🔒 Closing question in quiz {quiz_id}")
    db = SessionLocal()
    quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
    
    if quiz:
        quiz.active_question_id = None
        quiz.state = 'idle'
        db.commit()
        logger.info(f"📢 Broadcasting question_closed to room {quiz_id}")
        socketio.emit('question_closed', {}, room=quiz_id)
    
    db.close()
    return jsonify({'ok': True})

@app.route('/admin/quiz/<quiz_id>/leaderboard')
def view_leaderboard(quiz_id):
    if not session.get('admin'):
        return '', 403
    
    leaderboard = get_leaderboard(quiz_id)
    return jsonify(leaderboard)

@app.route('/admin/quiz/<quiz_id>/stats')
def quiz_stats(quiz_id):
    if not session.get('admin'):
        return '', 403
    
    db = SessionLocal()
    quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
    sessions = db.query(QuizSession).filter(QuizSession.quiz_id == quiz_id).all()
    
    stats = []
    for sess in sessions:
        answers = db.query(Answer).filter(Answer.session_id == sess.id).all()
        stats.append({
            'username': sess.username,
            'score': sess.score,
            'total': len(quiz.questions_data),
            'strikes': sess.strikes,
            'attended': sess.attended,
            'answers': [
                {
                    'q_id': a.question_id,
                    'correct': a.is_correct,
                    'time': a.time_taken,
                    'answer': a.user_answer
                } for a in answers
            ]
        })
    
    db.close()
    return jsonify(stats)

# ─────────────────────────────────────────────────────────────────────────────
# WEBSOCKET
# ─────────────────────────────────────────────────────────────────────────────
@socketio.on('join_quiz')
def on_join_quiz(data):
    quiz_id = data.get('quiz_id')
    logger.info(f"📍 Student joining quiz: {quiz_id}")
    join_room(quiz_id)
    emit('joined', {'ok': True})

@socketio.on('get_leaderboard')
def on_get_leaderboard(data):
    quiz_id = data.get('quiz_id')
    leaderboard = get_leaderboard(quiz_id)
    logger.debug(f"🏆 Leaderboard requested for {quiz_id}: {len(leaderboard)} users")
    emit('leaderboard_update', {'leaderboard': leaderboard})

if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info("🎓 QUIZ PLATFORM SERVER STARTING")
    logger.info("=" * 60)
    logger.info(f"📊 Database: {DB_PATH}")
    logger.info(f"🔐 Admin Password Set: {'Yes' if ADMIN_PASSWORD else 'No'}")
    logger.info(f"🌐 Listening on: 0.0.0.0:5001")
    logger.info("=" * 60)
    socketio.run(app, host='0.0.0.0', port=5001, debug=False, allow_unsafe_werkzeug=True)
