import tempfile
import unittest

from fastapi.testclient import TestClient

from app import db
from app.auth import hash_password
from app.config import settings
from app.main import app


class BrandSopTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_data_dir = settings.data_dir
        settings.data_dir = self.temp_dir.name
        db.init_db()
        db.create_user("worker", hash_password("test-password"), role="super_admin")
        self.operator = db.create_user("operator", hash_password("test-password"))
        self.business = db.create_business(name="Acme")
        self.base = f"/api/businesses/{self.business['id']}/sops"
        self.client = TestClient(app)

    def tearDown(self):
        settings.data_dir = self.old_data_dir
        self.temp_dir.cleanup()

    def _login(self):
        self.client.post("/api/auth/login", json={"username": "worker", "password": "test-password"})

    def test_requires_login(self):
        self.assertEqual(self.client.get(self.base).status_code, 401)

    def test_operator_can_read_but_not_manage_brand_sops(self):
        self._login()
        created = self.client.post(
            self.base, json={"title": "Posting", "content": "Draft and review"}
        )
        self.assertEqual(created.status_code, 200)

        operator_client = TestClient(app)
        operator_client.post(
            "/api/auth/login",
            json={"username": "operator", "password": "test-password"},
        )
        db.create_task(
            business_id=self.business["id"],
            title="Read the brand SOP",
            owner="operator",
        )
        self.assertEqual(operator_client.get(self.base).status_code, 200)
        self.assertEqual(
            operator_client.post(
                self.base, json={"title": "Unauthorized SOP"}
            ).status_code,
            403,
        )
        self.assertEqual(
            operator_client.patch(
                f"{self.base}/{created.json()['id']}", json={"content": "Changed"}
            ).status_code,
            403,
        )
        self.assertEqual(operator_client.delete(f"{self.base}/{created.json()['id']}").status_code, 403)

    def test_crud_and_url_normalization(self):
        self._login()
        created = self.client.post(
            self.base, json={"title": " Posting ", "content": "1. Draft\n2. Post", "url": "docs.example.com/sop"}
        )
        self.assertEqual(created.status_code, 200, created.text)
        sop = created.json()
        self.assertEqual(sop["title"], "Posting")
        self.assertEqual(sop["url"], "https://docs.example.com/sop")

        self.assertEqual(len(self.client.get(self.base).json()), 1)

        patched = self.client.patch(f"{self.base}/{sop['id']}", json={"content": "Updated"})
        self.assertEqual(patched.status_code, 200, patched.text)
        self.assertEqual(patched.json()["content"], "Updated")
        self.assertEqual(patched.json()["title"], "Posting")

        self.assertEqual(self.client.delete(f"{self.base}/{sop['id']}").status_code, 200)
        self.assertEqual(self.client.get(self.base).json(), [])

    def test_validation_and_missing(self):
        self._login()
        self.assertEqual(self.client.post(self.base, json={"title": "   "}).status_code, 422)
        self.assertEqual(self.client.post(self.base, json={"title": "x", "url": "not a link"}).status_code, 422)
        self.assertEqual(self.client.patch(f"{self.base}/nope", json={"title": "y"}).status_code, 404)
        self.assertEqual(self.client.delete(f"{self.base}/nope").status_code, 404)
        self.assertEqual(self.client.get("/api/businesses/nope/sops").status_code, 404)


if __name__ == "__main__":
    unittest.main()
