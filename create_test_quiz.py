from app import app, Quiz, SessionLocal
import json
import uuid

# Load questions
with open('questions.json') as f:
    questions = json.load(f)

# Create test quiz
db = SessionLocal()
quiz = Quiz(
    id=str(uuid.uuid4()),
    title="Test Quiz",
    pin="ABCD12",
    admin_id="admin",
    questions_data=questions,
    state='idle'
)
db.add(quiz)
db.commit()
print(f"✓ Test quiz created!")
print(f"✓ PIN: ABCD12")
print(f"✓ Questions: {len(questions)}")
db.close()
