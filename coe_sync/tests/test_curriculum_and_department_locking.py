"""COE SYNC 2026 - Curriculum Management & Department Locking Test Suite
Tests:
1. Master curricula data creation and subject mappings for all departments:
   - BS Computer Engineering (Revised 2024, CMO No. 87 s 2017)
   - BS Computer Engineering (Based on CMO No. 87 s 2017)
   - BS Electrical Engineering (Revised 2024, CMO No. 88 s 2017)
   - BS Electrical Engineering (Based on CMO No. 88 s 2017)
   - BS Industrial Engineering (Based on CMO No. 96 s 2017)
   - CE, ECE, ME, MEE, MFGE standard department tracks
2. Department-locked Curriculum API endpoints:
   - GET /api/curricula locks results to the authenticated Program Chair's department
   - GET /api/curricula allows Admins and Deans to browse across all engineering departments
   - GET /api/curricula/<id>/subjects forbids access to other departments' curricula for Chairs
3. Smart Schedule Generation with Curriculum Selection:
   - Filtering subjects according to chosen curriculum version
   - Rejecting schedule generation requests across mismatched department curricula for Chairs
"""

import sys
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app import create_app
from models import db, Curriculum, CurriculumSubject, Subject, Section, Room, Faculty, User
from seed_curricula import ensure_curriculum_master_data
from services.scheduler import run_smart_scheduler


class TestCurriculumAndDepartmentLocking(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app("testing")
        cls.client = cls.app.test_client()

    def setUp(self):
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()

        # Seed authoritative subjects catalog from dump
        from import_sql_dump import load_and_clean_sql_dump
        dump_data = load_and_clean_sql_dump()
        for s in dump_data["subjects"]:
            db.session.add(
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
        db.session.commit()
        ensure_curriculum_master_data()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_curricula_seeded_properly(self):
        """Verify all official curricula tracks exist with correct metadata and mappings."""
        curricula = {c.code: c for c in Curriculum.query.all()}
        self.assertIn("CPE-2024", curricula)
        self.assertIn("CPE-2017", curricula)
        self.assertIn("EE-2024", curricula)
        self.assertIn("EE-2017", curricula)
        self.assertIn("IE-2017", curricula)
        self.assertIn("CE-2018", curricula)
        self.assertIn("ME-2018", curricula)
        self.assertIn("ECE-2019", curricula)

        # Check that CPE-2024 has its subjects mapped
        cpe_2024 = curricula["CPE-2024"]
        self.assertEqual(cpe_2024.program, "CPE")
        self.assertEqual(cpe_2024.revision_year, 2024)
        self.assertGreater(len(cpe_2024.curriculum_subjects), 50)

        # Check EE-2024
        ee_2024 = curricula["EE-2024"]
        self.assertEqual(ee_2024.program, "EE")
        self.assertEqual(ee_2024.revision_year, 2024)
        self.assertGreater(len(ee_2024.curriculum_subjects), 50)

        # Check IE-2017
        ie_2017 = curricula["IE-2017"]
        self.assertEqual(ie_2017.program, "IE")
        self.assertEqual(ie_2017.cmo_reference, "CMO No. 96 s 2017")
        self.assertGreater(len(ie_2017.curriculum_subjects), 50)

    def test_admin_can_view_all_curricula(self):
        """Verify Admin/unlocked user receives all engineering curricula."""
        res = self.client.get("/api/curricula")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertFalse(data["is_locked"])
        self.assertIsNone(data["locked_department"])
        self.assertGreaterEqual(data["count"], 10)

        # Filtering with query param ?program=CPE
        res_filter = self.client.get("/api/curricula?program=CPE")
        self.assertEqual(res_filter.status_code, 200)
        data_filter = res_filter.get_json()
        self.assertEqual(data_filter["count"], 2)
        codes = [c["code"] for c in data_filter["data"]]
        self.assertIn("CPE-2024", codes)
        self.assertIn("CPE-2017", codes)

    def test_chair_curriculum_locking(self):
        """Verify Program Chair is strictly locked to their department's curricula."""
        # 1. Login as CPE Chair
        with self.client.session_transaction() as sess:
            sess["user_id"] = 101
            sess["role"] = "CHAIR"
            sess["program"] = "CPE"

        res = self.client.get("/api/curricula")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["is_locked"])
        self.assertEqual(data["locked_department"], "CPE")
        self.assertEqual(data["count"], 2)
        for cur in data["data"]:
            self.assertEqual(cur["program"], "CPE")

        # 2. Login as EE Chair
        with self.client.session_transaction() as sess:
            sess["user_id"] = 102
            sess["role"] = "CHAIR"
            sess["program"] = "EE"

        res_ee = self.client.get("/api/curricula")
        self.assertEqual(res_ee.status_code, 200)
        data_ee = res_ee.get_json()
        self.assertTrue(data_ee["is_locked"])
        self.assertEqual(data_ee["locked_department"], "EE")
        for cur in data_ee["data"]:
            self.assertEqual(cur["program"], "EE")

    def test_curriculum_subjects_department_authorization(self):
        """Verify Chair cannot access subjects for a curriculum belonging to another department."""
        cpe_cur = Curriculum.query.filter_by(code="CPE-2024").first()
        ee_cur = Curriculum.query.filter_by(code="EE-2024").first()

        with self.client.session_transaction() as sess:
            sess["user_id"] = 101
            sess["role"] = "CHAIR"
            sess["program"] = "CPE"

        # Authorized access to CPE curriculum subjects
        res_auth = self.client.get(f"/api/curricula/{cpe_cur.id}/subjects")
        self.assertEqual(res_auth.status_code, 200)
        data_auth = res_auth.get_json()
        self.assertGreater(data_auth["count"], 0)

        # Unauthorized attempt to inspect EE curriculum
        res_unauth = self.client.get(f"/api/curricula/{ee_cur.id}/subjects")
        self.assertEqual(res_unauth.status_code, 403)
        self.assertIn("Unauthorized", res_unauth.get_json()["message"])

    def test_smart_scheduler_with_selected_curriculum(self):
        """Verify Smart Schedule generator filters subjects to the chosen curriculum."""
        cpe_cur = Curriculum.query.filter_by(code="CPE-2024").first()

        # Seed minimal Section, Room, Faculty
        sec = Section(name="CPE-2A", program="CPE", year_level=2)
        room = Room(room_code="COE 101", room_type="LECTURE", capacity=45, is_available=True)
        fac = Faculty(name="Engr. John Doe", email="jdoe@bulsu.edu.ph", program="CPE", max_units=21)
        db.session.add_all([sec, room, fac])
        db.session.commit()

        # Run scheduler specifying CPE-2024 curriculum
        config = {
            "program": "CPE",
            "curriculum_id": cpe_cur.id,
            "year_level": 2,
            "semester": 1,
            "section_ids": [sec.id],
            "room_ids": [room.id],
            "faculty_ids": "all",
        }
        res = run_smart_scheduler(config)
        self.assertEqual(res["status"], "success")

    def test_smart_scheduler_rejects_cross_department_curriculum_for_chair(self):
        """Verify POST /api/scheduler/generate rejects curriculum from other departments for Chair."""
        ee_cur = Curriculum.query.filter_by(code="EE-2024").first()

        with self.client.session_transaction() as sess:
            sess["user_id"] = 101
            sess["role"] = "CHAIR"
            sess["program"] = "CPE"

        res = self.client.post("/api/scheduler/generate", json={
            "curriculum_id": ee_cur.id,
            "program": "EE",
            "year_level": 1,
            "semester": 1,
            "section_ids": [1],
            "room_ids": [1],
        })
        self.assertEqual(res.status_code, 403)
        self.assertIn("Unauthorized", res.get_json()["message"])


if __name__ == "__main__":
    unittest.main()
