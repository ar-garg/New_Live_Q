# Live_Q

Real-time quiz platform for classrooms. Teachers control questions live, students answer on their phones, leaderboard updates instantly.

---

## Live URLs

| Purpose | URL |
|---|---|
| Student Join | https://live-q-2xag.onrender.com |
| Admin Dashboard | https://live-q-2xag.onrender.com/admin |

---

## Question Types

| Type | Key | Description |
|---|---|---|
| Multiple Choice | `mcq` | One correct option |
| True / False | `true_false` | Boolean answer |
| Fill in the Blank | `fill_up` | Text match |
| Multi-correct | `multi_correct` | Multiple correct options |

---

## Question JSON Format

```json
[
  {
    "id": 0,
    "type": "mcq",
    "q": "Question text?",
    "options": ["A", "B", "C", "D"],
    "correct_option": 1,
    "time_limit": null
  },
  {
    "id": 1,
    "type": "true_false",
    "q": "Statement to evaluate.",
    "correct_answer": false,
    "time_limit": null
  },
  {
    "id": 2,
    "type": "fill_up",
    "q": "The answer is ___.",
    "correct_answer": "answer",
    "time_limit": null
  },
  {
    "id": 3,
    "type": "multi_correct",
    "q": "Select all that apply.",
    "options": ["A", "B", "C", "D"],
    "correct_options": [0, 2],
    "time_limit": null
  }
]
```

`time_limit`: `null` means teacher closes manually. A number (seconds) auto-closes the question on the student side.

---

## Scoring

| Component | Rule |
|---|---|
| Correct answer | +1 to correct count |
| Speed bonus | 0–1000 based on time taken, only awarded on a fully correct answer |
| Multi-correct | Partial credit: correct selections minus wrong selections, min 0 |

**Leaderboard ranks by:** most correct answers first, then highest speed points as the tie-breaker.

---

## Teacher Workflow

1. Go to `/admin` and log in
2. Create a quiz — paste the questions JSON and give it a title
3. Share the PIN or QR code with students
4. Open questions one at a time
5. Close a question to reveal the correct answer to all students
6. Export stats as XLSX when done

---

## Student Workflow

1. Go to the join URL or scan the QR code
2. Enter the PIN and your name / student ID
3. Answer each question when it opens — a speed bonus is awarded for fast correct answers
4. The correct answer is revealed after the teacher closes the question
5. If you disconnect, rejoin with the same name to resume your session

---

## Scoring Export (XLSX)

Two sheets:

**Sheet 1 — Student Details**
`Username | Total Correct | Speed Points | Q1 Result | Q1 Speed Pts | Q2 Result | ...`

Result values: `Correct`, `Wrong`, `N/A`

**Sheet 2 — Question Summary**
`Q# | Question | Type | Correct | Wrong | Not Attempted | Correct %`

---

## Running Locally

```bash
pip install -r requirements.txt
python app.py
```

Server runs at `http://localhost:5001`

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | `cs671-viva-g27` | Flask session key |
| `ADMIN_PASSWORD` | `samiscrazy` | Admin login password |
| `PORT` | `5001` | Port to run on |
| `DB_PATH` | `quiz_platform.db` | SQLite database path |

---

## Tech Stack

| Component | Technology |
|---|---|
| Backend | Flask + Flask-SocketIO (threading mode) |
| Database | SQLite + SQLAlchemy (WAL mode) |
| Frontend | HTML + CSS + JavaScript |
| Realtime | WebSockets via Socket.IO |
| QR Generation | qrcode + Pillow |
| Export | openpyxl |

---

## Resetting Data

Wipe everything and start fresh:

```bash
del quiz_platform.db   # Windows
rm quiz_platform.db    # Mac/Linux
python app.py
```

Clear only student sessions (keep quizzes):

```bash
python -c "from app import SessionLocal, Answer, QuizSession; db=SessionLocal(); db.query(Answer).delete(); db.query(QuizSession).delete(); db.commit(); db.close(); print('Cleared')"
```

---

## Notes

- Free Render tier spins down after 15 min of inactivity — first request takes ~30s to wake up
- SQLite database resets on Render redeploy (free tier has no persistent disk) — create quizzes fresh each session or upgrade to a paid tier with persistent disk
- For persistent storage across deploys, set `DB_PATH=/tmp/quiz_platform.db` or switch to PostgreSQL