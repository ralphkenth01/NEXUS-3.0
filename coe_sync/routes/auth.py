"""COE SYNC 2026 - Authentication, RBAC & Role Impersonator Routes

Endpoints:
- POST /api/auth/login: Authenticate credentials and initialize session
- POST /api/auth/logout: Terminate current session
- POST /api/auth/impersonate: Swap active viewing role for live demonstration
- GET /api/auth/me: Retrieve active user and session profile
"""

from functools import wraps
from flask import (
    Blueprint,
    request,
    jsonify,
    session,
    redirect,
    url_for,
    render_template,
    current_app,
)
from pathlib import Path
import re
from models import db, User, Faculty, Section, AuditLog, PasswordResetRequest

auth_bp = Blueprint("auth", __name__)
auth_views = Blueprint("auth_views", __name__)


def update_sql_dump_password(username: str, new_password: str, old_username: str = None) -> bool:
    """
    Synchronize password (and optionally username) changes directly into data-final.sql.
    Finds the user's tuple in INSERT INTO `users` VALUES (...) and updates the password literal.
    """
    try:
        sql_path = Path(__file__).resolve().parent.parent / "data-final.sql"
        if not sql_path.exists():
            return False

        content = sql_path.read_text(encoding="utf-8")
        target_user = old_username if old_username else username
        safe_pwd = new_password.replace("'", "\\'")

        # Match (id, 'TARGET_USERNAME', 'OLD_PASSWORD', ...)
        # Capture group 1: (id, 'TARGET_USERNAME', '
        # Capture group 2: ', ...
        pattern = re.compile(r"(\(\s*\d+\s*,\s*'" + re.escape(target_user) + r"'\s*,\s*')[^']*(')")
        
        if old_username and old_username != username:
            # If username also changed, update username as well
            user_pattern = re.compile(r"(\(\s*\d+\s*,\s*')(" + re.escape(old_username) + r")('\s*,\s*')[^']*(')")
            new_content, count = user_pattern.subn(rf"\g<1>{re.escape(username)}\g<3>{safe_pwd}\g<4>", content)
        else:
            new_content, count = pattern.subn(rf"\g<1>{safe_pwd}\g<2>", content)

        if count > 0:
            sql_path.write_text(new_content, encoding="utf-8")
            return True
        return False
    except Exception as e:
        print(f"[COE SYNC] Warning: Failed to sync password to data-final.sql for {username}: {e}")
        return False


def append_sql_dump_user(user_id: int, username: str, password: str, role: str, dept: str = None, ref_id: int = None) -> bool:
    """
    Append a newly created user tuple to the INSERT INTO `users` statement in data-final.sql.
    """
    try:
        sql_path = Path(__file__).resolve().parent.parent / "data-final.sql"
        if not sql_path.exists():
            return False

        content = sql_path.read_text(encoding="utf-8")
        # Check if user is already present
        if f"'{username}'" in content:
            return update_sql_dump_password(username, password)

        safe_user = username.replace("'", "\\'")
        safe_pwd = password.replace("'", "\\'")
        safe_role = role.replace("'", "\\'")
        safe_dept = f"'{dept.replace(chr(39), chr(92) + chr(39))}'" if dept else "NULL"
        ref_val = str(ref_id) if ref_id is not None else "NULL"

        tuple_str = f",({user_id},'{safe_user}','{safe_pwd}','{safe_role}',{safe_dept},{ref_val});"
        
        # Replace the trailing semicolon of the INSERT INTO `users` statement
        insert_prefix = "INSERT INTO `users` VALUES ("
        if insert_prefix in content:
            # Find the line containing INSERT INTO `users`
            lines = content.splitlines(keepends=True)
            for idx, line in enumerate(lines):
                if line.startswith(insert_prefix):
                    trimmed = line.rstrip("\r\n")
                    if trimmed.endswith(";"):
                        lines[idx] = trimmed[:-1] + tuple_str + "\n"
                        sql_path.write_text("".join(lines), encoding="utf-8")
                        return True
        return False
    except Exception as e:
        print(f"[COE SYNC] Warning: Failed to append user to data-final.sql: {e}")
        return False


def remove_sql_dump_user(username: str) -> bool:
    """
    Remove a user's tuple from the INSERT INTO `users` statement in data-final.sql.
    """
    try:
        sql_path = Path(__file__).resolve().parent.parent / "data-final.sql"
        if not sql_path.exists():
            return False

        content = sql_path.read_text(encoding="utf-8")
        # Match user tuple: ,(\d+,'username',...) or (\d+,'username',...),
        escaped_u = re.escape(username)
        # Pattern for leading comma: ,\(\s*\d+\s*,\s*'username'[^\)]*\)
        p1 = re.compile(r",\(\s*\d+\s*,\s*'" + escaped_u + r"'[^\)]*\)")
        new_content, count = p1.subn("", content)
        if count == 0:
            # Maybe it's the very first item: (\d+,'username',...),
            p2 = re.compile(r"\(\s*\d+\s*,\s*'" + escaped_u + r"'[^\)]*\),")
            new_content, count = p2.subn("", content)

        if count > 0:
            sql_path.write_text(new_content, encoding="utf-8")
            return True
        return False
    except Exception as e:
        print(f"[COE SYNC] Warning: Failed to remove user {username} from data-final.sql: {e}")
        return False


def generate_faculty_username(name: str) -> str:
    """
    Generate username starting with 'FAC-' followed by First initial and Last name (compacted uppercase).
    Example: 'ENGR. RUTH B. SANGALANG-GABRILLO' -> 'FAC-RSANGALANGGABRILLO'
             'ENGR. ALBERTO C. CRUZ JR.' -> 'FAC-ACRUZ'
    """
    clean = re.sub(r"^(ENGR\.|DR\.|PROF\.|ARCH\.)\s+", "", name, flags=re.IGNORECASE).strip()
    # Strip any trailing suffixes like JR., SR., III, II, etc.
    clean = re.sub(r"[\s,]+(JR\.?|SR\.?|III|II|IV)\.?$", "", clean, flags=re.IGNORECASE).strip()
    
    parts = clean.split()
    if not parts:
        return "FAC-FACULTY"

    first_init = parts[0][0].upper()
    last_word = parts[-1].replace("-", "").replace(" ", "").upper()

    # Filter only alphanumeric characters
    clean_last = re.sub(r"[^A-Z0-9]", "", last_word)
    if not clean_last:
        clean_last = "FACULTY"
    return f"FAC-{first_init}{clean_last}"


def append_sql_dump_faculty(faculty_id: int, name: str, dept_full: str) -> bool:
    """
    Append a newly created faculty tuple to the INSERT INTO `coe facculty` statement in data-final.sql.
    """
    try:
        sql_path = Path(__file__).resolve().parent.parent / "data-final.sql"
        if not sql_path.exists():
            return False

        content = sql_path.read_text(encoding="utf-8")
        safe_name = name.replace("'", "\\'")
        safe_dept = dept_full.replace("'", "\\'")

        tuple_str = f",({faculty_id},'{safe_name}','','{safe_dept}','','');"

        insert_prefix = "INSERT INTO `coe facculty` VALUES ("
        if insert_prefix in content:
            lines = content.splitlines(keepends=True)
            for idx, line in enumerate(lines):
                if line.startswith(insert_prefix):
                    trimmed = line.rstrip("\r\n")
                    if trimmed.endswith(";"):
                        lines[idx] = trimmed[:-1] + tuple_str + "\n"
                        sql_path.write_text("".join(lines), encoding="utf-8")
                        return True
        return False
    except Exception as e:
        print(f"[COE SYNC] Warning: Failed to append faculty to data-final.sql: {e}")
        return False


def remove_sql_dump_faculty(faculty_id: int, name: str = None) -> bool:
    """
    Remove a faculty tuple from the INSERT INTO `coe facculty` statement in data-final.sql.
    """
    try:
        sql_path = Path(__file__).resolve().parent.parent / "data-final.sql"
        if not sql_path.exists():
            return False

        content = sql_path.read_text(encoding="utf-8")
        # Match ,(\d+,'name',...)
        p1 = re.compile(rf",\(\s*{faculty_id}\s*,\s*[^,]+[^\)]*\)")
        new_content, count = p1.subn("", content)
        if count == 0:
            p2 = re.compile(rf"\(\s*{faculty_id}\s*,\s*[^,]+[^\)]*\),")
            new_content, count = p2.subn("", content)

        if count > 0:
            sql_path.write_text(new_content, encoding="utf-8")
            return True
        return False
    except Exception as e:
        print(f"[COE SYNC] Warning: Failed to remove faculty #{faculty_id} from data-final.sql: {e}")
        return False




@auth_views.route("/login")
def login_page():
    """Render the dedicated Login Gateway."""
    if "user_id" in session:
        try:
            return redirect(url_for("views.dashboard"))
        except Exception:
            return redirect("/")
    show_demo = current_app.config.get("SHOW_DEMO_CREDENTIALS", True)
    return render_template("login.html", show_demo_credentials=show_demo)


def normalize_role(r: str) -> str:
    """Normalize NEXUS 2.0 and legacy role names to standard role identifier."""
    up = str(r or "").strip().upper()
    if up in ("CHAIR", "PROG_CHA"):
        return "CHAIR"
    if up in ("FACULTY", "FCLTY_DEPT"):
        return "FACULTY"
    if up in ("STUDENT", "STDN_MYR"):
        return "STUDENT"
    return up


def get_effective_role() -> str:
    """Return the currently active impersonated or authenticated role."""
    return normalize_role(session.get("active_role") or session.get("role") or "")


def get_effective_program() -> str:
    """Return the currently active program scope (e.g., MEE, CE, CPE, or None for Admin)."""
    if "active_program" in session:
        return session.get("active_program")
    if "program" in session:
        return session.get("program")
    user_id = session.get("user_id")
    if user_id:
        user = db.session.get(User, user_id)
        if user and user.program:
            return user.program
    return None


def get_effective_reference_id():
    """Return the currently active reference ID (Faculty.id or Section.id)."""
    if "active_reference_id" in session:
        return session.get("active_reference_id")
    return session.get("reference_id")


def login_required(f):
    """
    Session Guard Decorator.
    Redirects unauthenticated users to the Login Gateway for page routes,
    or returns HTTP 401 for REST API routes.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized: Authentication required."}), 401
            try:
                return redirect(url_for("auth_views.login_page"))
            except Exception:
                try:
                    return redirect(url_for("views.login_page"))
                except Exception:
                    return redirect("/login")
        return f(*args, **kwargs)

    return decorated_function




def require_roles(*roles):
    """
    Role-Based Access Control decorator.
    Enforces that the active session holds one of the specified roles (supporting aliases).
    Fails closed:
      - HTTP 401 Unauthorized if unauthenticated.
      - HTTP 403 Forbidden if active role lacks required permissions.
    """
    target_roles = {normalize_role(r) for r in roles} | {str(r).upper() for r in roles}

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if "user_id" not in session:
                return (
                    jsonify({"error": "Unauthorized: Authentication required."}),
                    401,
                )
            current_role = get_effective_role()
            norm_current = normalize_role(current_role)
            if not current_role or (current_role not in target_roles and norm_current not in target_roles):
                return (
                    jsonify({"error": "Unauthorized: Insufficient permissions"}),
                    403,
                )
            return f(*args, **kwargs)

        return decorated_function

    return decorator


@auth_bp.route("/login", methods=["POST"])
def login():
    """
    POST /api/auth/login
    Inputs: { "username": "...", "password": "..." } or form data.
    """
    data = request.get_json(silent=True) or request.form.to_dict()
    if not data:
        return jsonify({"error": "No login data provided"}), 400

    username = str(data.get("username", "")).strip()
    password = str(data.get("password", "")).strip()

    if not username or not password:
        return (
            jsonify({"error": "Username and password are required."}),
            400,
        )

    try:
        user = User.query.filter_by(username=username).first()
        if not user or not user.check_password(password):
            return (
                jsonify({"error": "Invalid username or password"}),
                401,
            )

        # Initialize session
        norm_role = normalize_role(user.role)
        session.clear()
        session["user_id"] = user.id
        session["username"] = user.username
        session["role"] = norm_role
        session["active_role"] = norm_role
        session["program"] = user.program
        session["active_program"] = user.program
        session["reference_id"] = user.reference_id
        session["active_reference_id"] = user.reference_id
        session["must_change_credentials"] = user.must_change_credentials
        session.permanent = True

        # Record login audit safely
        try:
            audit = AuditLog(
                user_id=user.id,
                action="USER_LOGIN",
                details=f"User #{user.id} ({user.username}, {user.role}, {user.program or 'ALL'}) logged in.",
            )
            db.session.add(audit)
            db.session.commit()
        except Exception as audit_err:
            db.session.rollback()
            print(f"[AUTH AUDIT WARNING] Could not record login audit: {audit_err}", flush=True)

        return (
            jsonify(
                {
                    "status": "success",
                    "message": "Login successful",
                    "redirect": "/schedules" if normalize_role(user.role) in ["STUDENT", "FACULTY"] else "/dashboard",
                    "role": norm_role,
                    "user": user.to_dict(),
                    "user_role": norm_role,
                    "user_program": user.program,
                    "program": user.program,
                    "active_program": user.program,
                    "must_change_credentials": user.must_change_credentials,
                    "active_role": norm_role,
                    "active_reference_id": user.reference_id,
                }
            ),
            200,
        )
    except Exception as err:
        print(f"[AUTH ERROR] Failed during login execution: {str(err)}", flush=True)
        return jsonify({"error": f"Internal authentication error: {str(err)}"}), 500


@auth_bp.route("/logout", methods=["POST"])
def logout():
    """POST /api/auth/logout - Terminate active session."""
    user_id = session.get("user_id")
    if user_id:
        audit = AuditLog(
            user_id=user_id,
            action="USER_LOGOUT",
            details=f"User #{user_id} logged out.",
        )
        db.session.add(audit)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

    session.clear()
    return (
        jsonify({"status": "success", "message": "Logged out successfully."}),
        200,
    )


@auth_bp.route("/demo-credentials", methods=["GET"])
def get_demo_credentials():
    """
    GET /api/auth/demo-credentials
    Dynamically returns current demo account credentials directly from database.
    Reflects any credential updates made via Profile / Settings immediately.
    """
    show_creds = current_app.config.get("SHOW_DEMO_CREDENTIALS", True)

    it_team = User.query.filter_by(username="it_team").first()
    dean = User.query.filter_by(username="DEAN-AAlba").first() or User.query.filter_by(role="DEAN").first()
    chair = User.query.filter_by(username="CHAIR-ATullao").first() or User.query.filter_by(role="CHAIR").first()
    admin = User.query.filter_by(username="admin").first()
    faculty = User.query.filter_by(username="FAC-RCALUAG").first() or User.query.filter_by(role="FACULTY").first()
    student = User.query.filter_by(username="SMIDeJesus").first() or User.query.filter_by(role="STUDENT").first()

    all_users = User.query.filter(User.username.in_([
        "it_team", "DEAN-AAlba", "CHAIR-ATullao", "CHAIR-MVillena", "CHAIR-RGabrillo", "CHAIR-RCastro",
        "FAC-RCALUAG", "FAC-JSANTOS", "FAC-KBUGAYONG", "SMIDeJesus"
    ])).all()

    accounts_list = [u.to_dict() for u in all_users]

    return (
        jsonify(
            {
                "status": "success",
                "show_demo_credentials": show_creds,
                "it_team": {
                    "username": it_team.username if it_team else "it_team",
                    "password": (
                        it_team.demo_password if it_team and it_team.demo_password else "password"
                    ),
                    "role": "IT Support Team (IT)",
                    "program": "COE-IT",
                },
                "dean": {
                    "username": dean.username if dean else "DEAN-AAlba",
                    "password": (
                        dean.demo_password if dean and dean.demo_password else "password"
                    ),
                    "role": "College Dean (DEAN)",
                    "program": "ALL",
                },
                "chair": {
                    "username": chair.username if chair else "CHAIR-ATullao",
                    "password": (
                        chair.demo_password if chair and chair.demo_password else "password"
                    ),
                    "role": "Program Chair (CHAIR)",
                    "program": chair.program if chair else "MEE",
                },
                "admin": {
                    "username": admin.username if admin else "admin",
                    "password": (
                        admin.demo_password if admin and admin.demo_password else "password"
                    ),
                    "role": "Administrator (ADMIN)",
                    "program": "ALL",
                },
                "faculty": {
                    "username": faculty.username if faculty else "FAC-RCALUAG",
                    "password": (
                        faculty.demo_password
                        if faculty and faculty.demo_password
                        else "password"
                    ),
                    "role": "Faculty (FACULTY)",
                    "program": faculty.program if faculty else "CE",
                },
                "student": {
                    "username": student.username if student else "SMIDeJesus",
                    "password": (
                        student.demo_password
                        if student and student.demo_password
                        else "password"
                    ),
                    "role": "Student Mayor (STUDENT)",
                    "program": student.program if student else "MEE",
                },
                "accounts": accounts_list,
            }
        ),
        200,
    )


@auth_bp.route("/update-credentials", methods=["POST"])
@auth_bp.route("/change-credentials", methods=["POST"])
@auth_bp.route("/change-password", methods=["POST"])
def change_credentials():
    """
    POST /api/auth/update-credentials or /api/auth/change-credentials or /api/auth/change-password
    Allows any user to change their username and/or password.
    Works both when already logged in (via session) or from the login screen (by providing current credentials).
    Writes new password hash to DB, commits, updates session, synchronizes with data-final.sql,
    and invalidates old credentials immediately.
    """
    data = request.get_json(silent=True) or {}
    current_password = data.get("current_password") or data.get("old_password", "")
    new_username = (data.get("new_username") or "").strip()
    new_password = data.get("new_password", "")

    user_id = session.get("user_id")
    user = db.session.get(User, user_id) if user_id else None

    # If no active session, lookup user by new_username, or by old_username/username in payload
    if not user:
        candidate_username = (data.get("username") or data.get("current_username") or new_username).strip()
        if candidate_username:
            user = User.query.filter_by(username=candidate_username).first()

    if not user:
        return jsonify({"error": "User account not found. Please log in or specify your valid username."}), 404

    # Verify current password
    if not current_password or not user.check_password(current_password):
        return jsonify({"error": "Current password is incorrect."}), 400

    changes = []

    # Update username if specified and changed
    old_user_login = user.username
    if new_username and new_username != user.username:
        if len(new_username) < 3:
            return jsonify({"error": "Username must be at least 3 characters long."}), 400

        existing = User.query.filter(User.username == new_username, User.id != user.id).first()
        if existing:
            return jsonify({"error": f"Username '{new_username}' is already taken."}), 409

        old_username = user.username
        user.username = new_username
        session["username"] = new_username
        changes.append(f"username changed from '{old_username}' to '{new_username}'")

    # Update password if specified
    if new_password:
        if len(new_password) < 6:
            return jsonify({"error": "New password must be at least 6 characters long."}), 400
        user.set_password(new_password)
        changes.append("password updated")

    if not changes:
        return jsonify({"error": "Please provide a new username or new password to update."}), 400

    user.must_change_credentials = False
    session["must_change_credentials"] = False

    # Sync password changes directly to data-final.sql
    if new_password:
        update_sql_dump_password(user.username, new_password, old_username=old_user_login if new_username else None)

    audit = AuditLog(
        user_id=user.id,
        action="CREDENTIALS_CHANGED",
        details=f"User #{user.id} updated credentials: {', '.join(changes)}.",
    )
    db.session.add(audit)
    db.session.commit()

    # If user changed credentials without prior active session, log them in automatically
    norm_role = normalize_role(user.role)
    if "user_id" not in session or session["user_id"] != user.id:
        session.clear()
        session["user_id"] = user.id
        session["username"] = user.username
        session["role"] = norm_role
        session["active_role"] = norm_role
        session["program"] = user.program
        session["active_program"] = user.program
        session["reference_id"] = user.reference_id
        session["active_reference_id"] = user.reference_id
        session["must_change_credentials"] = False
        session.permanent = True

    redirect_target = "/schedules" if normalize_role(user.role) in ["STUDENT", "FACULTY"] else "/dashboard"
    return (
        jsonify(
            {
                "status": "success",
                "message": "Password and credentials updated successfully.",
                "redirect": redirect_target,
                "user": user.to_dict(),
            }
        ),
        200,
    )


@auth_bp.route("/forgot-password", methods=["POST"])
def forgot_password_request():
    """
    POST /api/auth/forgot-password
    Public endpoint: Allows users who forgot their credentials to request IT/Admin to reset to default.
    Body: { "username": "...", "reason": "..." }
    """
    from datetime import datetime
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    username = str(data.get("username", "")).strip()
    reason = str(data.get("reason", "Forgot password - requesting reset to default.")).strip()

    if not username:
        return jsonify({"error": "Username is required to request a password reset."}), 400

    user = User.query.filter_by(username=username).first()
    if not user:
        # Don't leak details if user doesn't exist, but give clear response
        return jsonify({"error": f"Account '{username}' was not found in BulSU COE Timetable system."}), 404

    # Check for existing PENDING request
    existing_pending = PasswordResetRequest.query.filter_by(user_id=user.id, status="PENDING").first()
    if existing_pending:
        return jsonify({
            "status": "already_submitted",
            "message": f"A password reset request for '{username}' is already pending IT approval.",
            "request_id": existing_pending.id,
        }), 200

    req = PasswordResetRequest(
        user_id=user.id,
        username=user.username,
        role=user.role,
        program=user.program,
        reason=reason,
        status="PENDING",
        created_at=datetime.utcnow(),
    )
    db.session.add(req)

    audit = AuditLog(
        user_id=user.id,
        action="PASSWORD_RESET_REQUESTED",
        details=f"User '{user.username}' submitted a password reset request to IT Admin: {reason}",
    )
    db.session.add(audit)
    db.session.commit()

    return jsonify({
        "status": "success",
        "message": f"Password reset request for '{user.username}' successfully forwarded to IT Admin. Your password will be restored to default once approved.",
        "request_id": req.id,
    }), 201


@auth_bp.route("/reset-requests", methods=["GET"])
@login_required
def list_reset_requests():
    """
    GET /api/auth/reset-requests
    List password reset requests. Accessible to IT, ADMIN, DEAN, and CHAIR.
    """
    status_filter = request.args.get("status", "PENDING").upper()
    query = PasswordResetRequest.query
    if status_filter != "ALL":
        query = query.filter_by(status=status_filter)
    requests_list = query.order_by(PasswordResetRequest.created_at.desc()).all()
    return jsonify({
        "status": "success",
        "data": [r.to_dict() for r in requests_list],
    }), 200


@auth_bp.route("/reset-requests/<int:request_id>/approve", methods=["POST"])
@login_required
def approve_reset_request(request_id):
    """
    POST /api/auth/reset-requests/<request_id>/approve
    Approves the reset request, resetting the user's password to default ('nexus2026').
    RESTRICTED: Strictly reserved for IT Team accounts ('IT', 'IT_TEAM').
    """
    from datetime import datetime
    effective_role = get_effective_role()
    if effective_role not in ("IT", "IT_TEAM"):
        return jsonify({
            "error": "Forbidden: Password reset approval is exclusively restricted to the IT Team account."
        }), 403

    req = db.session.get(PasswordResetRequest, request_id)
    if not req:
        return jsonify({"error": "Reset request not found."}), 404

    user = db.session.get(User, req.user_id)
    if not user:
        return jsonify({"error": "Target user no longer exists."}), 404

    default_pwd = "password" if user.role == "ADMIN" else "nexus2026"
    user.set_password(default_pwd)
    user.demo_password = default_pwd
    user.must_change_credentials = True

    # Sync password reset to data-final.sql
    update_sql_dump_password(user.username, default_pwd)

    req.status = "APPROVED"
    req.resolved_at = datetime.utcnow()
    req.admin_notes = f"Password reset to default '{default_pwd}' by IT Team ({session.get('username', 'it_team')})."

    audit = AuditLog(
        user_id=session.get("user_id"),
        action="PASSWORD_RESET_APPROVED",
        details=f"IT Team reset password for user '{user.username}' (ID: {user.id}) to default '{default_pwd}' per request #{req.id}.",
    )
    db.session.add(audit)
    db.session.commit()

    return jsonify({
        "status": "success",
        "message": f"Password for user '{user.username}' successfully reset to default ('{default_pwd}') by IT Team.",
        "default_password": default_pwd,
        "request": req.to_dict(),
    }), 200


@auth_bp.route("/reset-requests/<int:request_id>/reject", methods=["POST"])
@login_required
def reject_reset_request(request_id):
    """
    POST /api/auth/reset-requests/<request_id>/reject
    Rejects the password reset request.
    RESTRICTED: Strictly reserved for IT Team accounts ('IT', 'IT_TEAM').
    """
    from datetime import datetime
    effective_role = get_effective_role()
    if effective_role not in ("IT", "IT_TEAM"):
        return jsonify({
            "error": "Forbidden: Password reset rejection is exclusively restricted to the IT Team account."
        }), 403

    req = db.session.get(PasswordResetRequest, request_id)
    if not req:
        return jsonify({"error": "Reset request not found."}), 404

    data = request.get_json(silent=True) or {}
    notes = data.get("notes", "Rejected by IT Team.")

    req.status = "REJECTED"
    req.resolved_at = datetime.utcnow()
    req.admin_notes = notes

    audit = AuditLog(
        user_id=session.get("user_id"),
        action="PASSWORD_RESET_REJECTED",
        details=f"IT Team rejected reset request #{req.id} for user '{req.username}': {notes}",
    )
    db.session.add(audit)
    db.session.commit()

    return jsonify({
        "status": "success",
        "message": f"Password reset request #{req.id} rejected by IT Team.",
        "request": req.to_dict(),
    }), 200


@auth_bp.route("/users", methods=["POST"])
@login_required
def create_new_user():
    """
    POST /api/auth/users
    Create a new user account.
    RESTRICTED: Exclusively for IT Team accounts ('IT', 'IT_TEAM').
    Body: {
        "username": "...",
        "password": "...", (optional, defaults to 'nexus2026')
        "role": "...",     (DEAN, ADMIN, CHAIR, FACULTY, STUDENT, IT)
        "program": "...",  (optional, e.g. MEE, CE, CPE)
        "reference_id": ... (optional, faculty_id or section_id)
    }
    """
    effective_role = get_effective_role()
    if effective_role not in ("IT", "IT_TEAM"):
        return jsonify({
            "error": "Forbidden: Creating new user accounts is exclusively restricted to the IT Team."
        }), 403

    data = request.get_json(silent=True) or request.form.to_dict() or {}
    username = str(data.get("username", "")).strip()
    raw_password = str(data.get("password", "")).strip() or "nexus2026"
    role = str(data.get("role", "FACULTY")).strip().upper()
    program = (data.get("program") or "").strip() or None
    reference_id = data.get("reference_id")

    if not username:
        return jsonify({"error": "Username is required."}), 400

    if len(username) < 3:
        return jsonify({"error": "Username must be at least 3 characters long."}), 400

    valid_roles = {"DEAN", "ADMIN", "IT", "IT_TEAM", "CHAIR", "PROG_CHA", "FACULTY", "FCLTY_DEPT", "STUDENT", "STDN_MYR"}
    if role not in valid_roles:
        return jsonify({"error": f"Invalid role '{role}'. Valid roles: DEAN, ADMIN, IT, CHAIR, FACULTY, STUDENT."}), 400

    # Ensure username is unique
    existing = User.query.filter_by(username=username).first()
    if existing:
        return jsonify({"error": f"Username '{username}' already exists."}), 409

    try:
        if reference_id is not None and str(reference_id).strip() != "":
            reference_id = int(reference_id)
        else:
            reference_id = None
    except (ValueError, TypeError):
        reference_id = None

    new_user = User(
        username=username,
        role=role,
        program=program,
        reference_id=reference_id,
        demo_password=raw_password,
        must_change_credentials=True,
        is_active_status=True,
    )
    new_user.set_password(raw_password)

    db.session.add(new_user)
    db.session.flush()

    audit = AuditLog(
        user_id=session.get("user_id"),
        action="USER_CREATED",
        details=f"IT Team ({session.get('username')}) created new account '{username}' with role '{role}' and program '{program}'.",
    )
    db.session.add(audit)
    db.session.commit()

    # Sync newly created user to data-final.sql
    append_sql_dump_user(
        user_id=new_user.id,
        username=username,
        password=raw_password,
        role=role,
        dept=program,
        ref_id=reference_id,
    )

    return jsonify({
        "status": "success",
        "message": f"User account '{username}' created successfully by IT Team with initial password '{raw_password}'.",
        "user": new_user.to_dict(),
    }), 201


@auth_bp.route("/users", methods=["GET"])
@login_required
def list_all_users_for_it():
    """
    GET /api/auth/users
    List all user accounts in the system with their status, role, program, and creation date.
    RESTRICTED: Accessible to IT Team ('IT', 'IT_TEAM') and Administrator accounts.
    """
    effective_role = get_effective_role()
    if effective_role not in ("IT", "IT_TEAM", "ADMIN"):
        return jsonify({"error": "Forbidden: User directory is restricted to IT Support and Admin."}), 403

    search = request.args.get("search", "").strip().lower()
    role_filter = request.args.get("role", "").strip().upper()
    status_filter = request.args.get("status", "").strip().lower()

    query = User.query
    if role_filter:
        query = query.filter_by(role=role_filter)

    users = query.order_by(User.id.asc()).all()
    results = []
    for u in users:
        d = u.to_dict()
        if search:
            if (search not in (u.username or "").lower() and
                search not in (u.role or "").lower() and
                search not in (u.program or "").lower()):
                continue
        if status_filter == "active" and not u.is_active_status:
            continue
        if status_filter == "inactive" and u.is_active_status:
            continue
        results.append(d)

    return jsonify({
        "status": "success",
        "count": len(results),
        "data": results,
    }), 200


@auth_bp.route("/users/<int:target_user_id>", methods=["DELETE"])
@login_required
def delete_user_by_it(target_user_id):
    """
    DELETE /api/auth/users/<target_user_id>
    Allows IT Support Team to delete inactive or obsolete user accounts.
    Also cleans up data-final.sql so deleted accounts are removed from seed data.
    RESTRICTED: Exclusively for IT Team accounts ('IT', 'IT_TEAM').
    """
    effective_role = get_effective_role()
    if effective_role not in ("IT", "IT_TEAM"):
        return jsonify({"error": "Forbidden: Deleting users is exclusively restricted to the IT Team."}), 403

    current_uid = session.get("user_id")
    if current_uid == target_user_id:
        return jsonify({"error": "Cannot delete your own active IT Team account."}), 400

    target_user = db.session.get(User, target_user_id)
    if not target_user:
        return jsonify({"error": "User not found."}), 404

    # Protect essential institutional admin/it account
    if target_user.username in ("it_team", "admin"):
        return jsonify({"error": f"Cannot delete essential system account '{target_user.username}'."}), 400

    deleted_username = target_user.username
    deleted_role = target_user.role

    # Clean up associated reset requests first
    PasswordResetRequest.query.filter_by(user_id=target_user.id).delete()

    db.session.delete(target_user)
    db.session.flush()

    audit = AuditLog(
        user_id=current_uid,
        action="USER_DELETED",
        details=f"IT Team ({session.get('username')}) deleted account '{deleted_username}' (ID #{target_user_id}, Role: {deleted_role}).",
    )
    db.session.add(audit)
    db.session.commit()

    # Remove user from data-final.sql dump
    remove_sql_dump_user(deleted_username)

    return jsonify({
        "status": "success",
        "message": f"User account '{deleted_username}' deleted successfully by IT Team and removed from data-final.sql.",
    }), 200




@auth_bp.route("/impersonate", methods=["POST"])
def impersonate():
    """
    POST /api/auth/impersonate
    Pitch bar endpoint allowing rapid role switching between CHAIR, FACULTY, and STUDENT.
    Restricted to authenticated Program Chair and Admin users.
    """
    # Enforce role restriction: genuine Students and Faculty cannot impersonate
    auth_role = normalize_role(session.get("role"))
    if auth_role not in ["CHAIR", "ADMIN", "DEAN"]:
        return (
            jsonify(
                {
                    "error": "Access denied: Role impersonation is strictly reserved for Program Chair and Administrator accounts."
                }
            ),
            403,
        )

    data = request.get_json(silent=True) or {}
    raw_role = str(data.get("role", "")).strip().upper()
    target_program = data.get("program")
    reference_id = data.get("reference_id")

    target_role = normalize_role(raw_role)
    allowed_roles = {"DEAN", "ADMIN", "CHAIR", "PROG_CHA", "FACULTY", "FCLTY_DEPT", "STUDENT", "STDN_MYR"}
    if target_role not in allowed_roles and raw_role not in allowed_roles:
        return (
            jsonify(
                {
                    "error": f"Invalid role: '{raw_role}'. Must be one of {sorted(list(allowed_roles))}."
                }
            ),
            400,
        )

    # Scoping assignments for role impersonation
    if target_role in ("DEAN", "ADMIN"):
        target_program = None
        reference_id = None
    elif target_role == "CHAIR":
        target_program = (target_program or "MEE").strip().upper()
        reference_id = None
    elif target_role == "FACULTY":
        if reference_id is None:
            tullao = Faculty.query.filter(Faculty.name.ilike("%TULLAO%")).first()
            if tullao:
                reference_id = tullao.id
                target_program = tullao.program
            else:
                fac = Faculty.query.first()
                reference_id = fac.id if fac else None
                target_program = fac.program if fac else "MEE"
        else:
            reference_id = int(reference_id)
            fac_rec = db.session.get(Faculty, reference_id)
            if fac_rec:
                target_program = fac_rec.program
    elif target_role == "STUDENT":
        if reference_id is None:
            mee1a = Section.query.filter(Section.name.ilike("%MEE%1A%")).first()
            if mee1a:
                reference_id = mee1a.id
                target_program = "MEE"
            else:
                sec = Section.query.first()
                reference_id = sec.id if sec else None
                target_program = sec.program if sec else "MEE"
        else:
            reference_id = int(reference_id)
            sec_rec = db.session.get(Section, reference_id)
            if sec_rec:
                target_program = sec_rec.program

    # Ensure a valid session user exists when switching role in live presentation
    if "user_id" not in session:
        default_user = User.query.filter_by(role=target_role).first()
        if not default_user:
            default_user = User.query.first()
        if default_user:
            session["user_id"] = default_user.id
            session["username"] = default_user.username
            session["role"] = default_user.role
            session["must_change_credentials"] = default_user.must_change_credentials

    session["active_role"] = target_role
    session["active_reference_id"] = reference_id
    session["active_program"] = target_program

    return (
        jsonify(
            {
                "status": "success",
                "active_role": target_role,
                "active_reference_id": reference_id,
                "active_program": target_program,
                "message": f"Session role successfully set to {target_role} ({target_program or 'ALL'}).",
            }
        ),
        200,
    )


@auth_bp.route("/me", methods=["GET"])
def get_current_user():
    """GET /api/auth/me - Retrieve current session context."""
    user_id = session.get("user_id")
    user = db.session.get(User, user_id) if user_id else None

    return (
        jsonify(
            {
                "status": "success",
                "logged_in": bool(user_id),
                "user_id": user_id,
                "username": session.get("username"),
                "native_role": session.get("role"),
                "native_reference_id": session.get("reference_id"),
                "active_role": get_effective_role(),
                "active_reference_id": get_effective_reference_id(),
                "user_role": get_effective_role(),
                "user_program": get_effective_program(),
                "active_program": get_effective_program(),
                "program": session.get("program"),
                "must_change_credentials": bool(user.must_change_credentials if user else session.get("must_change_credentials")),
                "user": user.to_dict() if user else None,
            }
        ),
        200,
    )
