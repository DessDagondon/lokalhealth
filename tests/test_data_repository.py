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

    def test_repository_page_shows_per_year_data_availability_summary(self):
        with app.app_context():
            db.session.add_all([
                DengueRecord(case_id='R-2023-002', year=2023, morbidity_week=5, morbidity_month=1, district='Talomo', barangay='Bago Aplaya', age=0, sex='F', clinical_classification='Dengue without Warning Signs', case_classification='Confirmed', sync_status='Synced'),
                DengueRecord(case_id='R-2023-003', year=2023, morbidity_week=6, morbidity_month=0, district='Talomo', barangay='Matina', age=35, sex='M', clinical_classification='Dengue with Warning Signs', case_classification='Confirmed', sync_status='Synced'),
            ])
            db.session.commit()

        with app.test_client() as client:
            client.post('/login', data={'username': 'repoadmin', 'password': 'pass'}, follow_redirects=False)
            response = client.get('/repository?year=all')
            body = response.get_data(as_text=True)
            self.assertEqual(response.status_code, 200)
            self.assertIn('2023', body)
            self.assertIn('Data availability summary', body)
            self.assertIn('total records', body)
            self.assertIn('Morbidity Month', body)
            self.assertIn('Age', body)
            self.assertIn('Available: ', body)
            self.assertIn('Unavailable: ', body)

    def test_reports_page_filters_export_links_by_year(self):
        with app.test_client() as client:
            client.post('/login', data={'username': 'repoadmin', 'password': 'pass'}, follow_redirects=False)
            response = client.get('/reports?year=2024')
            body = response.get_data(as_text=True)
            self.assertEqual(response.status_code, 200)
            self.assertIn('year-select', body)
            self.assertIn('/reports/summary.pdf?year=2024', body)
            self.assertIn('/reports/summary.csv?year=2024', body)
            self.assertIn('/reports/cases.csv?year=2024', body)
            self.assertIn('/reports/cases.xlsx?year=2024', body)

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
