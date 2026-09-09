"""Stage 4.5 checks: role permissions, backup/restore and model history.

Frontend-only concerns (Tamil/English toggle, button disabling) are not
covered here; everything below exercises the enforced backend rules.
"""

import io
import json
import unittest
import zipfile
from pathlib import Path

from app import app
from stage4 import (ROLE_PERMISSIONS, build_backup, model_history, require_role,
                    restore_backup)
import stage4


class RoleMatrixTests(unittest.TestCase):
    def test_every_role_exists_and_views(self):
        for role in ROLE_PERMISSIONS:
            self.assertEqual(require_role(role, "view"), require_role(role, "view"))

    def test_operator_cannot_escalate(self):
        for blocked in ("config", "stress", "report", "resolve", "verify", "start_repair"):
            with self.assertRaises(ValueError):
                require_role("operator", blocked)

    def test_engineer_cannot_manage(self):
        for blocked in ("config", "stress", "restore"):
            with self.assertRaises(ValueError):
                require_role("maintenance_engineer", blocked)
        require_role("maintenance_engineer", "verify")  # allowed

    def test_manager_holds_everything(self):
        for role_perms in ROLE_PERMISSIONS.values():
            for permission in role_perms:
                require_role("manager", permission)

    def test_unknown_role_rejected(self):
        with self.assertRaises(ValueError):
            require_role("superuser", "view")


class BackupRestoreTests(unittest.TestCase):
    def test_round_trip_preserves_state(self):
        saved = stage4.TICKETS_PATH.read_text() if stage4.TICKETS_PATH.exists() else None
        self.addCleanup(lambda: (stage4.TICKETS_PATH.write_text(saved) if saved is not None
                                 else stage4.TICKETS_PATH.unlink(missing_ok=True)))
        stage4.create_ticket("leak", "Backup round-trip ticket", path=None)
        blob = build_backup()
        with zipfile.ZipFile(io.BytesIO(blob)) as bundle:
            self.assertIn("backup_info.json", bundle.namelist())
            self.assertIn("maintenance.json", bundle.namelist())
        before = stage4.list_tickets()
        restored = restore_backup(blob)
        self.assertIn("maintenance.json", restored)
        self.assertEqual(stage4.list_tickets(), before)

    def test_rejects_bad_zip_and_unknown_files(self):
        with self.assertRaises(ValueError):
            restore_backup(b"not a zip")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr("evil.sh", "rm -rf /")
        with self.assertRaises(ValueError):
            restore_backup(buffer.getvalue())

    def test_rejects_invalid_json_payload(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr("plant_config.json", "{not json")
        with self.assertRaises(ValueError):
            restore_backup(buffer.getvalue())


class ModelHistoryTests(unittest.TestCase):
    def test_history_reports_both_models(self):
        history = model_history()["history"]
        self.assertEqual(len(history), 2)
        for record in history:
            self.assertTrue(record["trained"], record.get("note"))
            self.assertIn("trained_at", record)
            self.assertIn("sklearn_version", record)
            self.assertIsNotNone(record["evaluation"])
        stage3 = history[1]["evaluation"]
        self.assertGreaterEqual(stage3["macro_f1"], 0.85)


class EndpointRoleTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.tickets_path = stage4.TICKETS_PATH
        self.backup_tickets = self.tickets_path.read_text() if self.tickets_path.exists() else None
        self.config_path = stage4.CONFIG_PATH
        self.backup_config = self.config_path.read_text() if self.config_path.exists() else None

    def tearDown(self):
        if self.backup_tickets is not None:
            self.tickets_path.write_text(self.backup_tickets)
        elif self.tickets_path.exists():
            self.tickets_path.unlink()
        if self.backup_config is not None:
            self.config_path.write_text(self.backup_config)
        elif self.config_path.exists():
            self.config_path.unlink()

    def test_status_lists_roles(self):
        data = self.client.get("/api/stage4/status").get_json()
        self.assertEqual(set(data["roles"]), set(ROLE_PERMISSIONS))

    def test_operator_blocked_from_config_and_stress(self):
        body = {"role": "operator", "site_name": "hax"}
        self.assertEqual(self.client.post("/api/stage4/config", json=body).status_code, 403)
        self.assertEqual(self.client.post("/api/stage4/stress", json={"role": "operator"}).status_code, 403)

    def test_manager_can_update_config(self):
        response = self.client.post("/api/stage4/config",
                                    json={"role": "manager", "site_name": "Role test site"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["site_name"], "Role test site")

    def test_operator_ticket_lifecycle_limits(self):
        created = self.client.post("/api/maintenance/tickets", json={
            "role": "operator", "fault_type": "leak", "title": "Operator ticket"})
        self.assertEqual(created.status_code, 200)
        ticket_id = created.get_json()["id"]
        ok = self.client.post(f"/api/maintenance/tickets/{ticket_id}/action",
                              json={"role": "operator", "action": "acknowledge"})
        self.assertEqual(ok.status_code, 200)
        blocked = self.client.post(f"/api/maintenance/tickets/{ticket_id}/action",
                                   json={"role": "operator", "action": "start_repair"})
        self.assertEqual(blocked.status_code, 403)
        promoted = self.client.post(f"/api/maintenance/tickets/{ticket_id}/action",
                                    json={"role": "maintenance_engineer", "action": "start_repair"})
        self.assertEqual(promoted.status_code, 200)
        self.assertEqual(promoted.get_json()["state"], "in_repair")
        self.assertIn("(Maintenance engineer)", promoted.get_json()["logs"][-1]["operator"])

    def test_backup_endpoint_serves_zip(self):
        response = self.client.get("/api/stage4/backup?role=operator")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data.startswith(b"PK"))

    def test_restore_endpoint_requires_file(self):
        response = self.client.post("/api/stage4/restore?role=manager")
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
