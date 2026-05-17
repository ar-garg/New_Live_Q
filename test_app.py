from app import app
import traceback

with app.app_context():
    try:
        with app.test_client() as client:
            resp = client.get('/')
            print(f"Status: {resp.status_code}")
            content = resp.data.decode()
            print(f"Content length: {len(content)}")
            print("=== First 2000 chars ===")
            print(content[:2000])
    except Exception as e:
        print(f"Exception: {type(e).__name__}: {e}")
        traceback.print_exc()
