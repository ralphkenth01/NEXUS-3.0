import sys
import pathlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from models import db, User, Section

app = create_app()

with app.app_context():
    client = app.test_client()
    sql_path = pathlib.Path("data-final.sql")

    # 1. Login as Program Chair (CHAIR-RGabrillo)
    login_res = client.post("/api/auth/login", json={"username": "CHAIR-RGabrillo", "password": "password"})
    assert login_res.status_code == 200, f"Chair login failed: {login_res.get_json()}"
    print("[TEST] Chair login successful.")

    # 2. Add Section via POST /api/sections
    test_sec_name = "MEE - 9Z"
    test_mayor = "Juan Dela Cruz"
    create_res = client.post("/api/sections", json={
        "name": test_sec_name,
        "program": "MEE",
        "year_level": 1,
        "student_count": 35,
        "class_mayor": test_mayor,
    })
    assert create_res.status_code == 201, f"Create section failed: {create_res.get_json()}"
    data = create_res.get_json()["data"]
    sec_id = data["id"]
    print(f"[TEST] Created section #{sec_id} '{test_sec_name}' with Mayor '{test_mayor}'.")

    # Verify user account was automatically created with username == section name
    user_account = User.query.filter_by(username=test_sec_name).first()
    assert user_account is not None, f"User account with username '{test_sec_name}' was not created!"
    assert user_account.role == "STUDENT", f"Expected role STUDENT, got {user_account.role}"
    assert user_account.reference_id == sec_id, f"Expected ref_id {sec_id}, got {user_account.reference_id}"
    print(f"[TEST] Confirmed User account created: username='{user_account.username}', role='{user_account.role}', ref_id={user_account.reference_id}.")

    # Verify data-final.sql was updated with section and user
    sql_content = sql_path.read_text(encoding="utf-8")
    assert f"'{test_sec_name}'" in sql_content, "data-final.sql missing created section name"
    print("[TEST] data-final.sql verified containing new section and student user.")

    # Verify Mayor can login using the Section Name as username
    mayor_login = client.post("/api/auth/login", json={"username": test_sec_name, "password": "password"})
    assert mayor_login.status_code == 200, f"Mayor login failed: {mayor_login.get_json()}"
    print(f"[TEST] Class Mayor successfully logged in with username '{test_sec_name}'.")

    # 3. Update Mayor Name and Section Name via PUT /api/sections/<id> (Chair auth)
    client.post("/api/auth/login", json={"username": "CHAIR-RGabrillo", "password": "password"})
    updated_sec_name = "MEE - 9Y"
    updated_mayor = "Maria Santos"
    update_res = client.put(f"/api/sections/{sec_id}", json={
        "name": updated_sec_name,
        "program": "MEE",
        "year_level": 1,
        "class_mayor": updated_mayor,
    })
    assert update_res.status_code == 200, f"Update section failed: {update_res.get_json()}"
    print(f"[TEST] Section updated to '{updated_sec_name}' and Mayor to '{updated_mayor}'.")

    # Verify Mayor user username changed to match new section name
    updated_user = User.query.filter_by(reference_id=sec_id, role="STUDENT").first()
    assert updated_user.username == updated_sec_name, f"Expected username '{updated_sec_name}', got '{updated_user.username}'"
    print(f"[TEST] Confirmed User account username updated to '{updated_sec_name}'.")

    # Verify login with updated username
    client.post("/api/auth/logout")
    updated_login = client.post("/api/auth/login", json={"username": updated_sec_name, "password": "password"})
    assert updated_login.status_code == 200, f"Login with updated section username failed: {updated_login.get_json()}"
    print(f"[TEST] Class Mayor successfully logged in with updated section name '{updated_sec_name}'.")

    # 4. Clean up / Delete section via DELETE /api/sections/<id>
    client.post("/api/auth/login", json={"username": "CHAIR-RGabrillo", "password": "password"})
    del_res = client.delete(f"/api/sections/{sec_id}")
    assert del_res.status_code == 200, f"Delete section failed: {del_res.get_json()}"
    
    assert db.session.get(Section, sec_id) is None, "Section not deleted from DB!"
    assert User.query.filter_by(reference_id=sec_id, role="STUDENT").first() is None, "Linked user not deleted!"
    print("[TEST] Cleaned up section and linked user successfully.")

    sql_final = sql_path.read_text(encoding="utf-8")
    assert f"'{updated_sec_name}'" not in sql_final, "Deleted section still found in data-final.sql!"
    print("[TEST] data-final.sql confirmed cleaned up.")

    print("\nALL SECTION MAYOR AUTOMATION TESTS PASSED SUCCESSFULLY!")
