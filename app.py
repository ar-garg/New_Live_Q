import os
import json
import uuid
import logging
import time
import threading
import queue
from io import BytesIO
from base64 import b64encode
from datetime import datetime
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger(__name__)

from flask import Flask, render_template, request, session, redirect, url_for, jsonify, send_file, Response
from flask_socketio import SocketIO, emit, join_room
from sqlalchemy import create_engine, Column, String, Integer, DateTime, Boolean, JSON, Float, text
from sqlalchemy.orm import declarative_base, sessionmaker
import qrcode

app = Flask(__name__, template_folder='.')
app.secret_key = os.environ.get('SECRET_KEY', 'cs671-viva-g27')
app.config['SESSION_COOKIE_DOMAIN'] = False
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = False
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading',
                    ping_timeout=60, ping_interval=25, max_http_buffer_size=1_000_000)

ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'samiscrazy')

DB_PATH = os.environ.get('DB_PATH', 'quiz_platform.db')
engine = create_engine(
    f'sqlite:///{DB_PATH}',
    connect_args={"check_same_thread": False, "timeout": 30},
    pool_size=10, max_overflow=20, pool_timeout=30, pool_pre_ping=True
)
with engine.connect() as conn:
    conn.execute(text("PRAGMA journal_mode=WAL"))
    conn.execute(text("PRAGMA synchronous=NORMAL"))
    conn.execute(text("PRAGMA cache_size=10000"))
    conn.execute(text("PRAGMA temp_store=MEMORY"))
    conn.commit()

Base = declarative_base()
SessionLocal = sessionmaker(bind=engine)

class Quiz(Base):
    __tablename__ = 'quizzes'
    id = Column(String(36), primary_key=True)
    title = Column(String(255), nullable=False)
    pin = Column(String(6), unique=True, nullable=False)
    admin_id = Column(String(36), nullable=False)
    questions_data = Column(JSON, nullable=False)
    active_question_id = Column(Integer, nullable=True)
    active_question_open_time = Column(DateTime, nullable=True)
    state = Column(String(50), default='idle')
    created_at = Column(DateTime, default=datetime.utcnow)

class QuizSession(Base):
    __tablename__ = 'quiz_sessions'
    id = Column(String(36), primary_key=True)
    quiz_id = Column(String(36), nullable=False)
    username = Column(String(255), nullable=False)
    correct_count = Column(Integer, default=0)
    speed_points = Column(Integer, default=0)
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
    partial_score = Column(Integer, default=0)
    speed_points = Column(Integer, default=0)
    time_taken = Column(Float, nullable=True)
    answered_at = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(engine)

# ─── IN-MEMORY LEADERBOARD CACHE ───────────────────────────────────────────────
# {quiz_id: {session_id: {username, correct, speed_points}}}
_lb_cache = defaultdict(dict)
_lb_lock = threading.Lock()
_lb_pending = set()
_lb_last_broadcast = {}
_LB_THROTTLE = 0.25  # broadcast at most 4x/second per quiz

def _lb_update(quiz_id, sess_id, username, correct, speed):
    with _lb_lock:
        _lb_cache[quiz_id][sess_id] = {'username': username, 'correct': correct,
                                        'speed_points': speed}
        _lb_pending.add(quiz_id)

def _get_sorted_lb(quiz_id):
    with _lb_lock:
        entries = list(_lb_cache[quiz_id].values())
    entries.sort(key=lambda x: (-x['correct'], -x['speed_points']))
    return entries

def _lb_broadcaster():
    while True:
        time.sleep(0.25)
        now = time.time()
        with _lb_lock:
            to_send = set(_lb_pending)
            _lb_pending.clear()
        for quiz_id in to_send:
            if now - _lb_last_broadcast.get(quiz_id, 0) >= _LB_THROTTLE:
                _lb_last_broadcast[quiz_id] = now
                lb = _get_sorted_lb(quiz_id)
                socketio.emit('leaderboard_update', {'leaderboard': lb}, room=quiz_id)

threading.Thread(target=_lb_broadcaster, daemon=True).start()

def _prime_lb_cache(quiz_id):
    with _lb_lock:
        if quiz_id in _lb_cache:
            return
    db = SessionLocal()
    sessions = db.query(QuizSession).filter(QuizSession.quiz_id == quiz_id).all()
    db.close()
    with _lb_lock:
        for s in sessions:
            _lb_cache[quiz_id][s.id] = {'username': s.username, 'correct': s.correct_count,
                                          'speed_points': s.speed_points}

# ─── ASYNC WRITE QUEUE ─────────────────────────────────────────────────────────
_write_queue = queue.Queue()

def _write_worker():
    while True:
        fn = _write_queue.get()
        try:
            fn()
        except Exception as e:
            logger.error(f"Write error: {e}")
        finally:
            _write_queue.task_done()

threading.Thread(target=_write_worker, daemon=True).start()

# ─── HELPERS ───────────────────────────────────────────────────────────────────

def gen_pin():
    return str(uuid.uuid4())[:6].upper()

def gen_qr(data):
    qr = qrcode.QRCode(version=1, box_size=8, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return b64encode(buf.getvalue()).decode()

def calc_speed_points(time_taken, time_limit=None):
    max_time = time_limit if time_limit else 30
    if time_taken is None or time_taken <= 0:
        return 500
    return int(max(0, 1 - time_taken / max_time) * 1000)

def check_answer_detailed(q, user_answer):
    t = q.get('type', 'mcq')
    if t == 'mcq':
        c = user_answer == str(q.get('correct_option'))
        return c, (1 if c else 0), not c
    elif t == 'true_false':
        c = (user_answer.lower() == 'true') == q.get('correct_answer')
        return c, (1 if c else 0), not c
    elif t == 'fill_up':
        c = user_answer.lower().strip() == q.get('correct_answer', '').lower().strip()
        return c, (1 if c else 0), not c
    elif t == 'multi_correct':
        try:
            user_set = set(int(x) for x in user_answer.split(',') if x.strip())
            correct_set = set(q.get('correct_options', []))
            wrong_set = set(range(len(q.get('options', [])))) - correct_set
            score = max(0, len(user_set & correct_set) - len(user_set & wrong_set))
            any_wrong = bool(user_set & wrong_set)
            return user_set == correct_set, score, any_wrong
        except:
            return False, 0, True
    return False, 0, True

def _get_correct_answer_str(q):
    t = q.get('type', 'mcq')
    if t == 'mcq':
        idx = q.get('correct_option', 0)
        opts = q.get('options', [])
        return opts[idx] if idx < len(opts) else str(idx)
    elif t == 'true_false':
        return str(q.get('correct_answer', '')).lower()
    elif t == 'fill_up':
        return q.get('correct_answer', '')
    elif t == 'multi_correct':
        idxs = q.get('correct_options', [])
        opts = q.get('options', [])
        return ', '.join(opts[i] for i in idxs if i < len(opts))
    return ''

# ─── ROUTES - STUDENT ──────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('login.html', prefill_pin=request.args.get('pin', ''), error=None)

@app.route('/join', methods=['GET', 'POST'])
def join():
    if request.method == 'GET':
        pin = request.args.get('pin', '').strip().upper()
        return redirect(url_for('index', pin=pin) if pin else url_for('index'))

    quiz_pin = request.form.get('quiz_pin', '').strip().upper()
    username = request.form.get('username', '').strip()

    if not quiz_pin or not username:
        return render_template('login.html', error='PIN and username required.', prefill_pin=quiz_pin)

    db = SessionLocal()
    quiz = db.query(Quiz).filter(Quiz.pin == quiz_pin).first()
    if not quiz:
        db.close()
        return render_template('login.html', error='Invalid quiz PIN.', prefill_pin=quiz_pin)

    quiz_id = quiz.id
    existing = db.query(QuizSession).filter(
        QuizSession.quiz_id == quiz_id,
        QuizSession.username.ilike(username)
    ).order_by(QuizSession.started_at.desc()).first()

    if existing and not existing.completed_at:
        eid = existing.id
        db.close()
        _prime_lb_cache(quiz_id)
        session.update({'session_id': eid, 'quiz_id': quiz_id, 'username': username, 'resuming': True})
        return redirect(url_for('quiz_page'))

    sess_id = str(uuid.uuid4())
    db.add(QuizSession(id=sess_id, quiz_id=quiz_id, username=username))
    db.commit()
    db.close()

    _prime_lb_cache(quiz_id)
    _lb_update(quiz_id, sess_id, username, 0, 0)
    session.update({'session_id': sess_id, 'quiz_id': quiz_id, 'username': username, 'resuming': False})
    return redirect(url_for('quiz_page'))

@app.route('/quiz')
def quiz_page():
    if 'session_id' not in session:
        return redirect(url_for('index'))
    db = SessionLocal()
    quiz = db.query(Quiz).filter(Quiz.id == session['quiz_id']).first()
    qs = db.query(QuizSession).filter(QuizSession.id == session['session_id']).first()
    if not quiz or not qs:
        db.close()
        return redirect(url_for('index'))
    if qs.completed_at:
        db.close()
        return redirect(url_for('result'))
    active_q_id = quiz.active_question_id
    resuming = session.pop('resuming', False)
    db.close()
    return render_template('viva.html', quiz_id=quiz.id, username=session['username'],
                           active_question_id=active_q_id, resuming=resuming)


@app.route('/my_answers')
def my_answers():
    if 'session_id' not in session:
        return jsonify([])
    db = SessionLocal()
    answers = db.query(Answer).filter(Answer.session_id == session['session_id']).all()
    db.close()
    return jsonify([a.question_id for a in answers])

@app.route('/submit', methods=['POST'])
def submit():
    if 'session_id' not in session:
        return jsonify({'error': 'not logged in'}), 401

    # force=True handles Content-Type issues, silent=True prevents crashing on bad JSON
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({'error': 'Invalid JSON'}), 400

    try:
        q_id = int(data.get('question_id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid question_id'}), 400
        
    user_answer = data.get('answer', '')
    time_taken = float(data.get('time_taken', 0))
    sess_id = session['session_id']
    quiz_id = session['quiz_id']
    username = session['username']

    db = SessionLocal()
    quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
    if not quiz or q_id >= len(quiz.questions_data):
        db.close()
        return jsonify({'error': 'Invalid'}), 400

    existing_ans = db.query(Answer).filter(
        Answer.session_id == sess_id, Answer.question_id == q_id).first()
    if existing_ans:
        spd = existing_ans.speed_points
        ic = existing_ans.is_correct
        db.close()
        return jsonify({'ok': True, 'is_correct': ic, 'already': True, 'speed_points': spd})

    q = quiz.questions_data[q_id]
    db.close()

    is_correct, partial_score, any_wrong = check_answer_detailed(q, user_answer)
    spd = calc_speed_points(time_taken, q.get('time_limit')) if not any_wrong else 0

    with _lb_lock:
        entry = _lb_cache[quiz_id].get(sess_id, {'username': username, 'correct': 0, 'speed_points': 0})
    new_correct = entry['correct'] + (1 if is_correct else 0)
    new_speed = entry['speed_points'] + spd
    _lb_update(quiz_id, sess_id, username, new_correct, new_speed)
    # Emit immediately so first correct answer shows up right away
    socketio.emit('leaderboard_update', {'leaderboard': _get_sorted_lb(quiz_id)}, room=quiz_id)

    answer_id, now = str(uuid.uuid4()), datetime.utcnow()

    def _write():
        db2 = SessionLocal()
        try:
            db2.add(Answer(id=answer_id, quiz_id=quiz_id, session_id=sess_id,
                           question_id=q_id, user_answer=user_answer,
                           is_correct=is_correct, partial_score=partial_score,
                           speed_points=spd, time_taken=time_taken, answered_at=now))
            qs = db2.query(QuizSession).filter(QuizSession.id == sess_id).first()
            if qs:
                if is_correct:
                    qs.correct_count += 1
                qs.speed_points += spd
            db2.commit()
        except Exception as e:
            db2.rollback()
            logger.error(f"Submit write: {e}")
        finally:
            db2.close()

    _write_queue.put(_write)
    return jsonify({'ok': True, 'is_correct': is_correct, 'speed_points': spd})

@app.route('/result')
def result():
    if 'session_id' not in session:
        return redirect(url_for('index'))
    db = SessionLocal()
    qs = db.query(QuizSession).filter(QuizSession.id == session['session_id']).first()
    quiz = db.query(Quiz).filter(Quiz.id == session['quiz_id']).first()
    if not qs or not quiz:
        db.close()
        return redirect(url_for('index'))
    
    # Finalize session if not already done
    if not qs.completed_at:
        qs.completed_at = datetime.utcnow()
        db.commit()
        
    correct = qs.correct_count
    total = len(quiz.questions_data)
    speed = qs.speed_points
    db.close()
    return render_template('viva.html', result_view=True, correct=correct, total=total, speed=speed, username=session['username'])

# ─── ROUTES - ADMIN ────────────────────────────────────────────────────────────

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            session['admin'] = True
            return redirect(url_for('admin_dashboard'))
        return render_template('admin_login.html', error='Invalid password')
    return render_template('admin_login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin', None)
    return redirect(url_for('admin_login'))

@app.route('/admin')
def admin_dashboard():
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    db = SessionLocal()
    quizzes = db.query(Quiz).order_by(Quiz.created_at.desc()).all()
    db.close()
    return render_template('admin.html', quizzes=quizzes)

@app.route('/admin/quizzes')
def admin_get_quizzes():
    if not session.get('admin'):
        return '', 403
    db = SessionLocal()
    quizzes = db.query(Quiz).order_by(Quiz.created_at.desc()).all()
    res = [{'id': q.id, 'title': q.title, 'pin': q.pin, 'state': q.state, 
            'active_question_id': q.active_question_id} for q in quizzes]
    db.close()
    return jsonify(res)

@app.route('/admin/live/<quiz_id>')
def admin_live_view(quiz_id):
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    db = SessionLocal()
    quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
    if not quiz:
        db.close()
        return "Quiz not found", 404
    # Plain data for template
    q_data = {        'id': quiz.
(Content truncated due to size limit. Use line ranges to read remaining content)