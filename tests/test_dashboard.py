import unittest
from datetime import datetime

from app import app, db, DengueRecord, User, evaluate_dashboard_alert, get_dashboard_trend


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

    def test_2024_dashboard_uses_prior_year_threshold(self):
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
            self.assertIn('Early warning status for MW', body)
            self.assertIn('Historical Median (P', body)
            self.assertIn('Epidemic Threshold (P', body)

            trend = client.get('/api/dashboard/trend?year=2024').get_json()
            current_week = min(datetime.now().isocalendar().week, 52) - 1
            self.assertEqual(trend['actual'][current_week], 20)
            self.assertEqual(trend['median'][current_week], 10.0)
            self.assertEqual(trend['baseline_75th'][current_week], 10.0)
            self.assertEqual(trend['risk_level'], 'RED')

    def test_alert_levels_use_median_and_upper_quartile_boundaries(self):
        self.assertEqual(evaluate_dashboard_alert(4, 5, 7)['risk_level'], 'GREEN')
        self.assertEqual(evaluate_dashboard_alert(5, 5, 7)['risk_level'], 'YELLOW')
        self.assertEqual(evaluate_dashboard_alert(7, 5, 7)['risk_level'], 'YELLOW')
        self.assertEqual(evaluate_dashboard_alert(8, 5, 7)['risk_level'], 'RED')

    def test_trend_uses_only_the_latest_three_historical_years(self):
        with app.app_context():
            current_week = min(datetime.now().isocalendar().week, 52)
            for case_number in range(40):
                db.session.add(DengueRecord(
                    case_id=f'OLD-{case_number:03d}',
                    year=2022,
                    morbidity_month=3,
                    morbidity_week=current_week,
                    district='Talomo',
                    barangay='Older Barangay',
                    age=24,
                    sex='F',
                    clinical_classification='Dengue without Warning Signs',
                    case_classification='Confirmed',
                    sync_status='Synced',
                ))
            db.session.commit()

            trend = get_dashboard_trend(2026)

        current_week_index = min(datetime.now().isocalendar().week, 52) - 1
        self.assertEqual(trend['baseline_years'], [2023, 2024, 2025])
        self.assertEqual(trend['median'][current_week_index], 20.0)
        self.assertEqual(trend['baseline_75th'][current_week_index], 25.0)

    def test_2023_dashboard_shows_baseline_unavailable_without_prior_years(self):
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
            self.assertIn('30 reported cases', body)

            trend = client.get('/api/dashboard/trend?year=2023').get_json()
            current_week = min(datetime.now().isocalendar().week, 52) - 1
            self.assertEqual(trend['actual'][current_week], 30)
            self.assertEqual(trend['baseline_75th'][current_week], 0)
            self.assertEqual(trend['risk_level'], 'BLUE')

    def test_multi_year_dashboard_readings_use_historical_thresholds(self):
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
            self.assertIn('DEMOGRAPHICS', dashboard_body)
            self.assertIn('Male', dashboard_body)
            self.assertIn('Clinical classification', dashboard_body)
            self.assertIn('Epidemic Threshold (P', dashboard_body)

            expected_levels = {
                2023: 'BLUE',
                2024: 'GREEN',
                2025: 'RED',
                2026: 'GREEN',
            }
            showcase_week_index = min(datetime.now().isocalendar().week, 52) - 1
            for year, expected_level in expected_levels.items():
                trend = client.get(f'/api/dashboard/trend?year={year}').get_json()
                self.assertEqual(trend['actual'][showcase_week_index], {2023: 20, 2024: 15, 2025: 30, 2026: 10}[year])
                self.assertEqual(trend['risk_level'], expected_level)

            self.assertIn('10 reported cases', dashboard_body)
            self.assertIn('GREEN - Below Alert Threshold', dashboard_body)

    def test_forecast_api_returns_multiweek_projection_and_comparison_metrics(self):
        forecast_year = datetime.now().year
        with app.app_context():
            db.session.add_all([
                DengueRecord(
                    case_id=f'FORECAST-{week}',
                    year=forecast_year,
                    morbidity_month=1,
                    morbidity_week=week,
                    district='Talomo',
                    barangay='Forecast Barangay',
                    age=20,
                    sex='M' if week == 1 else 'F',
                    clinical_classification=(
                        'Severe Dengue' if week == 3 else 'Dengue without Warning Signs'
                    ),
                    case_classification='Confirmed',
                    sync_status='Synced',
                )
                for week in range(1, 5)
            ])
            db.session.add(DengueRecord(
                case_id='FORECAST-SUSPECTED',
                year=forecast_year,
                morbidity_month=1,
                morbidity_week=4,
                district='Talomo',
                barangay='Forecast Barangay',
                age=30,
                sex='M',
                clinical_classification='Non-severe Dengue',
                case_classification='Suspected',
                sync_status='Synced',
            ))
            db.session.commit()

        with app.test_client() as client:
            client.post(
                '/login',
                data={'username': 'dashboardtester', 'password': 'pass'},
                follow_redirects=False,
            )
            response = client.get(
                f'/api/dashboard/forecast?year={forecast_year}&week=4&horizon=4'
            )

        self.assertEqual(response.status_code, 200)
        forecast = response.get_json()
        self.assertEqual([item['week'] for item in forecast['forecasts']], [5, 6, 7, 8])
        for item in forecast['forecasts']:
            self.assertLessEqual(item['lower_bound'], item['predicted_cases'])
            self.assertGreaterEqual(item['upper_bound'], item['predicted_cases'])
        for metric in (
            'rmse_rf', 'mae_rf', 'rmse_naive', 'mae_naive',
            'rmse_lr', 'mae_lr', 'rmse_hybrid', 'mae_hybrid',
            'rmse_seasonal', 'mae_seasonal',
            'mean_error_rf', 'mean_error_naive', 'mean_error_lr',
            'mean_error_hybrid', 'mean_error_seasonal',
        ):
            self.assertIsNotNone(forecast[metric])
        self.assertEqual(forecast['mean_error'], forecast['mean_error_naive'])
        self.assertEqual(
            [item['predicted_cases'] for item in forecast['forecasts']],
            [forecast['current_cases']] * 4,
        )
        self.assertEqual(
            [item['week'] for item in forecast['forecasts']],
            list(range(forecast['current_week'] + 1, forecast['current_week'] + 5)),
        )

        with app.test_client() as client:
            client.post(
                '/login',
                data={'username': 'dashboardtester', 'password': 'pass'},
                follow_redirects=False,
            )
            dashboard = client.get('/dashboard').get_data(as_text=True)
        self.assertIn('Random Forest', dashboard)
        self.assertIn('Naive Persistence', dashboard)
        self.assertIn('Severe 1', dashboard)
        self.assertIn('Suspected 1', dashboard)
        self.assertIn('Male 1', dashboard)
        self.assertIn('Female 3', dashboard)


if __name__ == '__main__':
    unittest.main()
