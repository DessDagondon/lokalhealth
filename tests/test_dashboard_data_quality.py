import unittest

import pandas as pd
import sqlcipher3
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app import (
    DengueRecord,
    User,
    app,
    build_standardized_upload_df,
    db,
    evaluate_dashboard_alert,
    get_dashboard_trend,
    get_intervention_priority,
)


class DashboardDataQualityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with app.app_context():
            cls.original_engine = db.engines[None]
            cls.test_engine = create_engine(
                'sqlite://',
                creator=lambda: sqlcipher3.connect(':memory:', check_same_thread=False),
                poolclass=StaticPool,
            )
            db.session.remove()
            db.engines[None] = cls.test_engine
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        with app.app_context():
            db.session.remove()
            db.engines[None] = cls.original_engine
        cls.test_engine.dispose()

    def setUp(self):
        with app.app_context():
            db.drop_all()
            db.create_all()
            user = User(username='qualitytester', role='Admin')
            user.set_password('pass')
            db.session.add(user)

            records = []
            case_number = 1

            def add_cases(year, week, count, barangay, month=3, classification='Confirmed'):
                nonlocal case_number
                for _ in range(count):
                    records.append(DengueRecord(
                        case_id=f'QUALITY-{case_number:03d}',
                        year=year,
                        morbidity_month=month,
                        morbidity_week=week,
                        district='Talomo',
                        barangay=barangay,
                        age=24,
                        sex='F',
                        clinical_classification='Dengue without Warning Signs',
                        case_classification=classification,
                        sync_status='Synced',
                    ))
                    case_number += 1

            add_cases(2023, 10, 10, 'Peak Barangay')
            add_cases(2023, 20, 1, 'Baseline Barangay')
            add_cases(2024, None, 1, 'No Week Barangay', month=7)
            add_cases(2025, 10, 5, 'Peak Barangay')
            add_cases(2025, 20, 5, 'Surge Barangay')
            add_cases(2025, 20, 2, 'Rising Barangay')
            add_cases(2025, 20, 1, 'Stable Barangay')
            add_cases(2025, 20, 1, 'Suspect Barangay', month=6, classification='Suspect')
            db.session.add_all(records)
            db.session.commit()

    def test_month_only_upload_does_not_invent_morbidity_week(self):
        source = pd.DataFrame([{
            'Year': 2024,
            'Case ID': 'MONTH-ONLY-1',
            'Morbidity Month': 7,
            'Barangay': 'No Week Barangay',
            'Case Classification': 'Confirmed',
        }])

        result = build_standardized_upload_df(source, filename='cases_2024.xlsx')

        self.assertEqual(result.loc[0, 'year'], 2024)
        self.assertEqual(result.loc[0, 'morbidity_month'], 7)
        self.assertTrue(pd.isna(result.loc[0, 'morbidity_week']))

    def test_alert_names_match_levels_and_watchlist_colors(self):
        self.assertEqual(evaluate_dashboard_alert(7, 10)['risk_level'], 'GREEN')
        self.assertEqual(evaluate_dashboard_alert(8, 10)['risk_label'], 'Warning State')
        self.assertEqual(evaluate_dashboard_alert(11, 10)['risk_label'], 'Surge State')
        self.assertEqual(get_intervention_priority(1), ('STABLE', 'success'))
        self.assertEqual(get_intervention_priority(2), ('RISING', 'warning'))
        self.assertEqual(get_intervention_priority(5), ('SURGE', 'danger'))

    def test_historical_warning_uses_latest_week_and_confirmed_counts(self):
        with app.app_context():
            trend = get_dashboard_trend(2025)

        self.assertEqual(trend['current_week'], 20)
        self.assertEqual(trend['current_count'], 8)
        self.assertEqual(trend['baseline_75th'][19], 1.0)
        self.assertEqual(trend['risk_level'], 'RED')
        self.assertEqual(trend['risk_label'], 'Surge State')

    def test_week_missing_year_is_unavailable_not_a_zero_case_warning(self):
        with app.app_context():
            trend = get_dashboard_trend(2024)

        self.assertFalse(trend['weekly_data_available'])
        self.assertTrue(all(value is None for value in trend['actual']))
        self.assertEqual(trend['risk_level'], 'BLUE')
        self.assertEqual(trend['risk_label'], 'Weekly Data Unavailable')
        self.assertEqual(trend['monthly_actual'][6], 1)

    def test_dashboard_shows_matching_intervention_names_and_colors(self):
        with app.app_context():
            user_id = User.query.filter_by(username='qualitytester').first().id

        with app.test_client() as client:
            with client.session_transaction() as session:
                session['_user_id'] = str(user_id)
                session['_fresh'] = True

            response = client.get('/dashboard?year=2025')
            unavailable = client.get('/dashboard?year=2024').get_data(as_text=True)

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('RED - Surge State', body)
        self.assertIn('alert alert-danger', body)
        self.assertIn('bg-danger">SURGE</span>', body)
        self.assertIn('bg-warning">RISING</span>', body)
        self.assertIn('bg-success">STABLE</span>', body)

        self.assertIn('Weekly case count unavailable', unavailable)
        self.assertIn('alert alert-info', unavailable)
        self.assertIn('Weekly intervention priorities are unavailable', unavailable)
        self.assertNotIn('STABLE</span>', unavailable)


if __name__ == '__main__':
    unittest.main()
