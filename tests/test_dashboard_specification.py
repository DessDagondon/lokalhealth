from datetime import datetime

from app import (
    DengueRecord,
    User,
    app,
    calculate_dashboard_quantile,
    classify_dashboard_case,
    db,
    evaluate_dashboard_alert,
    get_dashboard_trend,
)
from tests.functional_test_support import IsolatedDatabaseTestCase


class DashboardSpecificationTest(IsolatedDatabaseTestCase):
    def test_order_statistic_quantiles_ignore_null_like_and_invalid_values(self):
        baseline = [None, '', 'nan', 'n/a', 'unavailable', 'not-a-number', 1, 3, 7]

        self.assertEqual(calculate_dashboard_quantile(baseline, 0.5), 3)
        self.assertEqual(calculate_dashboard_quantile(baseline, 0.75), 5)

    def test_risk_tiers_use_inclusive_p50_and_p75_boundaries(self):
        self.assertEqual(evaluate_dashboard_alert(2, 3, 4)['risk_level'], 'GREEN')
        self.assertEqual(evaluate_dashboard_alert(3, 3, 4)['risk_level'], 'YELLOW')
        self.assertEqual(evaluate_dashboard_alert(4, 3, 4)['risk_level'], 'YELLOW')
        self.assertEqual(evaluate_dashboard_alert(5, 3, 4)['risk_level'], 'RED')

    def test_dashboard_classification_excludes_unconfirmed_and_counts_suspect(self):
        self.assertEqual(classify_dashboard_case('Confirmed'), 'confirmed')
        self.assertEqual(classify_dashboard_case('Suspect'), 'suspected')
        self.assertEqual(classify_dashboard_case('Unconfirmed'), 'other')
        self.assertEqual(classify_dashboard_case('unavailable'), 'other')

    def test_trend_ignores_invalid_numeric_values_and_interpolates_upper_quartile(self):
        current_year = datetime.now().year
        current_week = max(4, min(datetime.now().isocalendar().week, 52))
        with app.app_context():
            case_number = 1
            for year, count in (
                (current_year - 3, 1),
                (current_year - 2, 3),
                (current_year - 1, 7),
                (current_year, 4),
            ):
                for _ in range(count):
                    db.session.add(DengueRecord(
                        case_id=f'QUANTILE-{case_number:03d}',
                        year=year,
                        morbidity_month=3,
                        morbidity_week=current_week,
                        district='Talomo',
                        barangay='Quantile Barangay',
                        age=24,
                        sex='F',
                        clinical_classification='Dengue without Warning Signs',
                        case_classification='Confirmed',
                        sync_status='Synced',
                    ))
                    case_number += 1

            db.session.add_all([
                DengueRecord(
                    case_id='INVALID-WEEK',
                    year=current_year - 1,
                    morbidity_month='n/a',
                    morbidity_week='unavailable',
                    district='Talomo',
                    barangay='Quantile Barangay',
                    age=24,
                    sex='F',
                    clinical_classification='Dengue without Warning Signs',
                    case_classification='Confirmed',
                    sync_status='Synced',
                ),
                DengueRecord(
                    case_id='INVALID-YEAR',
                    year='nan',
                    morbidity_month='',
                    morbidity_week='n/a',
                    district='Talomo',
                    barangay='Invalid Year',
                    age=24,
                    sex='F',
                    clinical_classification='Dengue without Warning Signs',
                    case_classification='Confirmed',
                    sync_status='Synced',
                ),
            ])
            db.session.commit()

            trend = get_dashboard_trend(current_year)

        week_index = current_week - 1
        self.assertEqual(trend['baseline_years'], [current_year - 3, current_year - 2, current_year - 1])
        self.assertEqual(trend['actual'][week_index], 4)
        self.assertEqual(trend['median'][week_index], 3)
        self.assertEqual(trend['baseline_75th'][week_index], 5)
        self.assertNotIn(0, trend['baseline_years'])

        client = app.test_client()
        login = client.post(
            '/login',
            data={'username': 'admin', 'password': 'Admin123!'},
            follow_redirects=False,
        )
        self.assertEqual(login.status_code, 302)
        dashboard = client.get(f'/dashboard?year={current_year}')
        self.assertEqual(dashboard.status_code, 200)

    def test_watchlist_sorts_by_risk_then_weekly_change_then_volume(self):
        current_year = datetime.now().year
        current_week = max(4, min(datetime.now().isocalendar().week, 52))
        previous_week = current_week - 1
        with app.app_context():
            admin = User(username='watchlist-admin', role='Admin')
            admin.set_password('watchlist-password')
            db.session.add(admin)

            locations = {
                'Red Rising High Volume': (7, 3),
                'Red Rising Lower Volume': (6, 2),
                'Red Smaller Rise': (6, 4),
                'Yellow Rising': (4, 1),
                'Yellow Small Rise': (3, 2),
                'Green Rising': (2, 0),
            }
            case_number = 1
            for year, count in (
                (current_year - 3, 1),
                (current_year - 2, 3),
                (current_year - 1, 5),
            ):
                for barangay in locations:
                    for _ in range(count):
                        db.session.add(DengueRecord(
                            case_id=f'BASELINE-{case_number:04d}',
                            year=year,
                            morbidity_month=3,
                            morbidity_week=current_week,
                            district='Talomo',
                            barangay=barangay,
                            age=24,
                            sex='F',
                            clinical_classification='Dengue without Warning Signs',
                            case_classification='Confirmed',
                            sync_status='Synced',
                        ))
                        case_number += 1

            for barangay, (current_count, previous_count) in locations.items():
                for week, count in (
                    (previous_week, previous_count),
                    (current_week, current_count),
                ):
                    for _ in range(count):
                        db.session.add(DengueRecord(
                            case_id=f'CURRENT-{case_number:04d}',
                            year=current_year,
                            morbidity_month=3,
                            morbidity_week=week,
                            district='Talomo',
                            barangay=barangay,
                            age=24,
                            sex='F',
                            clinical_classification='Dengue without Warning Signs',
                            case_classification='Confirmed',
                            sync_status='Synced',
                        ))
                        case_number += 1
            db.session.commit()

        client = app.test_client()
        login = client.post(
            '/login',
            data={'username': 'watchlist-admin', 'password': 'watchlist-password'},
            follow_redirects=False,
        )
        self.assertEqual(login.status_code, 302)

        response = client.get(f'/dashboard?year={current_year}')
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        ordered_locations = [
            'Red Rising High Volume',
            'Red Rising Lower Volume',
            'Red Smaller Rise',
            'Yellow Rising',
            'Yellow Small Rise',
            'Green Rising',
        ]
        positions = [body.index(name) for name in ordered_locations]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('>+4<', body)
        self.assertIn('risk-badge-danger', body)
        self.assertIn('risk-badge-warning', body)
        self.assertIn('risk-badge-success', body)
        self.assertIn('No prior-week record', body)

    def test_naive_forecast_projects_latest_zero_case_week_without_benchmarks(self):
        current_year = datetime.now().year
        current_week = max(4, min(datetime.now().isocalendar().week, 52))
        with app.app_context():
            db.session.add_all([
                DengueRecord(
                    case_id=f'SPARSE-{week}',
                    year=current_year,
                    morbidity_month=1,
                    morbidity_week=week,
                    district='Talomo',
                    barangay='Sparse Barangay',
                    age=24,
                    sex='F',
                    clinical_classification='Dengue without Warning Signs',
                    case_classification='Confirmed',
                    sync_status='Synced',
                )
                for week in (1, 2, 3)
            ])
            db.session.add(DengueRecord(
                case_id='LATEST-SUSPECTED',
                year=current_year,
                morbidity_month=3,
                morbidity_week=current_week,
                district='Talomo',
                barangay='Sparse Barangay',
                age=24,
                sex='F',
                clinical_classification='Dengue without Warning Signs',
                case_classification='Suspected',
                sync_status='Synced',
            ))
            db.session.commit()

        client = app.test_client()
        login = client.post(
            '/login',
            data={'username': 'admin', 'password': 'Admin123!'},
            follow_redirects=False,
        )
        self.assertEqual(login.status_code, 302)
        response = client.get(
            f'/api/dashboard/forecast?year={current_year}&horizon=3'
        )
        self.assertEqual(response.status_code, 200)
        forecast = response.get_json()
        self.assertEqual(forecast['current_cases'], 0)
        self.assertEqual(forecast['predicted_cases'], 0)
        self.assertEqual([item['predicted_cases'] for item in forecast['forecasts']], [0, 0, 0])
        self.assertIsNone(forecast['rmse_rf'])
        self.assertIsNone(forecast['rmse_naive'])


if __name__ == '__main__':
    unittest.main()
