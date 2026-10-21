import tempfile
import unittest

from app import db
from app.config import settings


class NCCBrandLayerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_data_dir = settings.data_dir
        settings.data_dir = self.temp_dir.name
        db.init_db()

    def tearDown(self):
        settings.data_dir = self.old_data_dir
        self.temp_dir.cleanup()

    def test_seed_ncc_brand_portfolio_creates_real_brand_contexts(self):
        result = db.seed_ncc_brand_portfolio()

        self.assertTrue(result["seeded"])
        self.assertIn("sixer-supreme", result["created_slugs"])
        self.assertIn("la-pret", result["created_slugs"])
        self.assertIn("ww-fabrics", result["created_slugs"])

        gxl = db.get_business_by_slug("sixer-supreme")
        self.assertIsNotNone(gxl)
        self.assertEqual(gxl["status"], "active")
        self.assertIn("GXL", gxl["context_md"])
        self.assertIn("Account management", gxl["context_md"])
        self.assertIn("Pack snapshot, 30 September 2026", gxl["context_md"])
        self.assertIn("2 for $8 promotion", gxl["context_md"])

        parked = db.get_business_by_slug("araish")
        self.assertIsNotNone(parked)
        self.assertEqual(parked["status"], "parked")
        self.assertIn("parked", parked["context_md"].lower())

        prospect = db.get_business_by_slug("the-social-links")
        self.assertIsNotNone(prospect)
        self.assertEqual(prospect["status"], "prospect")
        self.assertIn("Prospect", prospect["context_md"])

        # Idempotent seed should not duplicate brands.
        second = db.seed_ncc_brand_portfolio()
        self.assertFalse(second["seeded"])
        self.assertEqual(second["created_count"], 0)
        self.assertEqual(second["updated_count"], 0)

    def test_seed_adds_snapshot_without_replacing_existing_context(self):
        db.create_business(
            name="Gorilla X Labs",
            slug="sixer-supreme",
            description="Existing description",
            context_md="# Gorilla X Labs\n\nUser-authored account note.",
            status="active",
        )

        result = db.seed_ncc_brand_portfolio()
        gxl = db.get_business_by_slug("sixer-supreme")

        self.assertEqual(result["updated_count"], 1)
        self.assertIn("User-authored account note.", gxl["context_md"])
        self.assertIn("Pack snapshot, 30 September 2026", gxl["context_md"])

        second = db.seed_ncc_brand_portfolio()
        self.assertEqual(second["updated_count"], 0)


if __name__ == "__main__":
    unittest.main()
