"""COE SYNC 2026 - Master Data Management CRUD Routes

Endpoints for Rooms, Faculty, Sections, and Subjects:
- POST   /api/rooms, PUT /api/rooms/<id>, DELETE /api/rooms/<id>
- POST   /api/faculty, PUT /api/faculty/<id>, DELETE /api/faculty/<id>
- POST   /api/sections, PUT /api/sections/<id>, DELETE /api/sections/<id>
- POST   /api/subjects, PUT /api/subjects/<id>, DELETE /api/subjects/<id>
"""

# pyrefly: ignore[missing-import]
from flask import Blueprint, request, jsonify, session
from datetime import time
from models import (
    db,
    User,
    Room,
    Faculty,
    Section,
    Subject,
    AuditLog,
    FacultyAvailability,
    Schedule,
    FacultySubject,
    Curriculum,
    CurriculumSubject,
)
from routes.auth import (
    require_roles,
    get_effective_role,
    get_effective_program,
    generate_faculty_username,
    append_sql_dump_faculty,
    append_sql_dump_user,
    remove_sql_dump_faculty,
    remove_sql_dump_user,
    append_sql_dump_section,
    remove_sql_dump_section,
    update_sql_dump_section_name,
    update_sql_dump_password,
)

master_data_bp = Blueprint("master_data", __name__, url_prefix="/api")


# -----------------------------------------------------------------
# ROOMS CRUD
# -----------------------------------------------------------------
@master_data_bp.route("/rooms", methods=["POST"])
@require_roles("CHAIR", "ADMIN")
def create_room():
    """Create a new Room."""
    data = request.get_json(silent=True) or {}
    room_code = str(data.get("room_code", "")).strip()
    room_type = str(data.get("room_type", "LECTURE")).strip().upper()
    capacity = data.get("capacity")
    is_available = bool(data.get("is_available", True))

    if not room_code:
        return jsonify({"error": "Room code is required."}), 400
    if room_type not in {"LECTURE", "LAB"}:
        return jsonify({"error": "Room type must be LECTURE or LAB."}), 400
    cap_val = 40
    if capacity is not None and str(capacity).strip() != "":
        try:
            parsed_c = int(capacity)
            if parsed_c > 0:
                cap_val = parsed_c
        except (ValueError, TypeError):
            cap_val = 40

    # Check uniqueness
    if Room.query.filter_by(room_code=room_code).first():
        return jsonify({"error": f"Room code '{room_code}' already exists."}), 409

    department = str(data.get("department", "GEN")).strip().upper() or "GEN"

    room = Room(
        room_code=room_code,
        room_type=room_type,
        capacity=cap_val,
        is_available=is_available,
        department=department,
    )
    db.session.add(room)
    db.session.flush()

    audit = AuditLog(
        user_id=session.get("user_id"),
        action="ROOM_CREATED",
        details=f"Created room {room.room_code} ({room.room_type}, capacity: {room.capacity}, dept: {room.department}).",
    )
    db.session.add(audit)
    db.session.commit()

    return jsonify({"status": "success", "message": "Room created successfully.", "data": room.to_dict()}), 201


@master_data_bp.route("/rooms/<int:room_id>", methods=["PUT"])
@require_roles("CHAIR", "ADMIN")
def update_room(room_id: int):
    """Update an existing Room."""
    room = db.session.get(Room, room_id)
    if not room:
        return jsonify({"error": f"Room #{room_id} not found."}), 404

    data = request.get_json(silent=True) or {}
    if "room_code" in data:
        code = str(data["room_code"]).strip()
        if not code:
            return jsonify({"error": "Room code cannot be empty."}), 400
        existing = Room.query.filter_by(room_code=code).first()
        if existing and existing.id != room.id:
            return jsonify({"error": f"Room code '{code}' is already taken."}), 409
        room.room_code = code

    if "room_type" in data:
        rtype = str(data["room_type"]).strip().upper()
        if rtype not in {"LECTURE", "LAB"}:
            return jsonify({"error": "Room type must be LECTURE or LAB."}), 400
        room.room_type = rtype

    if "capacity" in data:
        try:
            cap = int(data["capacity"])
            if cap <= 0:
                raise ValueError()
            room.capacity = cap
        except (ValueError, TypeError):
            return jsonify({"error": "Capacity must be greater than 0."}), 400

    if "is_available" in data:
        room.is_available = bool(data["is_available"])

    if "department" in data:
        dept = str(data["department"]).strip().upper()
        if dept:
            room.department = dept

    if "booked_by_label" in data:
        room.booked_by_label = data["booked_by_label"]

    audit = AuditLog(
        user_id=session.get("user_id"),
        action="ROOM_UPDATED",
        details=f"Updated room #{room.id} ({room.room_code}).",
    )
    db.session.add(audit)
    db.session.commit()

    return jsonify({"status": "success", "message": "Room updated successfully.", "data": room.to_dict()}), 200


@master_data_bp.route("/rooms/<int:room_id>", methods=["DELETE"])
@require_roles("CHAIR", "ADMIN")
def delete_room(room_id: int):
    """Delete a Room."""
    room = db.session.get(Room, room_id)
    if not room:
        return jsonify({"error": f"Room #{room_id} not found."}), 404

    code = room.room_code
    db.session.delete(room)

    audit = AuditLog(
        user_id=session.get("user_id"),
        action="ROOM_DELETED",
        details=f"Deleted room {code} (#{room_id}).",
    )
    db.session.add(audit)
    db.session.commit()

    return jsonify({"status": "success", "message": f"Room '{code}' deleted successfully."}), 200


# -----------------------------------------------------------------
# FACULTY CRUD
# -----------------------------------------------------------------
@master_data_bp.route("/faculty", methods=["POST"])
@require_roles("CHAIR", "ADMIN")
def create_faculty():
    """Create a new Faculty member."""
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()
    email = str(data.get("email", "")).strip().lower()
    max_units = data.get("max_units", 21)

    if not name or not email:
        return jsonify({"error": "Name and email are required."}), 400
    try:
        max_units = int(max_units)
        if max_units <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        return jsonify({"error": "Max units must be greater than 0."}), 400

    if Faculty.query.filter_by(email=email).first():
        return jsonify({"error": f"Email '{email}' is already registered."}), 409

    program = str(data.get("program") or session.get("active_program") or "MEE").strip().upper()

    faculty = Faculty(
        name=name,
        email=email,
        program=program,
        max_units=max_units,
        current_units=0,
    )
    db.session.add(faculty)
    db.session.flush()

    # Department full name mapping for data-final.sql
    dept_mapping = {
        "CE": "Civil Engineering",
        "CPE": "Computer Engineering",
        "EE": "Electrical Engineering",
        "ECE": "Electronics Engineering",
        "IE": "Industrial Engineering",
        "MFGE": "Manufacturing Engineering",
        "ME": "Mechanical Engineering",
        "MEE": "Mechatronics Engineering",
        "GEN": "General Engineering",
    }
    dept_full = dept_mapping.get(faculty.program, "Mechatronics Engineering")

    # 1. Update data-final.sql `coe facculty` table
    append_sql_dump_faculty(faculty.id, faculty.name, dept_full)

    # 2. Generate User Account starting with FAC- First initial and Last name
    base_username = generate_faculty_username(faculty.name)
    user_uname = base_username
    counter = 1
    while User.query.filter_by(username=user_uname).first():
        counter += 1
        user_uname = f"{base_username}{counter}"

    # Default password for newly generated faculty user
    default_pwd = "password"
    user_account = User(
        username=user_uname,
        role="FACULTY",
        program=faculty.program,
        reference_id=faculty.id,
        is_active_status=True,
        must_change_credentials=True,
    )
    user_account.set_password(default_pwd)
    user_account.demo_password = default_pwd
    db.session.add(user_account)
    db.session.flush()

    # 3. Update data-final.sql `users` table
    append_sql_dump_user(
        user_id=user_account.id,
        username=user_account.username,
        password=default_pwd,
        role="FCLTY_DEPT",
        dept=dept_full,
        ref_id=faculty.id,
    )

    audit = AuditLog(
        user_id=session.get("user_id"),
        action="FACULTY_CREATED",
        details=f"Created faculty {faculty.name} ({faculty.email}, max units: {faculty.max_units}) and user account '{user_uname}'.",
    )
    db.session.add(audit)
    db.session.commit()

    faculty_dict = faculty.to_dict()
    faculty_dict["user_account"] = {
        "id": user_account.id,
        "username": user_account.username,
        "role": user_account.role,
    }

    return jsonify({
        "status": "success",
        "message": f"Faculty member '{faculty.name}' and user account '{user_uname}' created successfully.",
        "data": faculty_dict,
    }), 201


@master_data_bp.route("/faculty/<int:faculty_id>", methods=["GET"])
@master_data_bp.route("/master/faculty/<int:faculty_id>", methods=["GET"])
def get_faculty_detail(faculty_id: int):
    """Retrieve detailed profile of a faculty member including assigned subjects and availability."""
    faculty = db.session.get(Faculty, faculty_id)
    if not faculty:
        return jsonify({"error": f"Faculty #{faculty_id} not found."}), 404

    # Fetch assigned subjects from FacultySubject (subjects this faculty is qualified/assigned to teach)
    handled = FacultySubject.query.filter_by(faculty_id=faculty_id).all()
    subjects_dict = {}
    total_units = 0

    for fs in handled:
        if fs.subject:
            total_units += (fs.subject.units or 0)
            subjects_dict[fs.subject.id] = {
                "id": fs.subject.id,
                "code": fs.subject.code,
                "title": fs.subject.title,
                "units": fs.subject.units,
                "type": fs.subject.subject_type,
                "faculty_subject_id": fs.id,
                "sections": [],
                "schedule_id": None,
                "day_of_week": "",
                "start_time": "",
                "end_time": "",
                "room_code": "",
            }

    # Fetch assigned schedules & merge details
    assigned_schedules = (
        Schedule.query.filter_by(faculty_id=faculty_id)
        .order_by(Schedule.day_of_week.asc(), Schedule.start_time.asc())
        .all()
    )
    for s in assigned_schedules:
        if s.subject:
            if s.subject.id not in subjects_dict:
                total_units += (s.subject.units or 0)
                subjects_dict[s.subject.id] = {
                    "id": s.subject.id,
                    "code": s.subject.code,
                    "title": s.subject.title,
                    "units": s.subject.units,
                    "type": s.subject.subject_type,
                    "faculty_subject_id": None,
                    "sections": [s.section.name] if s.section else [],
                    "schedule_id": s.id,
                    "day_of_week": s.day_of_week,
                    "start_time": s.start_time.strftime("%H:%M") if s.start_time else "",
                    "end_time": s.end_time.strftime("%H:%M") if s.end_time else "",
                    "room_code": s.room.room_code if s.room else "",
                }
            else:
                if s.id and not subjects_dict[s.subject.id]["schedule_id"]:
                    subjects_dict[s.subject.id]["schedule_id"] = s.id
                    subjects_dict[s.subject.id]["day_of_week"] = s.day_of_week
                    subjects_dict[s.subject.id]["start_time"] = s.start_time.strftime("%H:%M") if s.start_time else ""
                    subjects_dict[s.subject.id]["end_time"] = s.end_time.strftime("%H:%M") if s.end_time else ""
                    subjects_dict[s.subject.id]["room_code"] = s.room.room_code if s.room else ""
                if s.section and s.section.name not in subjects_dict[s.subject.id]["sections"]:
                    subjects_dict[s.subject.id]["sections"].append(s.section.name)

    # Fetch availability records
    avails = FacultyAvailability.query.filter_by(faculty_id=faculty_id).all()

    return jsonify({
        "status": "success",
        "data": {
            **faculty.to_dict(),
            "current_units": total_units or faculty.current_units,
            "assigned_subjects": list(subjects_dict.values()),
            "schedules": [s.to_dict() for s in assigned_schedules],
            "availabilities": [a.to_dict() for a in avails],
        }
    }), 200


@master_data_bp.route("/faculty/<int:faculty_id>", methods=["PUT"])
@master_data_bp.route("/master/faculty/<int:faculty_id>", methods=["PUT"])
@require_roles("CHAIR", "ADMIN", "FACULTY")
def update_faculty(faculty_id: int):
    """Update an existing Faculty member with strict self-ownership validation for FACULTY role."""
    faculty = db.session.get(Faculty, faculty_id)
    if not faculty:
        return jsonify({"error": f"Faculty #{faculty_id} not found."}), 404

    effective_role = session.get("active_role") or session.get("role")
    effective_ref_id = session.get("active_reference_id") or session.get("reference_id")

    if effective_role == "FACULTY":
        if not effective_ref_id or int(effective_ref_id) != int(faculty_id):
            return jsonify({"error": "Unauthorized: You are only permitted to update your own faculty profile."}), 403

    data = request.get_json(silent=True) or {}
    if "name" in data:
        name = str(data["name"]).strip()
        if not name:
            return jsonify({"error": "Name cannot be empty."}), 400
        faculty.name = name

    if "email" in data:
        email = str(data["email"]).strip().lower()
        if not email:
            return jsonify({"error": "Email cannot be empty."}), 400
        existing = Faculty.query.filter_by(email=email).first()
        if existing and existing.id != faculty.id:
            return jsonify({"error": f"Email '{email}' is already taken."}), 409
        faculty.email = email

    if "max_units" in data:
        try:
            m = int(data["max_units"])
            if m <= 0:
                raise ValueError()
            faculty.max_units = m
        except (ValueError, TypeError):
            return jsonify({"error": "Max units must be greater than 0."}), 400

    if "preferred_session_type" in data:
        sess_pref = str(data["preferred_session_type"]).strip().upper()
        if sess_pref in {"SPLIT", "STRAIGHT"}:
            faculty.preferred_session_type = sess_pref

    avails_data = data.get("availabilities") if "availabilities" in data else data.get("availability")
    if avails_data is not None and isinstance(avails_data, list):
        # Update or set availability windows
        FacultyAvailability.query.filter_by(faculty_id=faculty_id).delete()
        for item in avails_data:
            day = (item.get("day_of_week") or "").strip().upper()
            if day in {"MON", "TUE", "WED", "THU", "FRI", "SAT"}:
                st_parts = [int(p) for p in str(item.get("start_time", "08:00")).split(":")[:2]]
                et_parts = [int(p) for p in str(item.get("end_time", "17:00")).split(":")[:2]]
                db.session.add(
                    FacultyAvailability(
                        faculty_id=faculty_id,
                        day_of_week=day,
                        start_time=time(st_parts[0], st_parts[1]),
                        end_time=time(et_parts[0], et_parts[1]),
                        is_preferred=bool(item.get("is_preferred", True)),
                    )
                )

    audit = AuditLog(
        user_id=session.get("user_id"),
        action="FACULTY_UPDATED",
        details=f"Updated faculty #{faculty.id} ({faculty.name}).",
    )
    db.session.add(audit)
    db.session.commit()

    return jsonify({"status": "success", "message": "Faculty updated successfully.", "data": faculty.to_dict()}), 200


@master_data_bp.route("/faculty/<int:faculty_id>", methods=["DELETE"])
@require_roles("CHAIR", "ADMIN")
def delete_faculty(faculty_id: int):
    """Delete a Faculty member."""
    faculty = db.session.get(Faculty, faculty_id)
    if not faculty:
        return jsonify({"error": f"Faculty #{faculty_id} not found."}), 404

    name = faculty.name
    
    # 1. Remove from data-final.sql coe facculty table
    remove_sql_dump_faculty(faculty.id, name)

    # 2. Look up and remove linked User account
    linked_user = User.query.filter_by(reference_id=faculty.id, role="FACULTY").first()
    if not linked_user:
        # Also check role FCLTY_DEPT
        linked_user = User.query.filter(User.reference_id == faculty.id, User.username.startswith("FAC-")).first()
    
    if linked_user:
        remove_sql_dump_user(linked_user.username)
        db.session.delete(linked_user)

    db.session.delete(faculty)

    audit = AuditLog(
        user_id=session.get("user_id"),
        action="FACULTY_DELETED",
        details=f"Deleted faculty {name} (#{faculty_id}) and linked user account.",
    )
    db.session.add(audit)
    db.session.commit()

    return jsonify({"status": "success", "message": f"Faculty '{name}' deleted successfully."}), 200


# -----------------------------------------------------------------
# SECTIONS CRUD
# -----------------------------------------------------------------
@master_data_bp.route("/sections", methods=["POST"])
@require_roles("CHAIR", "ADMIN")
def create_section():
    """Create a new Cohort Section with automated User credential generation mirroring Faculty creation."""
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()
    program = str(data.get("program", "BSCpE")).strip()
    year_level = data.get("year_level")
    student_count = data.get("student_count")

    if not name or not program:
        return jsonify({"error": "Section name and program are required."}), 400
    try:
        year_level = int(year_level)
        if year_level <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        return jsonify({"error": "Year level must be a positive integer."}), 400

    sc_val = None
    if student_count is not None and str(student_count).strip() != "":
        try:
            parsed_sc = int(student_count)
            if parsed_sc > 0:
                sc_val = parsed_sc
        except (ValueError, TypeError):
            pass

    class_mayor = str(data.get("class_mayor", "")).strip() or None

    # Check for duplicate section name
    if Section.query.filter_by(name=name).first():
        return jsonify({"error": f"Section '{name}' already exists."}), 409

    section = Section(
        name=name,
        program=program,
        year_level=year_level,
        student_count=sc_val,
        class_mayor=class_mayor,
    )
    db.session.add(section)
    db.session.flush()

    # Department full name mapping for data-final.sql
    dept_mapping = {
        "CE": "Civil Engineering",
        "CPE": "Computer Engineering",
        "EE": "Electrical Engineering",
        "ECE": "Electronics Engineering",
        "IE": "Industrial Engineering",
        "MFGE": "Manufacturing Engineering",
        "ME": "Mechanical Engineering",
        "MEE": "Mechatronics Engineering",
        "GEN": "General Engineering",
    }
    dept_full = dept_mapping.get(section.program, "Computer Engineering")

    # 1. Update data-final.sql `coe sections` table
    append_sql_dump_section(section.id, section.name, dept_full, section.year_level)

    # 2. Generate User Account for Mayor: credential username is always the section name
    user_uname = section.name
    existing_user = User.query.filter_by(username=user_uname).first()
    default_pwd = "password"

    if existing_user:
        # Re-link or update reference
        existing_user.reference_id = section.id
        existing_user.role = "STUDENT"
        existing_user.program = section.program
        user_account = existing_user
    else:
        user_account = User(
            username=user_uname,
            role="STUDENT",
            program=section.program,
            reference_id=section.id,
            is_active_status=True,
            must_change_credentials=True,
        )
        user_account.set_password(default_pwd)
        user_account.demo_password = default_pwd
        db.session.add(user_account)
        db.session.flush()

    # 3. Update data-final.sql `users` table
    append_sql_dump_user(
        user_id=user_account.id,
        username=user_account.username,
        password=default_pwd,
        role="STDN_MYR",
        dept=dept_full,
        ref_id=section.id,
    )

    audit = AuditLog(
        user_id=session.get("user_id"),
        action="SECTION_CREATED",
        details=f"Created section {section.name} ({section.program}, Year {section.year_level}, Mayor: {section.mayor_name}) and user account '{user_uname}'.",
    )
    db.session.add(audit)
    db.session.commit()

    sec_dict = section.to_dict()
    sec_dict["user_account"] = {
        "id": user_account.id,
        "username": user_account.username,
        "role": user_account.role,
    }

    return jsonify({
        "status": "success",
        "message": f"Section '{section.name}' and Class Mayor account '{user_uname}' created successfully.",
        "data": sec_dict,
    }), 201


@master_data_bp.route("/sections/<int:section_id>", methods=["PUT"])
@require_roles("CHAIR", "ADMIN")
def update_section(section_id: int):
    """Update an existing Cohort Section and synchronize Class Mayor name and username."""
    section = db.session.get(Section, section_id)
    if not section:
        return jsonify({"error": f"Section #{section_id} not found."}), 404

    data = request.get_json(silent=True) or {}
    old_sec_name = section.name
    name_changed = False

    if "name" in data:
        name = str(data["name"]).strip()
        if not name:
            return jsonify({"error": "Section name cannot be empty."}), 400
        existing = Section.query.filter_by(name=name).first()
        if existing and existing.id != section.id:
            return jsonify({"error": f"Section name '{name}' is already taken."}), 409
        if name != old_sec_name:
            name_changed = True
            section.name = name

    if "program" in data:
        p = str(data["program"]).strip()
        if not p:
            return jsonify({"error": "Program cannot be empty."}), 400
        section.program = p

    if "year_level" in data:
        try:
            yl = int(data["year_level"])
            if yl <= 0:
                raise ValueError()
            section.year_level = yl
        except (ValueError, TypeError):
            return jsonify({"error": "Year level must be a positive integer."}), 400

    if "class_mayor" in data:
        raw_cm = str(data["class_mayor"]).strip() if data["class_mayor"] else ""
        section.class_mayor = raw_cm if raw_cm else None

    if "student_count" in data:
        raw_sc = data["student_count"]
        if raw_sc is None or str(raw_sc).strip() == "":
            section.student_count = None
        else:
            try:
                sc = int(raw_sc)
                if sc > 0:
                    section.student_count = sc
            except (ValueError, TypeError):
                pass

    dept_mapping = {
        "CE": "Civil Engineering",
        "CPE": "Computer Engineering",
        "EE": "Electrical Engineering",
        "ECE": "Electronics Engineering",
        "IE": "Industrial Engineering",
        "MFGE": "Manufacturing Engineering",
        "ME": "Mechanical Engineering",
        "MEE": "Mechatronics Engineering",
        "GEN": "General Engineering",
    }
    dept_full = dept_mapping.get(section.program, "Computer Engineering")

    # Update section in data-final.sql if name changed
    if name_changed:
        update_sql_dump_section_name(section.id, section.name, dept_full, section.year_level)

    # Find associated mayor user account (role STUDENT / STDN_MYR, ref_id == section.id or username == old_sec_name)
    linked_user = User.query.filter(
        User.reference_id == section.id,
        User.role.in_(["STUDENT", "STDN_MYR"])
    ).first()
    if not linked_user:
        linked_user = User.query.filter_by(username=old_sec_name).first()

    if linked_user:
        # Username is always their section name
        if linked_user.username != section.name:
            prev_uname = linked_user.username
            linked_user.username = section.name
            linked_user.program = section.program
            linked_user.reference_id = section.id
            update_sql_dump_password(
                username=section.name,
                new_password=linked_user.demo_password or "password",
                old_username=prev_uname,
            )
    else:
        # Create user account for section if not yet existing
        default_pwd = "password"
        user_uname = section.name
        new_u = User(
            username=user_uname,
            role="STUDENT",
            program=section.program,
            reference_id=section.id,
            is_active_status=True,
            must_change_credentials=True,
        )
        new_u.set_password(default_pwd)
        new_u.demo_password = default_pwd
        db.session.add(new_u)
        db.session.flush()
        append_sql_dump_user(
            user_id=new_u.id,
            username=new_u.username,
            password=default_pwd,
            role="STDN_MYR",
            dept=dept_full,
            ref_id=section.id,
        )

    audit = AuditLog(
        user_id=session.get("user_id"),
        action="SECTION_UPDATED",
        details=f"Updated section #{section.id} ({section.name}, Mayor: {section.mayor_name}).",
    )
    db.session.add(audit)
    db.session.commit()

    return jsonify({"status": "success", "message": "Section updated successfully.", "data": section.to_dict()}), 200


@master_data_bp.route("/sections/<int:section_id>", methods=["DELETE"])
@require_roles("CHAIR", "ADMIN")
def delete_section(section_id: int):
    """Delete a Cohort Section and linked Class Mayor account."""
    section = db.session.get(Section, section_id)
    if not section:
        return jsonify({"error": f"Section #{section_id} not found."}), 404

    name = section.name
    
    # 1. Remove from data-final.sql `coe sections`
    remove_sql_dump_section(section.id, name)

    # 2. Look up and remove linked User account
    linked_user = User.query.filter(
        User.reference_id == section.id,
        User.role.in_(["STUDENT", "STDN_MYR"])
    ).first()
    if not linked_user:
        linked_user = User.query.filter_by(username=name).first()

    if linked_user:
        remove_sql_dump_user(linked_user.username)
        db.session.delete(linked_user)

    db.session.delete(section)

    audit = AuditLog(
        user_id=session.get("user_id"),
        action="SECTION_DELETED",
        details=f"Deleted section {name} (#{section_id}) and linked user account.",
    )
    db.session.add(audit)
    db.session.commit()

    return jsonify({"status": "success", "message": f"Section '{name}' deleted successfully."}), 200



# -----------------------------------------------------------------
# SUBJECTS CRUD
# -----------------------------------------------------------------
@master_data_bp.route("/subjects", methods=["POST"])
@require_roles("CHAIR", "ADMIN")
def create_subject():
    """Create a new Curricular Subject."""
    data = request.get_json(silent=True) or {}
    code = str(data.get("code", "")).strip().upper()
    title = str(data.get("title", "")).strip()
    units = data.get("units")
    subject_type = str(data.get("subject_type", "LECTURE")).strip().upper()
    hours_per_week = data.get("hours_per_week")

    if not code or not title:
        return jsonify({"error": "Course code and title are required."}), 400
    if subject_type not in {"LECTURE", "LAB"}:
        return jsonify({"error": "Subject type must be LECTURE or LAB."}), 400
    try:
        units = int(units)
        hours_per_week = int(hours_per_week)
        if units <= 0 or hours_per_week <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        return jsonify({"error": "Units and hours per week must be positive integers."}), 400

    if Subject.query.filter_by(code=code).first():
        return jsonify({"error": f"Subject code '{code}' already exists."}), 409

    split_pref = str(data.get("split_preference", "3_HOUR_BLOCK")).strip()
    if split_pref not in {"3_HOUR_BLOCK", "1.5_HOUR_SPLIT"}:
        split_pref = "3_HOUR_BLOCK"

    subject = Subject(
        code=code,
        title=title,
        units=units,
        subject_type=subject_type,
        hours_per_week=hours_per_week,
        split_preference=split_pref,
        preferred_session_type="SPLIT" if split_pref == "1.5_HOUR_SPLIT" else "STRAIGHT",
    )
    db.session.add(subject)
    db.session.flush()

    audit = AuditLog(
        user_id=session.get("user_id"),
        action="SUBJECT_CREATED",
        details=f"Created subject {subject.code} - {subject.title} ({subject.units}u, {subject.subject_type}).",
    )
    db.session.add(audit)
    db.session.commit()

    return jsonify({"status": "success", "message": "Subject created successfully.", "data": subject.to_dict()}), 201


@master_data_bp.route("/subjects/<int:subject_id>", methods=["PUT"])
@require_roles("CHAIR", "ADMIN")
def update_subject(subject_id: int):
    """Update an existing Curricular Subject."""
    subject = db.session.get(Subject, subject_id)
    if not subject:
        return jsonify({"error": f"Subject #{subject_id} not found."}), 404

    data = request.get_json(silent=True) or {}
    if "code" in data:
        code = str(data["code"]).strip().upper()
        if not code:
            return jsonify({"error": "Subject code cannot be empty."}), 400
        existing = Subject.query.filter_by(code=code).first()
        if existing and existing.id != subject.id:
            return jsonify({"error": f"Subject code '{code}' already exists."}), 409
        subject.code = code

    if "title" in data:
        t = str(data["title"]).strip()
        if not t:
            return jsonify({"error": "Subject title cannot be empty."}), 400
        subject.title = t

    if "units" in data:
        try:
            u = int(data["units"])
            if u <= 0:
                raise ValueError()
            subject.units = u
        except (ValueError, TypeError):
            return jsonify({"error": "Units must be a positive integer."}), 400

    if "subject_type" in data:
        st = str(data["subject_type"]).strip().upper()
        if st not in {"LECTURE", "LAB"}:
            return jsonify({"error": "Subject type must be LECTURE or LAB."}), 400
        subject.subject_type = st

    if "hours_per_week" in data:
        try:
            hpw = int(data["hours_per_week"])
            if hpw <= 0:
                raise ValueError()
            subject.hours_per_week = hpw
        except (ValueError, TypeError):
            return jsonify({"error": "Hours per week must be a positive integer."}), 400

    if "split_preference" in data:
        sp = str(data["split_preference"]).strip()
        if sp in {"3_HOUR_BLOCK", "1.5_HOUR_SPLIT"}:
            subject.split_preference = sp
            subject.preferred_session_type = "SPLIT" if sp == "1.5_HOUR_SPLIT" else "STRAIGHT"

    if "preferred_session_type" in data:
        pst = str(data["preferred_session_type"]).strip().upper()
        if pst in {"SPLIT", "STRAIGHT"}:
            subject.preferred_session_type = pst
            if "split_preference" not in data:
                subject.split_preference = "1.5_HOUR_SPLIT" if pst == "SPLIT" else "3_HOUR_BLOCK"

    audit = AuditLog(
        user_id=session.get("user_id"),
        action="SUBJECT_UPDATED",
        details=f"Updated subject #{subject.id} ({subject.code}).",
    )
    db.session.add(audit)
    db.session.commit()

    return jsonify({"status": "success", "message": "Subject updated successfully.", "data": subject.to_dict()}), 200


@master_data_bp.route("/subjects/<int:subject_id>", methods=["DELETE"])
@require_roles("CHAIR", "ADMIN")
def delete_subject(subject_id: int):
    """Delete a Curricular Subject."""
    subject = db.session.get(Subject, subject_id)
    if not subject:
        return jsonify({"error": f"Subject #{subject_id} not found."}), 404

    code = subject.code
    db.session.delete(subject)

    audit = AuditLog(
        user_id=session.get("user_id"),
        action="SUBJECT_DELETED",
        details=f"Deleted subject {code} (#{subject_id}).",
    )
    db.session.add(audit)
    db.session.commit()

    return jsonify({"status": "success", "message": f"Subject '{code}' deleted successfully."}), 200


# -----------------------------------------------------------------
# FACULTY AVAILABILITY CRUD (Workflow 2 support)
# -----------------------------------------------------------------
@master_data_bp.route("/faculty/<int:faculty_id>/availability", methods=["GET"])
def get_faculty_availability(faculty_id: int):
    """List availability windows for a given faculty member."""
    faculty = db.session.get(Faculty, faculty_id)
    if not faculty:
        return jsonify({"error": f"Faculty #{faculty_id} not found."}), 404

    avails = FacultyAvailability.query.filter_by(faculty_id=faculty_id).order_by(
        FacultyAvailability.day_of_week.asc(), FacultyAvailability.start_time.asc()
    ).all()

    return jsonify({
        "status": "success",
        "faculty_id": faculty_id,
        "faculty_name": faculty.name,
        "count": len(avails),
        "data": [a.to_dict() for a in avails],
    }), 200


@master_data_bp.route("/faculty/<int:faculty_id>/availability", methods=["POST"])
@require_roles("CHAIR", "ADMIN", "FACULTY")
def create_faculty_availability(faculty_id: int):
    """
    Add an availability or blackout window for a faculty member.
    Prototype behavior: allows faculty members or chairs to maintain availability windows.
    """
    faculty = db.session.get(Faculty, faculty_id)
    if not faculty:
        return jsonify({"error": f"Faculty #{faculty_id} not found."}), 404

    data = request.get_json(silent=True) or {}
    day_of_week = str(data.get("day_of_week", "")).strip().upper()
    start_time_str = str(data.get("start_time", "")).strip()
    end_time_str = str(data.get("end_time", "")).strip()
    is_preferred = bool(data.get("is_preferred", True))

    valid_days = {"MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"}
    if day_of_week not in valid_days:
        return jsonify({"error": f"Invalid day of week: {day_of_week}"}), 400

    try:
        sp = [int(p) for p in start_time_str.split(":")]
        ep = [int(p) for p in end_time_str.split(":")]
        st = time(sp[0], sp[1])
        et = time(ep[0], ep[1])
        if (st.hour * 60 + st.minute) >= (et.hour * 60 + et.minute):
            raise ValueError()
    except Exception:
        return jsonify({"error": "Invalid time window. Start time must be strictly earlier than end time (HH:MM)."}), 400

    avail = FacultyAvailability(
        faculty_id=faculty_id,
        day_of_week=day_of_week,
        start_time=st,
        end_time=et,
        is_preferred=is_preferred,
    )
    db.session.add(avail)
    db.session.flush()

    label = "preferred availability" if is_preferred else "blackout period"
    audit = AuditLog(
        user_id=session.get("user_id"),
        action="FACULTY_AVAILABILITY_SET",
        details=f"Added {label} for {faculty.name}: {day_of_week} {start_time_str}-{end_time_str}.",
    )
    db.session.add(audit)
    db.session.commit()

    return jsonify({
        "status": "success",
        "message": f"Availability window saved for {faculty.name}.",
        "data": avail.to_dict(),
    }), 201


@master_data_bp.route("/availabilities/<int:avail_id>", methods=["DELETE"])
@require_roles("CHAIR", "ADMIN", "FACULTY")
def delete_faculty_availability(avail_id: int):
    """Delete an availability window."""
    avail = db.session.get(FacultyAvailability, avail_id)
    if not avail:
        return jsonify({"error": f"Availability record #{avail_id} not found."}), 404

    faculty_name = avail.faculty.name if avail.faculty else f"Faculty #{avail.faculty_id}"
    db.session.delete(avail)

    audit = AuditLog(
        user_id=session.get("user_id"),
        action="FACULTY_AVAILABILITY_DELETED",
        details=f"Deleted availability window #{avail_id} for {faculty_name}.",
    )
    db.session.add(audit)
    db.session.commit()

    return jsonify({
        "status": "success",
        "message": "Availability window removed successfully.",
    }), 200


# -----------------------------------------------------------------
# FACULTY SUBJECT ASSIGNMENT (Program Chair & Admin)
# -----------------------------------------------------------------
@master_data_bp.route("/faculty/<int:faculty_id>/assignable-options", methods=["GET"])
@master_data_bp.route("/master/faculty/<int:faculty_id>/assignable-options", methods=["GET"])
@require_roles("CHAIR", "ADMIN")
def get_faculty_assignable_options(faculty_id: int):
    """Retrieve subjects, sections, and rooms tailored for assigning to a specific faculty member."""
    faculty = db.session.get(Faculty, faculty_id)
    if not faculty:
        return jsonify({"error": f"Faculty #{faculty_id} not found."}), 404

    effective_role = session.get("active_role") or session.get("role")
    effective_prog = session.get("active_program") or session.get("program")

    # Chair scoping check
    if effective_role in ("CHAIR", "PROG_CHA") and effective_prog:
        fac_prog = (faculty.program or "").strip().upper()
        eff_prog = effective_prog.strip().upper()
        if fac_prog != eff_prog and fac_prog != "GEN":
            return jsonify({
                "error": f"Unauthorized: As {effective_prog} Program Chair, you can only manage faculty in your department."
            }), 403

    # Subjects: faculty's program + GEN subjects (or all if ADMIN)
    sub_query = Subject.query
    if effective_role in ("CHAIR", "PROG_CHA") or request.args.get("scoped", "true").lower() == "true":
        fac_prog = (faculty.program or "").strip().upper()
        sub_query = sub_query.filter(Subject.program.in_([fac_prog, "GEN"]))
    subjects = sub_query.order_by(Subject.code.asc()).all()
    # Exclude minor subjects: only Major subjects can be assigned to faculty
    subjects = [s for s in subjects if s.classification == "MAJOR"]

    # Sections: faculty's program sections
    sec_query = Section.query
    if effective_role in ("CHAIR", "PROG_CHA") or request.args.get("scoped", "true").lower() == "true":
        fac_prog = (faculty.program or "").strip().upper()
        sec_query = sec_query.filter(Section.program == fac_prog)
    sections = sec_query.order_by(Section.year_level.asc(), Section.name.asc()).all()

    # Rooms: all available rooms
    rooms = Room.query.filter_by(is_available=True).order_by(Room.room_code.asc()).all()

    return jsonify({
        "status": "success",
        "data": {
            "faculty": faculty.to_dict(),
            "subjects": [s.to_dict() for s in subjects],
            "sections": [s.to_dict() for s in sections],
            "rooms": [r.to_dict() for r in rooms],
        }
    }), 200


@master_data_bp.route("/faculty/<int:faculty_id>/assign-subject", methods=["POST"])
@master_data_bp.route("/master/faculty/<int:faculty_id>/assign-subject", methods=["POST"])
@require_roles("CHAIR", "ADMIN")
def assign_subject_to_faculty(faculty_id: int):
    """Assign a curricular subject offering to a faculty member with conflict validation."""
    from services.conflict_detector import validate_schedule_slot, parse_time, find_alternatives

    faculty = db.session.get(Faculty, faculty_id)
    if not faculty:
        return jsonify({"error": f"Faculty #{faculty_id} not found."}), 404

    effective_role = session.get("active_role") or session.get("role")
    effective_prog = session.get("active_program") or session.get("program")

    # Chair scoping check
    if effective_role in ("CHAIR", "PROG_CHA") and effective_prog:
        fac_prog = (faculty.program or "").strip().upper()
        eff_prog = effective_prog.strip().upper()
        if fac_prog != eff_prog and fac_prog != "GEN":
            return jsonify({
                "error": f"Unauthorized: As {effective_prog} Program Chair, you can only assign subjects to faculty in your department."
            }), 403

    data = request.get_json(silent=True) or {}
    # Can receive single subject_id or list of subject_ids/subject objects
    raw_subjects = data.get("subjects") or []
    if not raw_subjects and data.get("subject_id"):
        raw_subjects = [{
            "subject_id": data.get("subject_id"),
            "split_preference": data.get("split_preference") or "3_HOUR_BLOCK"
        }]
    elif isinstance(raw_subjects, list) and raw_subjects and not isinstance(raw_subjects[0], dict):
        raw_subjects = [{"subject_id": sid, "split_preference": "3_HOUR_BLOCK"} for sid in raw_subjects]

    if not raw_subjects:
        return jsonify({"error": "At least one Subject Offering is required."}), 400

    assigned_records = []

    for item in raw_subjects:
        s_id = item.get("subject_id") if isinstance(item, dict) else item
        item_split_pref = (item.get("split_preference") if isinstance(item, dict) else None) or "3_HOUR_BLOCK"
        if not s_id:
            continue

        subject = db.session.get(Subject, int(s_id))
        if not subject:
            return jsonify({"error": f"Subject #{s_id} not found."}), 404

        if subject.classification == "MINOR":
            return jsonify({
                "error": f"Minor subject '{subject.code} - {subject.title}' cannot be assigned to faculty members. Only Major subjects may be assigned."
            }), 400

        # Program Chair can only add subjects belonging to their department (or GEN) that faculty will teach
        if effective_role in ("CHAIR", "PROG_CHA") and effective_prog:
            sub_prog = (subject.program or "").strip().upper()
            eff_prog = effective_prog.strip().upper()
            if sub_prog and sub_prog != "GEN" and sub_prog != eff_prog:
                return jsonify({
                    "error": f"Unauthorized: As {effective_prog} Program Chair, you can only assign subjects offered by your department."
                }), 403

        # Update subject's split preference if explicitly chosen
        if item_split_pref in ("1.5_HOUR_SPLIT", "3_HOUR_BLOCK"):
            subject.split_preference = item_split_pref

        # Assign subject to faculty pool / qualifications
        existing_fs = FacultySubject.query.filter_by(faculty_id=faculty.id, subject_id=subject.id).first()
        if not existing_fs:
            fs_record = FacultySubject(faculty_id=faculty.id, subject_id=subject.id)
            db.session.add(fs_record)
        assigned_records.append(subject)

    db.session.flush()

    # Automatically update any existing unassigned / TBA schedules for these assigned subjects
    assigned_subject_ids = [s.id for s in assigned_records]
    updated_schedule_count = 0
    skipped_conflicts = []

    if assigned_subject_ids:
        # Find all unassigned (faculty_id is NULL) schedules for these subjects
        tba_schedules = (
            Schedule.query.filter(
                Schedule.subject_id.in_(assigned_subject_ids),
                Schedule.faculty_id.is_(None),
            )
            .order_by(Schedule.id.asc())
            .all()
        )

        for sched in tba_schedules:
            # Check if assigning this faculty introduces a conflict (double booking or unavailability)
            test_payload = {
                "subject_id": sched.subject_id,
                "section_id": sched.section_id,
                "faculty_id": faculty.id,
                "room_id": sched.room_id,
                "sub_group": sched.sub_group,
                "day_of_week": sched.day_of_week,
                "start_time": sched.start_time.strftime("%H:%M:%S") if sched.start_time else "",
                "end_time": sched.end_time.strftime("%H:%M:%S") if sched.end_time else "",
            }
            val = validate_schedule_slot(test_payload, exclude_schedule_id=sched.id)
            if val.get("valid"):
                sched.faculty_id = faculty.id
                updated_schedule_count += 1
            else:
                # Slot has collision (e.g. faculty was already booked for another section at this same time slot)
                # Find an alternative conflict-free slot for this section, room, and faculty so all sections get the professor
                suggestions = find_alternatives(test_payload, exclude_schedule_id=sched.id)
                # Prioritize suggestions that maintain valid timing and assign the faculty
                slot_resolved = False
                for alt in suggestions:
                    alt_start = parse_time(alt.get("start_time"))
                    alt_end = parse_time(alt.get("end_time"))
                    alt_day = alt.get("day_of_week")
                    alt_room_id = alt.get("room_id") or sched.room_id
                    
                    alt_payload = {
                        "subject_id": sched.subject_id,
                        "section_id": sched.section_id,
                        "faculty_id": faculty.id,
                        "room_id": alt_room_id,
                        "sub_group": sched.sub_group,
                        "day_of_week": alt_day,
                        "start_time": alt_start.strftime("%H:%M:%S") if alt_start else "",
                        "end_time": alt_end.strftime("%H:%M:%S") if alt_end else "",
                    }
                    val_alt = validate_schedule_slot(alt_payload, exclude_schedule_id=sched.id)
                    if val_alt.get("valid"):
                        sched.faculty_id = faculty.id
                        sched.day_of_week = alt_day
                        sched.start_time = alt_start
                        sched.end_time = alt_end
                        sched.room_id = alt_room_id
                        updated_schedule_count += 1
                        slot_resolved = True
                        break

                if not slot_resolved:
                    # Fallback systematic slot search across standard days/slots (standard 3-hr or 1.5-hr window)
                    start_min = (sched.start_time.hour * 60 + sched.start_time.minute) if sched.start_time else 480
                    end_min = (sched.end_time.hour * 60 + sched.end_time.minute) if sched.end_time else 660
                    duration = max(90, end_min - start_min)

                    candidate_days = ["MON", "TUE", "WED", "THU", "FRI", "SAT"]
                    # Candidate start times (07:00 to 19:00 - duration)
                    for c_day in candidate_days:
                        if slot_resolved:
                            break
                        for c_start_m in range(7 * 60, 19 * 60 - duration + 1, 30):
                            c_start = time(c_start_m // 60, c_start_m % 60)
                            c_end_m = c_start_m + duration
                            c_end = time(c_end_m // 60, c_end_m % 60)

                            alt_payload = {
                                "subject_id": sched.subject_id,
                                "section_id": sched.section_id,
                                "faculty_id": faculty.id,
                                "room_id": sched.room_id,
                                "sub_group": sched.sub_group,
                                "day_of_week": c_day,
                                "start_time": c_start.strftime("%H:%M:%S"),
                                "end_time": c_end.strftime("%H:%M:%S"),
                            }
                            val_search = validate_schedule_slot(alt_payload, exclude_schedule_id=sched.id)
                            if val_search.get("valid"):
                                sched.faculty_id = faculty.id
                                sched.day_of_week = c_day
                                sched.start_time = c_start
                                sched.end_time = c_end
                                updated_schedule_count += 1
                                slot_resolved = True
                                break

                if not slot_resolved:
                    conflicts_summary = "; ".join(val.get("conflicts", []))
                    skipped_conflicts.append(f"Schedule #{sched.id} ({sched.subject.code if sched.subject else 'Subject'}): {conflicts_summary}")

    db.session.flush()

    # Recalculate faculty current_units based on handled subjects + any assigned active schedules
    all_fs = FacultySubject.query.filter_by(faculty_id=faculty.id).all()
    fs_units = sum((fs.subject.units or 0) for fs in all_fs if fs.subject)
    faculty.current_units = fs_units

    subj_codes = ", ".join([s.code for s in assigned_records if s])
    audit_detail = f"Assigned handled subjects {subj_codes} to {faculty.name}."
    if updated_schedule_count > 0:
        audit_detail += f" Automatically updated {updated_schedule_count} previously unassigned (TBA) schedule slot(s)."
    if skipped_conflicts:
        audit_detail += f" Skipped {len(skipped_conflicts)} slot(s) due to timing conflicts."

    audit = AuditLog(
        user_id=session.get("user_id"),
        action="FACULTY_SUBJECT_ASSIGNED",
        details=audit_detail,
    )
    db.session.add(audit)
    db.session.commit()

    msg = f"Successfully assigned {subj_codes} to {faculty.name}."
    if updated_schedule_count > 0:
        msg += f" Automatically updated {updated_schedule_count} schedule slot(s) previously marked as TBA (No Prof)."

    return jsonify({
        "status": "success",
        "message": msg,
        "data": {
            "assigned_subjects": [s.to_dict() for s in assigned_records],
            "faculty": faculty.to_dict(),
            "updated_schedules_count": updated_schedule_count,
        }
    }), 201


@master_data_bp.route("/faculty/<int:faculty_id>/unassign-subject", methods=["POST"])
@master_data_bp.route("/master/faculty/<int:faculty_id>/unassign-subject", methods=["POST"])
@require_roles("CHAIR", "ADMIN")
def unassign_subject_from_faculty(faculty_id: int):
    """Unassign / remove a subject offering from a faculty member's handled subjects and schedules."""
    faculty = db.session.get(Faculty, faculty_id)
    if not faculty:
        return jsonify({"error": f"Faculty #{faculty_id} not found."}), 404

    effective_role = session.get("active_role") or session.get("role")
    effective_prog = session.get("active_program") or session.get("program")

    # Chair scoping check
    if effective_role in ("CHAIR", "PROG_CHA") and effective_prog:
        fac_prog = (faculty.program or "").strip().upper()
        eff_prog = effective_prog.strip().upper()
        if fac_prog != eff_prog and fac_prog != "GEN":
            return jsonify({
                "error": f"Unauthorized: As {effective_prog} Program Chair, you can only manage faculty in your department."
            }), 403

    data = request.get_json(silent=True) or {}
    schedule_id = data.get("schedule_id")
    subject_id = data.get("subject_id")

    subj_code = "Subject"
    if subject_id:
        target_sub_id = int(subject_id)
        # Remove from FacultySubject
        fs_records = FacultySubject.query.filter_by(faculty_id=faculty_id, subject_id=target_sub_id).all()
        for fs in fs_records:
            if fs.subject:
                subj_code = fs.subject.code
            db.session.delete(fs)

        # Unassign any active schedules holding this faculty and subject (turn them into TBA)
        matching_scheds = Schedule.query.filter_by(faculty_id=faculty_id, subject_id=target_sub_id).all()
        for ms in matching_scheds:
            ms.faculty_id = None
    elif schedule_id:
        sched = db.session.get(Schedule, int(schedule_id))
        if not sched or sched.faculty_id != faculty.id:
            return jsonify({"error": "Schedule assignment not found for this faculty member."}), 404
        subj_code = sched.subject.code if sched.subject else "Subject"
        sched.faculty_id = None
    else:
        return jsonify({"error": "schedule_id or subject_id is required."}), 400

    db.session.flush()

    # Recalculate faculty current_units
    all_fs = FacultySubject.query.filter_by(faculty_id=faculty.id).all()
    faculty.current_units = sum((fs.subject.units or 0) for fs in all_fs if fs.subject)

    audit = AuditLog(
        user_id=session.get("user_id"),
        action="FACULTY_SUBJECT_UNASSIGNED",
        details=f"Unassigned {subj_code} from {faculty.name}.",
    )
    db.session.add(audit)
    db.session.commit()

    return jsonify({
        "status": "success",
        "message": f"Successfully unassigned {subj_code} from {faculty.name}.",
        "data": faculty.to_dict(),
    }), 200


# -----------------------------------------------------------------
# CURRICULA & CURRICULUM SUBJECTS (DEPARTMENT-LOCKED)
# -----------------------------------------------------------------
@master_data_bp.route("/curricula", methods=["GET"])
def list_curricula():
    """
    GET /api/curricula
    Retrieve curricula tracks.
    Locks curricula list based on department for Program Chairs.
    """
    from services.scheduler import normalize_prog_code
    from seed_curricula import ensure_curriculum_master_data

    # Ensure curricula table is populated
    if Curriculum.query.count() == 0:
        ensure_curriculum_master_data()

    effective_role = get_effective_role()
    effective_prog = get_effective_program()
    req_prog = request.args.get("program", "").strip().upper()

    query = Curriculum.query.filter_by(is_active=True)

    # Department locking for Program Chairs
    is_chair = effective_role in ("CHAIR", "PROG_CHA")
    locked_department = None
    if is_chair and effective_prog and effective_prog != "ALL":
        norm_chair_prog = normalize_prog_code(effective_prog)
        query = query.filter(Curriculum.program == norm_chair_prog)
        locked_department = norm_chair_prog
    elif req_prog and req_prog != "ALL":
        norm_req = normalize_prog_code(req_prog)
        query = query.filter(Curriculum.program == norm_req)

    curricula = query.order_by(Curriculum.program.asc(), Curriculum.revision_year.desc()).all()

    return jsonify({
        "status": "success",
        "count": len(curricula),
        "is_locked": bool(locked_department),
        "locked_department": locked_department,
        "data": [c.to_dict() for c in curricula],
    }), 200


@master_data_bp.route("/curricula/<int:curriculum_id>/subjects", methods=["GET"])
def get_curriculum_subjects(curriculum_id: int):
    """
    GET /api/curricula/<id>/subjects
    Retrieve subjects in a curriculum, organized by year level and semester.
    Enforces department access validation for Chairs.
    """
    from services.scheduler import normalize_prog_code

    curr = db.session.get(Curriculum, curriculum_id)
    if not curr:
        return jsonify({"status": "error", "message": "Curriculum not found."}), 404

    effective_role = get_effective_role()
    effective_prog = get_effective_program()
    if effective_role in ("CHAIR", "PROG_CHA") and effective_prog and effective_prog != "ALL":
        if normalize_prog_code(curr.program) != normalize_prog_code(effective_prog):
            return jsonify({
                "status": "error",
                "message": f"Unauthorized: You can only view curricula for your department ({effective_prog})."
            }), 403

    year_level = request.args.get("year_level", type=int)
    semester = request.args.get("semester", type=int)

    query = CurriculumSubject.query.filter_by(curriculum_id=curriculum_id)
    if year_level:
        query = query.filter_by(year_level=year_level)
    if semester:
        query = query.filter_by(semester=semester)

    entries = query.all()
    # Sort by year level, semester, subject code
    entries_sorted = sorted(entries, key=lambda e: (e.year_level, e.semester, (e.subject.code if e.subject else "")))

    return jsonify({
        "status": "success",
        "curriculum": curr.to_dict(),
        "count": len(entries_sorted),
        "data": [e.to_dict() for e in entries_sorted],
    }), 200



