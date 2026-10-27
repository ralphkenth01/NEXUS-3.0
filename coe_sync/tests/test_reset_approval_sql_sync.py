import sys
import pathlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from models import db, User, PasswordResetRequest

app = create_app()

with app.app_context():
    client = app.test_client()
    sql_path = Path("data-final.sql")

    # 1. Login as SMIDeJesus, change password to 'Student2026!'
    client.post("/api/auth/login", json={"username": "SMIDeJesus", "password": "password"})
    client.post("/api/auth/change-credentials", json={"current_password": "password", "new_password": "Student2026!"})
    client.post("/api/auth/logout")

    # Verify data-final.sql contains 'Student2026!'
    content = sql_path.read_text(encoding="utf-8")
    assert "'SMIDeJesus','Student2026!'" in content, "data-final.sql missing changed password"
    print("[TEST] Student password updated and written to data-final.sql.")

    # 2. Student requests password reset
    forgot_res = client.post("/api/auth/forgot-password", json={"username": "SMIDeJesus", "reason": "Forgot student pass"})
    assert forgot_res.status_code in (200, 201), "Forgot password request failed"
    req_id = forgot_res.get_json()["request_id"]
    print(f"[TEST] Reset request submitted (id: {req_id}).")

    # 3. Login as it_team and approve reset
    client.post("/api/auth/login", json={"username": "it_team", "password": "password"})
    approve_res = client.post(f"/api/auth/reset-requests/{req_id}/approve")
    assert approve_res.status_code == 200, f"Approve failed: {approve_res.get_json()}"
    print("[TEST] IT team approved password reset.")

    # 4. Verify data-final.sql updated back to 'nexus2026'
    content_approved = sql_path.read_text(encoding="utf-8")
    assert "'SMIDeJesus','nexus2026'" in content_approved, "data-final.sql not updated to default 'nexus2026' on approval!"
    print("[TEST] data-final.sql confirmed updated to default 'nexus2026' upon IT approval.")

    # 5. Clean up: reset SMIDeJesus back to 'password'
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"username": "SMIDeJesus", "password": "nexus2026"})
    client.post("/api/auth/change-credentials", json={"current_password": "nexus2026", "new_password": "password"})
    content_final = sql_path.read_text(encoding="utf-8")
    assert "'SMIDeJesus','password'" in content_final
    print("[TEST] Cleanup completed. SMIDeJesus reset to 'password' in data-final.sql.")

    print("ALL RESET SYNC TESTS PASSED!")
