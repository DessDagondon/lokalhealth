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

    def test_2024_dashboard_uses_prior_year_baseline(self):
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
            self.assertIn('RED - Surge State', body)
            self.assertIn('Threshold Breach &amp; Targeted Intervention', body)
            self.assertIn('Historical 75th percentile', body)

            trend = client.get('/api/dashboard/trend?year=2024').get_json()
            current_week = min(datetime.now().isocalendar().week, 52) - 1
            self.assertEqual(trend['actual'][current_week], 20)
            self.assertEqual(trend['baseline_75th'][current_week], 10.0)
            self.assertEqual(trend['pre_surge_threshold'], 7.5)
            self.assertEqual(trend['risk_level'], 'RED')

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
            self.assertIn('BLUE - Historical Baseline', body)
            self.assertIn('30 reported cases | No baseline threshold available', body)
            self.assertIn('Insufficient prior historical data to establish a P75 epidemic threshold.', body)

            trend = client.get('/api/dashboard/trend?year=2023').get_json()
            current_week = min(datetime.now().isocalendar().week, 52) - 1
            self.assertEqual(trend['actual'][current_week], 30)
            self.assertEqual(trend['baseline_75th'][current_week], 0.0)
            self.assertEqual(trend['risk_level'], 'BLUE')

    def test_multi_year_dashboard_readings_and_warning_levels(self):
        with app.app_context():
            DengueRecord.query.delete()
            showcase_week = min(datetime.now().isocalendar().week, 52)
            districts = ('Talomo', 'Buhangin', 'Agdao', 'Poblacion')
            barangays = ('Matina', 'Cabantian', 'Sasa', 'Bago Aplaya')
            case_counts = {2023: 20, 2024: 15, 2025: 30, 2026: 10}
            records = []
            case_number = 1
            for year, case_count in case_counts.items():
                for index in range(case_count):
                    records.append(DengueRecord(
                        case_id=f'SHOWCASE-{year}-{case_number:03d}',
                        year=year,
                        morbidity_month=((showcase_week - 1) // 4) + 1,
                        morbidity_week=showcase_week,
                        district=districts[index % len(districts)],
                        barangay=barangays[index % len(barangays)],
                        age=(index + 8) if index % 7 else 0,
                        sex=('F' if index % 2 else 'M'),
                        clinical_classification=(
                            'Dengue without Warning Signs'
                            if index % 3 else 'Dengue with Warning Signs'
                        ),
                        case_classification='Confirmed',
                        sync_status='Synced',
                    ))
                    case_number += 1
            db.session.add_all(records)
            db.session.commit()

        with app.test_client() as client:
            client.post(
                '/login',
                data={'username': 'dashboardtester', 'password': 'pass'},
                follow_redirects=False,
            )
            dashboard = client.get('/dashboard')
            dashboard_body = dashboard.get_data(as_text=True)
            self.assertEqual(dashboard.status_code, 200)
            self.assertIn('2026', dashboard_body)
            self.assertIn('TOTAL CONFIRMED CASES', dashboard_body)
            self.assertIn('10 pediatric (0-17)', dashboard_body)

            expected_levels = {
                2023: 'BLUE',
                2024: 'YELLOW',
                2025: 'RED',
                2026: 'GREEN',
            }
            showcase_week_index = min(datetime.now().isocalendar().week, 52) - 1
            for year, expected_level in expected_levels.items():
                trend = client.get(f'/api/dashboard/trend?year={year}').get_json()
                self.assertEqual(trend['actual'][showcase_week_index], {2023: 20, 2024: 15, 2025: 30, 2026: 10}[year])
                self.assertEqual(trend['risk_level'], expected_level)

            self.assertIn('10 reported cases', dashboard_body)
            self.assertIn('GREEN - Normal State', dashboard_body)


if __name__ == '__main__':
    unittest.main()
