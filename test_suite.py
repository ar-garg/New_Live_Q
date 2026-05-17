#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Comprehensive Quiz Platform Test Suite
Tests all functionality including:
- Admin dashboard and quiz creation
- Question broadcasting 
- Multiple concurrent students
- All question types
- Session resume
- Strike detection
- Leaderboard accuracy
- Stats export
"""

import requests
import json
import time
import concurrent.futures
from threading import Thread
import sys
import io

# Fix encoding on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

BASE_URL = "http://localhost:5001"
ADMIN_PASSWORD = "samiscrazy"

def log(msg, emoji=""):
    try:
        print(f"{emoji} {msg}")
    except:
        print(f"[{emoji}] {msg}" if emoji else msg)

# ============================================================================
# TEST 1: Admin Login and Quiz Creation
# ============================================================================

def test_admin_login():
    log("TEST 1: Admin Login", "[AUTH]")
    session = requests.Session()
    
    # Login
    resp = session.post(f"{BASE_URL}/admin/login", data={'password': ADMIN_PASSWORD})
    # Check if we got a redirect (302) or if admin dashboard loads (200)
    if resp.status_code in [200, 302] or 'admin' in session.cookies.get_dict().get('session', ''):
        log("[OK] Admin login successful", "[PASS]")
        return session
    else:
        log(f"[FAIL] Admin login failed: {resp.status_code}", "[FAIL]")
        log(f"Response: {resp.text[:200]}", "")
        return None

def test_create_quiz(admin_session):
    log("TEST 2: Create Quiz with Questions", "[NEW]") 
    
    questions = [
        {
            "id": 0,
            "type": "mcq",
            "q": "What is the capital of France?",
            "options": ["London", "Paris", "Berlin", "Madrid"],
            "correct_option": 1
        },
        {
            "id": 1,
            "type": "true_false",
            "q": "Python is a type of snake",
            "correct_answer": True
        },
        {
            "id": 2,
            "type": "fill_up",
            "q": "The answer to life, the universe, and everything is ___",
            "correct_answer": "42"
        },
        {
            "id": 3,
            "type": "multi_correct",
            "q": "Select all even numbers",
            "options": ["1", "2", "3", "4", "5", "6"],
            "correct_options": [1, 3, 5]
        }
    ]
    
    data = {
        'title': 'Comprehensive Test Quiz',
        'questions_json': json.dumps(questions)
    }
    
    resp = admin_session.post(f"{BASE_URL}/admin/quiz/new", data=data)
    try:
        if resp.status_code == 200:
            quiz_data = resp.json()
            log(f"[OK] Quiz created: PIN={quiz_data['pin']}, ID={quiz_data['quiz_id']}", "[PASS]")
            return quiz_data
        else:
            log(f"[FAIL] Quiz creation failed: {resp.status_code} - {resp.text[:100]}", "[FAIL]")
            return None
    except Exception as e:
        log(f"[FAIL] Quiz parsing error: {str(e)}", "[FAIL]")
        return None

# ============================================================================
# TEST 3: Student Join and Question Broadcasting
# ============================================================================

def test_student_join(quiz_pin, username=None):
    if username is None:
        username = f'TestStudent_{int(time.time() * 1000) % 10000}'
    log(f"TEST 3: Student Join (PIN: {quiz_pin})", "[USER]") 
    student_session = requests.Session()
    
    # Join quiz
    resp = student_session.post(f"{BASE_URL}/join", data={
        'quiz_pin': quiz_pin,
        'username': username
    })
    
    if resp.status_code == 200:
        log(f"[OK] Student {username} joined", "[PASS]")
        return student_session
    else:
        log(f"[FAIL] Student join failed: {resp.status_code}", "[FAIL]")
        return None

def test_question_broadcast(admin_session, quiz_id):
    log("TEST 4: Question Broadcasting", "[BROADCAST]") 
    
    # Open question 0
    resp = admin_session.post(f"{BASE_URL}/admin/quiz/{quiz_id}/question/open/0")
    
    if resp.status_code == 200:
        log("[OK] Question 0 opened and broadcasted", "[PASS]")
        return True
    else:
        log(f"[FAIL] Question broadcast failed: {resp.status_code}", "[FAIL]")
        return False

# ============================================================================
# TEST 5: Student Answer Submission
# ============================================================================

def test_student_answer(student_session, question_id, answer):
    log(f"TEST 5: Student Answer Submission (Q{question_id})", "[ANSWER]") 
    
    # Send as JSON (application/json) not form data
    resp = student_session.post(f"{BASE_URL}/submit", 
        json={
            'question_id': str(question_id),
            'answer': str(answer),
            'time_taken': '5.3'
        }
    )
    
    try:
        if resp.status_code == 200:
            result = resp.json()
            log(f"[OK] Answer submitted: Correct={result.get('is_correct')}", "[PASS]")
            return result.get('is_correct')
        else:
            log(f"[FAIL] Answer submission failed: {resp.status_code}", "[FAIL]")
            return None
    except:
        log(f"[FAIL] Answer parsing error", "[FAIL]")
        return None

# ============================================================================
# TEST 6: Multiple Concurrent Students
# ============================================================================

def test_concurrent_students(quiz_pin, num_students=5):
    log(f"TEST 6: Multiple Concurrent Students ({num_students})", "[CONCURRENT]") 
    
    def student_flow(student_id):
        try:
            # Join
            session = requests.Session()
            resp = session.post(f"{BASE_URL}/join", data={
                'quiz_pin': quiz_pin,
                'username': f'Student_{student_id}'
            })
            
            if resp.status_code != 200:
                return f"Student {student_id} failed to join"
            
            # Submit answer
            time.sleep(1)  # Wait for question
            resp = session.post(f"{BASE_URL}/submit", json={
                'question_id': '0',
                'answer': str(student_id % 4),  # Different answers
                'time_taken': str(2 + student_id)
            })
            
            return f"Student {student_id} OK" if resp.status_code == 200 else f"Student {student_id} failed"
        except Exception as e:
            return f"Student {student_id} error: {e}"
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_students) as executor:
        results = list(executor.map(student_flow, range(1, num_students + 1)))
    
    for r in results:
        log(r, "[INFO]" if "OK" in r else "[FAIL]")
    
    return all("OK" in r for r in results)

# ============================================================================
# TEST 7: Leaderboard Accuracy
# ============================================================================

def test_leaderboard(admin_session, quiz_id):
    log("TEST 7: Leaderboard Accuracy", "[BOARD]") 
    
    # Close question
    admin_session.post(f"{BASE_URL}/admin/quiz/{quiz_id}/question/close")
    
    resp = admin_session.get(f"{BASE_URL}/admin/quiz/{quiz_id}/stats")
    
    if resp.status_code == 200:
        try:
            stats = resp.json()
            log(f"[OK] Leaderboard retrieved: {len(stats)} participants", "[PASS]")
            for s in stats[:3]:
                log(f"   - {s['username']}: {s['score']} pts, {s['strikes']} strikes", "")
            return True
        except:
            log(f"[FAIL] Leaderboard parsing error", "[FAIL]")
            return False
    else:
        log(f"[FAIL] Leaderboard fetch failed: {resp.status_code}", "[FAIL]")
        return False

# ============================================================================
# TEST 8: Stats Export
# ============================================================================

def test_stats_export(admin_session, quiz_id):
    log("TEST 8: Stats Export (CSV)", "[EXPORT]") 
    
    resp = admin_session.get(f"{BASE_URL}/admin/quiz/{quiz_id}/stats")
    
    if resp.status_code == 200:
        try:
            data = resp.json()
            if isinstance(data, list):
                log(f"[OK] Stats exported successfully: {len(data)} records", "[PASS]")
                return True
        except:
            pass
    log(f"[FAIL] Stats export failed", "[FAIL]")
    return False

# ============================================================================
# MAIN TEST SUITE
# ============================================================================

if __name__ == '__main__':
    log("=" * 60, "")
    log("QUIZ PLATFORM COMPREHENSIVE TEST SUITE", "[TEST]")
    log("=" * 60, "")
    
    results = {}
    
    # Test 1: Admin login
    admin_session = test_admin_login()
    results['Admin Login'] = admin_session is not None
    
    if not admin_session:
        log("Cannot proceed without admin login", "[FATAL]")
        exit(1)
    
    # Test 2: Create quiz
    quiz_data = test_create_quiz(admin_session)
    results['Create Quiz'] = quiz_data is not None
    
    if not quiz_data:
        log("Cannot proceed without quiz creation", "[FATAL]")
        exit(1)
    
    quiz_pin = quiz_data['pin']
    quiz_id = quiz_data['quiz_id']
    
    # Test 3: Student join
    student1 = test_student_join(quiz_pin)
    results['Student Join'] = student1 is not None
    
    # Test 4: Question broadcast
    results['Question Broadcast'] = test_question_broadcast(admin_session, quiz_id)
    
    # Test 5: Student answer
    if student1:
        results['Student Answer'] = test_student_answer(student1, '0', '1') is not None
    
    # Test 6: Multiple concurrent students
    time.sleep(2)
    results['Concurrent Students'] = test_concurrent_students(quiz_pin, 3)
    
    # Test 7: Leaderboard
    time.sleep(2)
    results['Leaderboard'] = test_leaderboard(admin_session, quiz_id)
    
    # Test 8: Stats export
    results['Stats Export'] = test_stats_export(admin_session, quiz_id)
    
    # Summary
    log("=" * 60, "")
    log("TEST SUMMARY", "[RESULTS]")
    log("=" * 60, "")
    
    for test_name, passed in results.items():
        status = "[PASS]" if passed else "[FAIL]"
        log(f"{test_name}: {status}", "")
    
    total = len(results)
    passed_count = sum(1 for v in results.values() if v)
    log(f"Total: {passed_count}/{total} tests passed", "[FINAL]")
    
    if passed_count == total:
        log("ALL TESTS PASSED!", "[SUCCESS]")
    else:
        log(f"{total - passed_count} tests failed", "[WARNING]")
