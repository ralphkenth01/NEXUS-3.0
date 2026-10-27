"""COE SYNC 2026 - Master Database Seeding & Migration Engine
Ingests data-2.sql as the Single Source of Truth for BulSU College of Engineering:
- 126 real BulSU COE faculty across 8 engineering departments + General Engineering
- 90 standardized Sections across 8 departments (CE - 1A to MEE - 4B)
- 267 normalized Subjects across all engineering curricula with LECTURE/LAB types
- Realistic COE 1 and COE 2 Rooms (Lecture halls and specialized labs)
- User accounts (admin, chair_cpe, chair, faculty, student)
- 1 Faculty blackout availability window (FRI 08:00-12:00)
- 0 pre-generated dummy schedules by default (clean slate for Smart Scheduler)
"""

import sys
from datetime import time
from pathlib import Path
from werkzeug.security import generate_password_hash

# Ensure project root is in sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from models import (
    db,
    User,
    Faculty,
    Section,
    Subject,
    Room,
    FacultyAvailability,
    Schedule,
    AuditLog,
    RescheduleRequest,
    Conflict,
    InterDepartmentRequest,
)
from import_sql_dump import load_and_clean_sql_dump


def seed_database(app=None, stage_demo_schedules: bool = False):
    """
    Idempotent seeding function.
    Drops all existing tables, recreates schema, imports cleaned data-2.sql data,
    and initializes realistic BulSU COE master fixtures.
    """
    from app import create_app

    if app is None:
        app = create_app()

    with app.app_context():
        print("[COE SYNC 2026] Resetting and wiping database schema...")
        db.drop_all()
        db.create_all()
        print("[COE SYNC 2026] Schema recreated successfully.")

        # Load cleaned data from data-final.sql (Single Source of Truth)
        print("[COE SYNC 2026] Loading and cleaning data-final.sql...")
        dump_data = load_and_clean_sql_dump()

        # -------------------------------------------------------------
        # 1. Seed Faculty (126 real BulSU COE Faculty)
        # -------------------------------------------------------------
        print(f"[COE SYNC 2026] Seeding {len(dump_data['faculty'])} real BulSU COE Faculty members...")
        faculty_members = []
        for f in dump_data["faculty"]:
            faculty_members.append(
                Faculty(
                    id=f["id"],
                    name=f["name"],
                    email=f["email"],
                    program=f["program"],
                    max_units=f.get("max_units", 21),
                    current_units=0,
                    preferred_session_type=f.get("preferred_session_type", "STRAIGHT"),
                )
            )
        db.session.add_all(faculty_members)
        db.session.flush()

        # -------------------------------------------------------------
        # 2. Seed Sections (90 Sections from CE - 1A to MEE - 4B)
        # -------------------------------------------------------------
        print(f"[COE SYNC 2026] Seeding {len(dump_data['sections'])} Section cohorts...")
        sections = []
        for s in dump_data["sections"]:
            sections.append(
                Section(
                    id=s["id"],
                    name=s["name"],
                    program=s["program"],
                    year_level=s["year_level"],
                    student_count=s.get("student_count", 40),
                    class_mayor=s.get("class_mayor"),
                )
            )
        db.session.add_all(sections)
        db.session.flush()

        # -------------------------------------------------------------
        # 3. Seed Subjects (267 Curricular Subjects from data-2.sql)
        # -------------------------------------------------------------
        print(f"[COE SYNC 2026] Seeding {len(dump_data['subjects'])} Subjects catalog...")
        subjects = []
        for s in dump_data["subjects"]:
            subjects.append(
                Subject(
                    id=s["id"],
                    code=s["code"],
                    title=s["title"],
                    program=s["program"],
                    units=s["units"],
                    subject_type=s["subject_type"],
                    hours_per_week=s["hours_per_week"],
                    year_level=s["year_level"],
                    semester=s["semester"],
                    split_preference=s.get("split_preference", "3_HOUR_BLOCK"),
                    is_general=s.get("is_general", False),
                    requires_grouping=s.get("requires_grouping", False),
                    preferred_session_type=s.get("preferred_session_type", "STRAIGHT"),
                )
            )
        db.session.add_all(subjects)
        db.session.flush()

        # -------------------------------------------------------------
        # 4. Seed Real BulSU COE Rooms from data-final.sql
        # -------------------------------------------------------------
        raw_dump_rooms = dump_data.get("rooms", [])
        if raw_dump_rooms:
            print(f"[COE SYNC 2026] Seeding {len(raw_dump_rooms)} authoritative Rooms from data-final.sql...")
            rooms = [
                Room(
                    id=r["id"],
                    room_code=r["room_code"],
                    room_type=r["room_type"],
                    capacity=r.get("capacity", 45),
                    building=r.get("building", "Natividad Hall"),
                    equipment=r.get("equipment", "Standard Classroom Amenities"),
                    department=r.get("department", "GEN"),
                    is_available=True,
                )
                for r in raw_dump_rooms
            ]
        else:
            print("[COE SYNC 2026] Seeding fallback BulSU COE Rooms...")
            rooms = []
            for room_num in range(101, 108):
                rooms.append(
                    Room(
                        room_code=f"COE {room_num}",
                        room_type="LECTURE",
                        capacity=45,
                        building="COE 1 Building",
                        equipment="Multimedia Projector, Whiteboard",
                        is_available=True,
                        department="GEN",
                    )
                )

        db.session.add_all(rooms)
        db.session.flush()

        # -------------------------------------------------------------
        # 5. Seed Authoritative Ground-Truth Users & Aliases
        # -------------------------------------------------------------
        print("[COE SYNC 2026] Seeding User accounts with role-based reference IDs...")
        first_faculty = faculty_members[0]
        cpe_faculty = next((f for f in faculty_members if f.program == "CPE"), first_faculty)
        cpe_section = next((s for s in sections if "CPE" in s.name and "2" in s.name), sections[0])

        tullao_fac = next((f for f in faculty_members if "TULLAO" in f.name.upper()), first_faculty)
        bugayong_fac = next((f for f in faculty_members if "BUGAYONG" in f.name.upper()), first_faculty)
        roque_fac = next((f for f in faculty_members if "ROQUE" in f.name.upper()), first_faculty)

        mee_section = next((s for s in sections if "MEE" in s.name and "1A" in s.name.replace(" ", "")), sections[0])
        ce_section = next((s for s in sections if "CE" in s.name and "1A" in s.name.replace(" ", "")), sections[0])

        default_pwd_hash = generate_password_hash("password")
        seeded_usernames = set()
        user_objects = []

        # 5a. Seed 129 Authoritative Users from data-final.sql
        if "users" in dump_data:
            print(f"[COE SYNC 2026] Ingesting {len(dump_data['users'])} authoritative users from SQL dump...")
            for u in dump_data["users"]:
                uname = u["username"]
                seeded_usernames.add(uname)
                user_pwd = u.get("password") or "password"
                user_objects.append(
                    User(
                        id=u["id"],
                        username=uname,
                        password_hash=generate_password_hash(user_pwd),
                        role=u["role"],
                        program=u.get("program"),
                        reference_id=u.get("reference_id"),
                        must_change_credentials=False,
                        is_active_status=True,
                        demo_password=user_pwd,
                    )
                )

        # 5b. Seed IT Support Team Account (The only system account outside data-final.sql)
        alias_accounts = [
            ("it_team", "IT", "COE-IT", None),
        ]

        for uname, urole, uprog, uref in alias_accounts:
            if uname not in seeded_usernames:
                seeded_usernames.add(uname)
                user_objects.append(
                    User(
                        username=uname,
                        password_hash=default_pwd_hash,
                        role=urole,
                        program=uprog,
                        reference_id=uref,
                        must_change_credentials=False,
                        is_active_status=True,
                        demo_password="password",
                    )
                )

        db.session.add_all(user_objects)
        db.session.flush()

        # -------------------------------------------------------------
        # 6. Seed Faculty Availability (1 Blackout Window on Friday)
        # -------------------------------------------------------------
        print("[COE SYNC 2026] Seeding Faculty Availability Constraint...")
        unavailability = FacultyAvailability(
            faculty_id=cpe_faculty.id,
            day_of_week="FRI",
            start_time=time(8, 0, 0),
            end_time=time(12, 0, 0),
            is_preferred=False,
            split_preference="1.5_HOUR_SPLIT",
        )
        db.session.add(unavailability)
        db.session.flush()

        # -------------------------------------------------------------
        # 7. Schedules & Reschedule Requests: 0 by default
        # -------------------------------------------------------------
        schedules_data = []
        reschedule_requests = []

        if stage_demo_schedules:
            # Stage minimal schedules if explicitly requested
            subj_map = {s.code: s for s in subjects}
            room_map = {r.room_code: r for r in rooms}
            sec_target = cpe_section

            sample_subj = next((s for s in subjects if s.program in ("CPE", "GEN") and s.subject_type == "LECTURE"), subjects[0])
            sample_room = rooms[0]

            demo_sched = Schedule(
                subject_id=sample_subj.id,
                section_id=sec_target.id,
                faculty_id=cpe_faculty.id,
                room_id=sample_room.id,
                day_of_week="MON",
                start_time=time(8, 0),
                end_time=time(9, 30),
                status="PUBLISHED",
            )
            schedules_data.append(demo_sched)
            db.session.add_all(schedules_data)
            db.session.flush()

        # -------------------------------------------------------------
        # 8. Seed Audit Log Entry
        # -------------------------------------------------------------
        author_user = next((u for u in user_objects if u.username == "it_team"), user_objects[0])
        audit_entry = AuditLog(
            user_id=author_user.id,
            action="DATABASE_SEEDED",
            details=(
                f"data-final.sql ingestion complete: {len(faculty_members)} faculty, "
                f"{len(sections)} sections, {len(subjects)} subjects, {len(rooms)} rooms, "
                f"{len(user_objects)} users."
            ),
        )
        db.session.add(audit_entry)

        db.session.commit()
        print(f"[COE SYNC 2026] Database successfully seeded with {len(faculty_members)} faculty, {len(sections)} sections, {len(subjects)} subjects, {len(rooms)} rooms, and {len(user_objects)} users.")

        return {
            "users": len(user_objects),
            "faculty": len(faculty_members),
            "sections": len(sections),
            "rooms": len(rooms),
            "subjects": len(subjects),
            "availabilities": 1,
            "schedules": len(schedules_data),
            "reschedule_requests": len(reschedule_requests),
        }


if __name__ == "__main__":
    seed_database()
