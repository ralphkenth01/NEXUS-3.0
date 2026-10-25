import sys
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app import create_app
from models import db, Subject, Faculty, User, Section, Room, Schedule, FacultySubject
from services.conflict_detector import validate_schedule_slot, parse_time, times_overlap


class TestSubjectClassificationAndAssignment(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app("testing")
        cls.client = cls.app.test_client()

    def setUp(self):
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()

        # Seed test subjects
        s1 = Subject(code="COE 100", title="Intro to Engineering", program="GEN", units=3, subject_type="LECTURE", hours_per_week=3)
        s2 = Subject(code="ETH 101", title="Ethics", program="GEN", units=3, subject_type="LECTURE", hours_per_week=3)
        s3 = Subject(code="MEE 101", title="Thermodynamics 1", program="MEE", units=3, subject_type="LECTURE", hours_per_week=3)
        s4 = Subject(code="COE 201", title="Engineering Mechanics", program="GEN", units=3, subject_type="LECTURE", hours_per_week=3)
        s5 = Subject(code="MEE 300", title="Machine Design 1", program="MEE", units=3, subject_type="LECTURE", hours_per_week=3)
        s6 = Subject(code="MEE 400", title="Plant Engineering", program="MEE", units=3, subject_type="LECTURE", hours_per_week=3)
        s7 = Subject(code="PE 10", title="Physical Education 1", program="GEN", units=2, subject_type="LECTURE", hours_per_week=2, year_level=1)
        db.session.add_all([s1, s2, s3, s4, s5, s6, s7])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_subject_computed_year_and_classification(self):
        """Test computed year level and Major vs Minor classification rules."""
        s_coe100 = Subject.query.filter_by(code="COE 100").first()
        s_eth101 = Subject.query.filter_by(code="ETH 101").first()
        s_mee101 = Subject.query.filter_by(code="MEE 101").first()
        s_coe201 = Subject.query.filter_by(code="COE 201").first()
        s_mee300 = Subject.query.filter_by(code="MEE 300").first()
        s_mee400 = Subject.query.filter_by(code="MEE 400").first()
        s_pe10 = Subject.query.filter_by(code="PE 10").first()

        # Year levels
        self.assertEqual(s_coe100.computed_year_level, 1)
        self.assertEqual(s_eth101.computed_year_level, 1)
        self.assertEqual(s_mee101.computed_year_level, 1)
        self.assertEqual(s_coe201.computed_year_level, 2)
        self.assertEqual(s_mee300.computed_year_level, 3)
        self.assertEqual(s_mee400.computed_year_level, 4)
        self.assertEqual(s_pe10.computed_year_level, 1)

        # Classifications:
        # 1st/2nd year COE -> MAJOR
        self.assertEqual(s_coe100.classification, "MAJOR")
        self.assertEqual(s_coe201.classification, "MAJOR")

        # 1st/2nd year GEN non-COE -> MINOR
        self.assertEqual(s_eth101.classification, "MINOR")
        self.assertEqual(s_pe10.classification, "MINOR")

        # 1st/2nd year Department -> MAJOR
        self.assertEqual(s_mee101.classification, "MAJOR")

        # 3rd & 4th year -> MAJOR
        self.assertEqual(s_mee300.classification, "MAJOR")
        self.assertEqual(s_mee400.classification, "MAJOR")

    def test_api_subjects_classification_filtering(self):
        """Test filtering by classification (MAJOR vs MINOR) on /api/subjects."""
        res_all = self.client.get("/api/subjects")
        self.assertEqual(res_all.status_code, 200)
        self.assertEqual(res_all.get_json()["count"], 7)

        # Major subjects: COE 100, MEE 101, COE 201, MEE 300, MEE 400 = 5
        res_majors = self.client.get("/api/subjects?classification=MAJOR")
        self.assertEqual(res_majors.status_code, 200)
        majors_data = res_majors.get_json()
        self.assertEqual(majors_data["count"], 5)
        major_codes = {s["code"] for s in majors_data["data"]}
        self.assertEqual(major_codes, {"COE 100", "MEE 101", "COE 201", "MEE 300", "MEE 400"})

        # Minor subjects: ETH 101, PE 10 = 2
        res_minors = self.client.get("/api/subjects?classification=MINOR")
        self.assertEqual(res_minors.status_code, 200)
        minors_data = res_minors.get_json()
        self.assertEqual(minors_data["count"], 2)
        minor_codes = {s["code"] for s in minors_data["data"]}
        self.assertEqual(minor_codes, {"ETH 101", "PE 10"})

    def test_minor_subjects_cannot_be_assigned_to_faculty(self):
        """Test that minor subjects are excluded from assignable options and rejected on assignment."""
        fac = Faculty(name="Engr. John Smith", email="jsmith@bulsu.edu.ph", program="MEE", max_units=21)
        sec = Section(name="MEE-1A", program="MEE", year_level=1)
        rm = Room(room_code="COE-201", room_type="LECTURE", capacity=40, is_available=True)
        admin_user = User(username="admin_test", role="ADMIN")
        admin_user.set_password("Admin@123")

        db.session.add_all([fac, sec, rm, admin_user])
        db.session.commit()

        with self.client.session_transaction() as sess:
            sess["user_id"] = admin_user.id
            sess["username"] = admin_user.username
            sess["role"] = "ADMIN"
            sess["active_role"] = "ADMIN"

        # 1. Verify assignable options excludes Minor subjects
        res_opt = self.client.get(f"/api/faculty/{fac.id}/assignable-options")
        self.assertEqual(res_opt.status_code, 200)
        opt_data = res_opt.get_json()["data"]
        assignable_subjects = opt_data["subjects"]
        for s in assignable_subjects:
            self.assertEqual(s["classification"], "MAJOR")
        assignable_codes = {s["code"] for s in assignable_subjects}
        self.assertNotIn("ETH 101", assignable_codes)
        self.assertNotIn("PE 10", assignable_codes)
        self.assertIn("MEE 101", assignable_codes)

        # 2. Attempt assigning a Minor subject (ETH 101) -> Must fail with 400
        s_eth = Subject.query.filter_by(code="ETH 101").first()
        res_assign_minor = self.client.post(
            f"/api/faculty/{fac.id}/assign-subject",
            json={
                "subject_id": s_eth.id,
                "section_id": sec.id,
                "room_id": rm.id,
                "day_of_week": "MON",
                "start_time": "08:00",
                "end_time": "11:00",
            },
        )
        self.assertEqual(res_assign_minor.status_code, 400)
        self.assertIn("Minor subject", res_assign_minor.get_json()["error"])

        # 3. Assigning a Major subject (MEE 101) -> Must succeed
        s_mee = Subject.query.filter_by(code="MEE 101").first()
        res_assign_major = self.client.post(
            f"/api/faculty/{fac.id}/assign-subject",
            json={
                "subject_id": s_mee.id,
                "section_id": sec.id,
                "room_id": rm.id,
                "day_of_week": "MON",
                "start_time": "08:00",
                "end_time": "11:00",
            },
        )
        self.assertEqual(res_assign_major.status_code, 201)
        self.assertEqual(res_assign_major.get_json()["status"], "success")

    def test_multi_subject_assignment_with_split_and_unassigned_room(self):
        """Test assigning multiple subjects as teaching qualifications without automatic timetable slots."""
        fac = Faculty(name="Engr. Elena Reyes", email="ereyes@bulsu.edu.ph", program="MEE", max_units=21)
        sec = Section(name="MEE-2A", program="MEE", year_level=2)
        s_coe = Subject.query.filter_by(code="COE 201").first()
        s_mee3 = Subject.query.filter_by(code="MEE 300").first()
        admin_user = User(username="admin_multi", role="ADMIN")
        admin_user.set_password("Admin@123")

        db.session.add_all([fac, sec, admin_user])
        db.session.commit()

        with self.client.session_transaction() as sess:
            sess["user_id"] = admin_user.id
            sess["role"] = "ADMIN"
            sess["active_role"] = "ADMIN"

        # Multi-subject payload assigns subjects faculty can handle
        res = self.client.post(
            f"/api/faculty/{fac.id}/assign-subject",
            json={
                "subjects": [
                    {"subject_id": s_coe.id, "split_preference": "1.5_HOUR_SPLIT"},
                    {"subject_id": s_mee3.id, "split_preference": "3_HOUR_BLOCK"},
                ]
            },
        )
        self.assertEqual(res.status_code, 201)
        body = res.get_json()
        self.assertEqual(body["status"], "success")
        self.assertEqual(len(body["data"]["assigned_subjects"]), 2)

        # Verify FacultySubject records created and faculty units updated
        fs_records = FacultySubject.query.filter_by(faculty_id=fac.id).all()
        self.assertEqual(len(fs_records), 2)
        assigned_sub_ids = {fs.subject_id for fs in fs_records}
        self.assertEqual(assigned_sub_ids, {s_coe.id, s_mee3.id})

        # Verify that assigning subjects does NOT automatically create a date schedule/slot by default
        scheds = Schedule.query.filter_by(faculty_id=fac.id).all()
        self.assertEqual(len(scheds), 0)

    def test_auto_update_tba_schedules_when_faculty_assigned(self):
        """Test that existing unassigned/TBA schedule slots automatically update with professor once assigned."""
        fac = Faculty(name="Engr. Auto Assigned", email="auto@bulsu.edu.ph", program="ECE", max_units=21)
        sec = Section(name="ECE-2A", program="ECE", year_level=2)
        rm = Room(room_code="RM-201", room_type="LECTURE", capacity=40, is_available=True)
        sub = Subject(code="ECE 201", title="Electronic Circuits", program="ECE", units=3, subject_type="LECTURE", hours_per_week=3)
        admin = User(username="admin_auto", role="ADMIN")
        admin.set_password("Admin@123")
        db.session.add_all([fac, sec, rm, sub, admin])
        db.session.commit()

        # Create an existing schedule that was generated with NO faculty (TBA)
        tba_sched = Schedule(
            subject_id=sub.id,
            section_id=sec.id,
            faculty_id=None,
            room_id=rm.id,
            day_of_week="MON",
            start_time=parse_time("08:00"),
            end_time=parse_time("11:00"),
            status="DRAFT"
        )
        db.session.add(tba_sched)
        db.session.commit()
        self.assertIsNone(tba_sched.faculty_id)

        with self.client.session_transaction() as sess:
            sess["user_id"] = admin.id
            sess["role"] = "ADMIN"
            sess["active_role"] = "ADMIN"

        # Now assign this subject to the faculty member
        res = self.client.post(
            f"/api/faculty/{fac.id}/assign-subject",
            json={"subject_id": sub.id}
        )
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["data"]["updated_schedules_count"], 1)

        # Refresh schedule from DB and verify faculty_id was updated automatically
        db.session.refresh(tba_sched)
        self.assertEqual(tba_sched.faculty_id, fac.id)
        self.assertEqual(tba_sched.to_dict()["faculty_name"], fac.name)
        self.assertFalse(tba_sched.to_dict()["has_no_faculty"])

    def test_lab_subgroup_g1_g2_dual_schedules_and_concurrency(self):
        """Test creating G1 and G2 laboratory schedule slots and section concurrency."""
        fac1 = Faculty(name="Engr. Lab Instructor 1", email="lab1@bulsu.edu.ph", program="MEE", max_units=21)
        fac2 = Faculty(name="Engr. Lab Instructor 2", email="lab2@bulsu.edu.ph", program="MEE", max_units=21)
        sec = Section(name="MEE-3A", program="MEE", year_level=3)
        rm1 = Room(room_code="LAB-101", room_type="LAB", capacity=30, is_available=True)
        rm2 = Room(room_code="LAB-102", room_type="LAB", capacity=30, is_available=True)
        sub_lab = Subject(code="MEE 301L", title="Instrumentation Lab", program="MEE", units=2, subject_type="LAB", hours_per_week=3)
        admin_user = User(username="admin_lab", role="ADMIN")
        admin_user.set_password("Admin@123")

        db.session.add_all([fac1, fac2, sec, rm1, rm2, sub_lab, admin_user])
        db.session.commit()

        with self.client.session_transaction() as sess:
            sess["user_id"] = admin_user.id
            sess["role"] = "ADMIN"
            sess["active_role"] = "ADMIN"

        # 1. Create dual schedule (split into G1 and G2) via POST /api/schedules
        res_dual = self.client.post(
            "/api/schedules",
            json={
                "subject_id": sub_lab.id,
                "section_id": sec.id,
                "faculty_id": fac1.id,
                "room_id": rm1.id,
                "day_of_week": "TUE",
                "start_time": "08:00",
                "end_time": "11:00",
                "split_lab_groups": True,
            },
        )
        self.assertEqual(res_dual.status_code, 201)
        dual_data = res_dual.get_json()
        self.assertEqual(len(dual_data["schedules"]), 2)
        subgroups = {s["sub_group"] for s in dual_data["schedules"]}
        self.assertEqual(subgroups, {"G1", "G2"})

        # 2. Section concurrent check: G1 and G2 can run concurrently in different rooms without conflict
        g2_candidate = {
            "subject_id": sub_lab.id,
            "section_id": sec.id,
            "faculty_id": fac2.id,
            "room_id": rm2.id,
            "day_of_week": "TUE",
            "start_time": "08:00",
            "end_time": "11:00",
            "sub_group": "G2",
        }
        s_g2 = Schedule.query.filter_by(section_id=sec.id, sub_group="G2").first()
        check_result = validate_schedule_slot(g2_candidate, exclude_schedule_id=s_g2.id)
        self.assertTrue(check_result["valid"], f"Expected valid, got conflicts: {check_result.get('conflicts')}")

    def test_curriculum_year_level_schedules_all_sections_with_same_faculty(self):
        """
        Verify that subjects for a curriculum year level are scheduled for ALL sections
        of that year level, and the assigned faculty teaches all sections across non-overlapping times.
        """
        from services.scheduler import run_smart_scheduler

        fac = Faculty(name="Engr. Robert Lee", email="rlee@bulsu.edu.ph", program="ECE", max_units=24)
        sec_a = Section(name="ECE-1A", program="ECE", year_level=1)
        sec_b = Section(name="ECE-1B", program="ECE", year_level=1)
        sub = Subject(
            code="ECE 111",
            title="Circuits I",
            units=3,
            hours_per_week=3,
            subject_type="LECTURE",
            program="ECE",
            year_level=1,
            semester=1,
            split_preference="3_HOUR_BLOCK",
        )
        rm1 = Room(room_code="COE-ECE-1", room_type="LECTURE", department="ECE", capacity=45, is_available=True)
        rm2 = Room(room_code="COE-ECE-2", room_type="LECTURE", department="ECE", capacity=45, is_available=True)

        db.session.add_all([fac, sec_a, sec_b, sub, rm1, rm2])
        db.session.commit()

        # Assign faculty to teach ECE 111
        fs = FacultySubject(faculty_id=fac.id, subject_id=sub.id)
        db.session.add(fs)
        db.session.commit()

        # Run smart scheduler for ECE 1st year
        res = run_smart_scheduler(
            {
                "program": "ECE",
                "year_level": "1",
                "section_ids": [sec_a.id, sec_b.id],
                "room_ids": [rm1.id, rm2.id],
                "faculty_ids": [fac.id],
                "semester": 1,
            }
        )

        self.assertEqual(res["status"], "success")

        # Verify both sections received ECE 111
        sched_a = Schedule.query.filter_by(section_id=sec_a.id, subject_id=sub.id).all()
        sched_b = Schedule.query.filter_by(section_id=sec_b.id, subject_id=sub.id).all()
        self.assertTrue(len(sched_a) > 0, "Section ECE-1A should have ECE 111 scheduled")
        self.assertTrue(len(sched_b) > 0, "Section ECE-1B should have ECE 111 scheduled")

        # Verify the same faculty teaches both sections
        for s in sched_a:
            self.assertEqual(s.faculty_id, fac.id)
        for s in sched_b:
            self.assertEqual(s.faculty_id, fac.id)

        # Verify that their time slots do not overlap for the faculty
        for sa in sched_a:
            for sb in sched_b:
                if sa.day_of_week == sb.day_of_week:
                    self.assertFalse(
                        times_overlap(sa.start_time, sa.end_time, sb.start_time, sb.end_time),
                        "Faculty cannot teach both sections at the same overlapping time",
                    )

    def test_manual_schedule_edit_allows_faculty_and_details_change(self):
        """
        Verify that manual edits to a timetable slot (PUT /api/schedules/<id>)
        allow changing the faculty, section, or setting faculty to TBA.
        """
        admin_user = User(username="chair_manual_test", role="CHAIR")
        admin_user.set_password("Admin@123")
        fac1 = Faculty(name="Prof. One", email="pone@bulsu.edu.ph", program="MEE", max_units=21)
        fac2 = Faculty(name="Prof. Two", email="ptwo@bulsu.edu.ph", program="MEE", max_units=21)
        sec = Section(name="MEE-2A", program="MEE", year_level=2)
        sub = Subject(
            code="MEE 222",
            title="Thermodynamics",
            units=3,
            hours_per_week=3,
            subject_type="LECTURE",
            program="MEE",
            year_level=2,
            semester=1,
        )
        rm = Room(room_code="COE-205", room_type="LECTURE", department="MEE", capacity=40, is_available=True)
        db.session.add_all([admin_user, fac1, fac2, sec, sub, rm])
        db.session.commit()

        sched = Schedule(
            subject_id=sub.id,
            section_id=sec.id,
            faculty_id=fac1.id,
            room_id=rm.id,
            day_of_week="MON",
            start_time=parse_time("08:00"),
            end_time=parse_time("09:30"),
            status="DRAFT",
        )
        db.session.add(sched)
        db.session.commit()

        with self.client.session_transaction() as sess:
            sess["user_id"] = admin_user.id
            sess["username"] = admin_user.username
            sess["role"] = "CHAIR"
            sess["active_role"] = "CHAIR"

        # 1. Update schedule faculty to Prof. Two
        res_put = self.client.put(
            f"/api/schedules/{sched.id}",
            json={
                "faculty_id": fac2.id,
                "room_id": rm.id,
                "day_of_week": "MON",
                "start_time": "08:00",
                "end_time": "09:30",
            },
        )
        self.assertEqual(res_put.status_code, 200)
        updated = db.session.get(Schedule, sched.id)
        self.assertEqual(updated.faculty_id, fac2.id)

        # 2. Update schedule to TBA (faculty_id = None)
        res_put_tba = self.client.put(
            f"/api/schedules/{sched.id}",
            json={
                "faculty_id": None,
                "room_id": rm.id,
                "day_of_week": "MON",
                "start_time": "08:00",
                "end_time": "09:30",
            },
        )
        self.assertEqual(res_put_tba.status_code, 200)
        updated_tba = db.session.get(Schedule, sched.id)
        self.assertIsNone(updated_tba.faculty_id)

    def test_assign_faculty_afterwards_registers_all_sections_and_resolves_conflicts(self):
        """
        Verify that when multiple sections have generated TBA schedules for a subject
        (even at conflicting times), assigning faculty to that subject afterwards
        automatically assigns the faculty to ALL sections and resolves timing collisions.
        """
        admin_user = User(username="chair_assign_all_sections", role="CHAIR")
        admin_user.set_password("Admin@123")
        fac = Faculty(name="Engr. Elena Vance", email="evance@bulsu.edu.ph", program="CPE", max_units=24)
        sec1 = Section(name="CPE-2A", program="CPE", year_level=2)
        sec2 = Section(name="CPE-2B", program="CPE", year_level=2)
        sub = Subject(
            code="CPE 225",
            title="Signals and Systems",
            units=3,
            hours_per_week=3,
            subject_type="LECTURE",
            program="CPE",
            year_level=2,
            semester=1,
            split_preference="3_HOUR_BLOCK",
        )
        rm1 = Room(room_code="COE-CPE-201", room_type="LECTURE", department="CPE", capacity=45, is_available=True)
        rm2 = Room(room_code="COE-CPE-202", room_type="LECTURE", department="CPE", capacity=45, is_available=True)

        db.session.add_all([admin_user, fac, sec1, sec2, sub, rm1, rm2])
        db.session.commit()

        # Both sections have TBA schedules generated initially at the SAME time slot (e.g. MON 08:00 - 11:00)
        sched1 = Schedule(
            subject_id=sub.id,
            section_id=sec1.id,
            faculty_id=None,  # TBA
            room_id=rm1.id,
            day_of_week="MON",
            start_time=parse_time("08:00"),
            end_time=parse_time("11:00"),
            status="PUBLISHED",
        )
        sched2 = Schedule(
            subject_id=sub.id,
            section_id=sec2.id,
            faculty_id=None,  # TBA
            room_id=rm2.id,
            day_of_week="MON",
            start_time=parse_time("08:00"),
            end_time=parse_time("11:00"),
            status="PUBLISHED",
        )
        db.session.add_all([sched1, sched2])
        db.session.commit()

        with self.client.session_transaction() as sess:
            sess["user_id"] = admin_user.id
            sess["username"] = admin_user.username
            sess["role"] = "CHAIR"
            sess["active_role"] = "CHAIR"
            sess["active_program"] = "CPE"

        # Now assign faculty afterwards via /api/faculty/<id>/assign-subject
        res = self.client.post(
            f"/api/faculty/{fac.id}/assign-subject",
            json={"subjects": [{"subject_id": sub.id, "split_preference": "3_HOUR_BLOCK"}]},
        )
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        self.assertEqual(data["data"]["updated_schedules_count"], 2, "Both sections must be automatically registered")

        # Verify in DB that BOTH sections are registered to the faculty
        s1 = db.session.get(Schedule, sched1.id)
        s2 = db.session.get(Schedule, sched2.id)
        self.assertEqual(s1.faculty_id, fac.id, "Section 1 must be registered to faculty")
        self.assertEqual(s2.faculty_id, fac.id, "Section 2 must be registered to faculty")

        # Verify that their schedules no longer conflict for the faculty
        if s1.day_of_week == s2.day_of_week:
            self.assertFalse(
                times_overlap(s1.start_time, s1.end_time, s2.start_time, s2.end_time),
                "Faculty schedules between sections must not collide",
            )

    def test_default_generation_is_3_hours_straight(self):
        """
        Verify that default schedule generation places subjects as 3 hours straight.
        """
        from services.scheduler import run_smart_scheduler

        fac = Faculty(name="Engr. Alan Turing", email="aturing@bulsu.edu.ph", program="CPE", max_units=24)
        sec = Section(name="CPE-3A", program="CPE", year_level=3)
        sub = Subject(
            code="CPE 315",
            title="Embedded Systems Architecture",
            units=3,
            hours_per_week=3,
            subject_type="LECTURE",
            program="CPE",
            year_level=3,
            semester=1,
        )
        rm = Room(room_code="COE-CPE-301", room_type="LECTURE", department="CPE", capacity=45, is_available=True)

        db.session.add_all([fac, sec, sub, rm])
        db.session.commit()

        fs = FacultySubject(faculty_id=fac.id, subject_id=sub.id)
        db.session.add(fs)
        db.session.commit()

        # Run scheduler without explicit split config (should default to 3 hours straight)
        res = run_smart_scheduler(
            {
                "program": "CPE",
                "year_level": "3",
                "section_ids": [sec.id],
                "room_ids": [rm.id],
                "faculty_ids": [fac.id],
                "semester": 1,
            }
        )
        self.assertEqual(res["status"], "success")

        scheds = Schedule.query.filter_by(section_id=sec.id, subject_id=sub.id).all()
        # Default should produce 1 straight 3-hour block session
        self.assertEqual(len(scheds), 1, "Default generation should be a single 3-hour straight session")
        s = scheds[0]
        st_m = s.start_time.hour * 60 + s.start_time.minute
        et_m = s.end_time.hour * 60 + s.end_time.minute
        self.assertEqual(et_m - st_m, 180, "Default session duration must be 180 minutes (3 hours straight)")


if __name__ == "__main__":
    unittest.main()

