import sys
import pathlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from routes.auth import update_sql_dump_password
from models import db, User

app = create_app()

with app.app_context():
    client = app.test_client()
    
    # Check data-final.sql before
    sql_path = pathlib.Path("data-final.sql")
    content_before = sql_path.read_text(encoding="utf-8")
    assert "'DEAN-AAlba','password'" in content_before, "Original password not found in data-final.sql"
    
    # 1. Login as DEAN-AAlba with current password
    login_res = client.post("/api/auth/login", json={"username": "DEAN-AAlba", "password": "password"})
    assert login_res.status_code == 200, f"Login failed: {login_res.get_json()}"
    print("[TEST] Login successful with 'password'.")
    
    # 2. Change password via /api/auth/change-credentials
    change_res = client.post(
        "/api/auth/change-credentials",
        json={"current_password": "password", "new_password": "DeanSecure2026!"}
    )
    assert change_res.status_code == 200, f"Password change failed: {change_res.get_json()}"
    print("[TEST] Password change endpoint succeeded.")
    
    # 3. Verify data-final.sql was updated
    content_after = sql_path.read_text(encoding="utf-8")
    assert "'DEAN-AAlba','DeanSecure2026!'" in content_after, "data-final.sql was not updated with new password!"
    print("[TEST] data-final.sql confirmed updated with 'DeanSecure2026!'.")
    
    # 4. Verify old password no longer works
    client.post("/api/auth/logout")
    fail_res = client.post("/api/auth/login", json={"username": "DEAN-AAlba", "password": "password"})
    assert fail_res.status_code == 401, "Old password still allowed login!"
    print("[TEST] Old password rejected correctly.")
    
    # 5. Verify new password logs in
    ok_res = client.post("/api/auth/login", json={"username": "DEAN-AAlba", "password": "DeanSecure2026!"})
    assert ok_res.status_code == 200, "New password failed login!"
    print("[TEST] New password login successful.")
    
    # 6. Revert back to default 'password' and verify data-final.sql updates back
    revert_res = client.post(
        "/api/auth/change-credentials",
        json={"current_password": "DeanSecure2026!", "new_password": "password"}
    )
    assert revert_res.status_code == 200, f"Reverting password failed: {revert_res.get_json()}"
    content_reverted = sql_path.read_text(encoding="utf-8")
    assert "'DEAN-AAlba','password'" in content_reverted, "data-final.sql was not reverted back!"
    print("[TEST] data-final.sql confirmed successfully reverted back to default 'password'.")
    
    print("ALL TESTS PASSED!")
