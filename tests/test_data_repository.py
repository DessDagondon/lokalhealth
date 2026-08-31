import unittest

from app import app, db, DengueRecord, User


class DataRepositoryRouteTest(unittest.TestCase):
    def setUp(self):
        with app.app_context():
            db.drop_all()
            db.create_all()
            user = User(username='repoadmin', role='Admin', can_create=True, can_edit=True, can_delete=True)
            user.set_password('pass')
            db.session.add(user)
            db.session.add_all([
                DengueRecord(case_id='R-2023-001', year=2023, morbidity_week=1, morbidity_month=1, district='Talomo', barangay='Bago Aplaya', age=18, sex='F', clinical_classification='Dengue without Warning Signs', case_classification='Confirmed', sync_status='Synced'),
                DengueRecord(case_id='R-2024-001', year=2024, morbidity_week=2, morbidity_month=2, district='Talomo', barangay='Matina', age=22, sex='M', clinical_classification='Dengue with Warning Signs', case_classification='Confirmed', sync_status='Synced'),
                DengueRecord(case_id='R-2025-001', year=2025, morbidity_week=3, morbidity_month=3, district='Baguio', barangay='Baguio', age=10, sex='F', clinical_classification='Severe Dengue', case_classification='Confirmed', sync_status='Synced'),
            ])
            db.session.commit()

    def test_repository_page_supports_year_filter_and_search(self):
        with app.test_client() as client:
            client.post('/login', data={'username': 'repoadmin', 'password': 'pass'}, follow_redirects=False)
            response = client.get('/repository?year=2024&search=Matina')
            body = response.get_data(as_text=True)
            self.assertEqual(response.status_code, 200)
            self.assertIn('Dengue Data Repository', body)
            self.assertIn('R-2024-001', body)
            self.assertIn('Page', body)
            self.assertIn('Previous', body)
            self.assertIn('Next', body)

    def test_admin_permission_is_labeled_archive(self):
        with app.test_client() as client:
            client.post('/login', data={'username': 'repoadmin', 'password': 'pass'}, follow_redirects=False)
            response = client.get('/admin')
            body = response.get_data(as_text=True)
            self.assertEqual(response.status_code, 200)
            self.assertIn('Archive', body)
            self.assertNotIn('Can Delete Case Records', body)


if __name__ == '__main__':
    unittest.main()
