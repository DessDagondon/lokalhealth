import unittest
from datetime import datetime

from app import app, db, DengueRecord, User


class DashboardWarningStateTest(unittest.TestCase):
    def setUp(self):
        with app.app_context():
            db.drop_all()
            db.create_all()
            user = User(
                username='dashboardtester',
                role='Admin',
                can_create=True,
                can_edit=True,
                can_delete=True,
            )
            user.set_password('pass')
            db.session.add(user)

            current_week = min(datetime.now().isocalendar().week, 52)
            case_number = 1
            for year, case_count in ((2023, 10), (2024, 20), (2025, 30)):
                for _ in range(case_count):
                    db.session.add(DengueRecord(
                        case_id=f'DASH-{case_number:03d}',
                        year=year,
                        morbidity_month=3,
                        morbidity_week=current_week,
                        district='Talomo',
                        barangay='Test Barangay',
                        age=24,
                        sex='F',
                        clinical_classification='Dengue without Warning Signs',
                        case_classification='Confirmed',
                        sync_status='Synced',
                    ))
                    case_number += 1
            db.session.commit()

    def test_2024_dashboard_shows_yellow_warning(self):
        with app.test_client() as client:
            login = client.post(
                '/login',
                data={'username': 'dashboardtester', 'password': 'pass'},
                follow_redirects=False,
            )
            self.assertEqual(login.status_code, 302)

            response = client.get('/dashboard?year=2024')
            body = response.get_data(as_text=True)
            self.assertEqual(response.status_code, 200)
            self.assertIn('YELLOW - Warning State', body)
            self.assertIn('Pre-surge Advisory', body)
            self.assertIn('Historical 75th percentile', body)

            trend = client.get('/api/dashboard/trend?year=2024').get_json()
            current_week = min(datetime.now().isocalendar().week, 52) - 1
            self.assertEqual(trend['actual'][current_week], 20)
            self.assertEqual(trend['baseline_75th'][current_week], 25.0)
            self.assertEqual(trend['pre_surge_threshold'], 18.75)
            self.assertEqual(trend['risk_level'], 'YELLOW')

    def test_2023_dashboard_shows_red_warning_after_old_data_is_removed(self):
        with app.app_context():
            DengueRecord.query.delete()
            db.session.commit()
            current_week = min(datetime.now().isocalendar().week, 52)
            case_number = 1
            for year, case_count in ((2023, 30), (2024, 10), (2025, 20)):
                for _ in range(case_count):
                    db.session.add(DengueRecord(
                        case_id=f'RED-{case_number:03d}',
                        year=year,
                        morbidity_month=3,
                        morbidity_week=current_week,
                        district='Talomo',
                        barangay='Red Test Barangay',
                        age=24,
                        sex='F',
                        clinical_classification='Dengue without Warning Signs',
                        case_classification='Confirmed',
                        sync_status='Synced',
                    ))
                    case_number += 1
            db.session.commit()

        with app.test_client() as client:
            client.post(
                '/login',
                data={'username': 'dashboardtester', 'password': 'pass'},
                follow_redirects=False,
            )
            response = client.get('/dashboard?year=2023')
            body = response.get_data(as_text=True)
            self.assertEqual(response.status_code, 200)
            self.assertIn('RED - Surge State', body)
            self.assertIn('30 reported cases | Epidemic Threshold: 25.0', body)
            self.assertIn('Action Required: Initiate targeted vector control and localized response.', body)

            trend = client.get('/api/dashboard/trend?year=2023').get_json()
            current_week = min(datetime.now().isocalendar().week, 52) - 1
            self.assertEqual(trend['actual'][current_week], 30)
            self.assertEqual(trend['baseline_75th'][current_week], 25.0)
            self.assertEqual(trend['risk_level'], 'RED')


if __name__ == '__main__':
    unittest.main()
