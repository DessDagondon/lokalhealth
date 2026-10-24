import unittest

import pandas as pd
import sqlcipher3
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app import (
    DengueRecord,
    RecordAuditLog,
    User,
    app,
    build_standardized_upload_df,
    db,
    get_dashboard_trend,
    get_available_report_years,
    get_dashboard_years,
    get_report_summary_for_year,
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
            add_cases(2023, 20, 5, 'Surge Barangay')
            add_cases(2023, 20, 2, 'Rising Barangay')
            add_cases(2023, 20, 2, 'Stable Barangay')
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

    def test_missing_age_month_and_week_remain_missing(self):
        source = pd.DataFrame([{
            'Case ID': 'MISSING-DATA-1',
            'Age': None,
            'Morbidity Week': 12,
        }])

        result = build_standardized_upload_df(source, filename='cases.csv')

        self.assertTrue(pd.isna(result.loc[0, 'age']))
        self.assertTrue(pd.isna(result.loc[0, 'morbidity_month']))
        self.assertEqual(result.loc[0, 'morbidity_week'], 12)
        self.assertTrue(pd.isna(result.loc[0, 'year']))

    def test_invalid_age_and_month_are_rejected_with_row_context(self):
        invalid_age = pd.DataFrame([{
            'Case ID': 'INVALID-AGE',
            'Age': 'unknown',
            'Morbidity Month': 3,
        }])
        with self.assertRaisesRegex(ValueError, r'Row 2 age must be a whole number'):
            build_standardized_upload_df(invalid_age, filename='cases.csv')

        invalid_month = pd.DataFrame([{
            'Case ID': 'INVALID-MONTH',
            'Age': 3,
            'Morbidity Month': 13,
        }])
        with self.assertRaisesRegex(ValueError, r'Row 2 morbidity month must be no greater than 12'):
            build_standardized_upload_df(invalid_month, filename='cases.csv')

    def test_historical_warning_uses_latest_week_and_confirmed_counts(self):
        with app.app_context():
            trend = get_dashboard_trend(2025)

        self.assertEqual(trend['current_week'], 20)
        self.assertEqual(trend['current_count'], 8)
        self.assertEqual(trend['median'][19], 10)
        self.assertEqual(trend['baseline_75th'][19], 10)
        self.assertEqual(trend['risk_level'], 'GREEN')

    def test_suspected_only_week_is_zero_and_unrecorded_future_week_is_none(self):
        with app.app_context():
            db.session.add(DengueRecord(
                case_id='SUSPECTED-ONLY-WEEK',
                year=2025,
                morbidity_month=5,
                morbidity_week=21,
                district='Talomo',
                barangay='Suspected-only Barangay',
                age=24,
                sex='F',
                clinical_classification='Dengue without Warning Signs',
                case_classification='Suspected',
                sync_status='Synced',
            ))
            db.session.commit()
            trend = get_dashboard_trend(2025)

        self.assertEqual(trend['actual'][20], 0)
        self.assertIsNone(trend['actual'][21])

    def test_week_missing_year_is_unavailable(self):
        with app.app_context():
            trend = get_dashboard_trend(2024)

        self.assertFalse(trend['weekly_data_available'])
        self.assertTrue(all(value is None for value in trend['actual']))
        self.assertEqual(trend['risk_level'], 'BLUE')
        self.assertEqual(trend['risk_label'], 'Weekly Data Unavailable')
        self.assertEqual(trend['monthly_actual'][6], 1)

    def test_dashboard_shows_warning_threshold_and_watchlist(self):
        with app.app_context():
            user_id = User.query.filter_by(username='qualitytester').first().id

        with app.test_client() as client:
            with client.session_transaction() as session:
                session['_user_id'] = str(user_id)
                session['_fresh'] = True

            response = client.get('/dashboard?year=2025')
            no_baseline = client.get('/dashboard?year=2023').get_data(as_text=True)
            unavailable = client.get('/dashboard?year=2024').get_data(as_text=True)

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Epidemic Threshold (P', body)
        self.assertIn('ACTIVE CLUSTERS', body)
        self.assertIn('BLUE - Historical Baseline', no_baseline)
        self.assertIn('Weekly Data Unavailable', unavailable)

    def test_watchlist_district_filter_is_independent_of_dashboard_metrics(self):
        with app.app_context():
            current_week = get_dashboard_trend(2025)['current_week']
            db.session.add(DengueRecord(
                case_id='QUALITY-NORTH-1',
                year=2025,
                morbidity_month=3,
                morbidity_week=current_week,
                district='North District',
                barangay='North Barangay',
                age=24,
                sex='F',
                clinical_classification='Dengue without Warning Signs',
                case_classification='Confirmed',
                sync_status='Synced',
            ))
            db.session.add(DengueRecord(
                case_id='QUALITY-NORTH-BASELINE',
                year=2023,
                morbidity_month=3,
                morbidity_week=current_week,
                district='North District',
                barangay='North Barangay',
                age=24,
                sex='F',
                clinical_classification='Dengue without Warning Signs',
                case_classification='Confirmed',
                sync_status='Synced',
            ))
            db.session.commit()
            user_id = User.query.filter_by(username='qualitytester').first().id

        with app.test_client() as client:
            with client.session_transaction() as session:
                session['_user_id'] = str(user_id)
                session['_fresh'] = True
            all_districts = client.get('/dashboard?year=2025').get_data(as_text=True)
            filtered = client.get(
                '/dashboard?year=2025&district=North+District'
            ).get_data(as_text=True)

        self.assertIn('North Barangay', all_districts)
        self.assertIn('North Barangay', filtered)
        self.assertIn('Surge Barangay', filtered)
        self.assertIn('id="watchlist-district"', filtered)
        self.assertNotIn('id="watchlist-district" name=', filtered)
        self.assertIn("watchlistDistrict.addEventListener('change'", filtered)
        self.assertIn('TOTAL CONFIRMED CASES: 14', all_districts)
        self.assertIn('TOTAL CONFIRMED CASES: 14', filtered)

    def test_archived_records_are_excluded_from_dashboard_reports_and_exports(self):
        with app.app_context():
            user_id = User.query.filter_by(username='qualitytester').first().id
            archived = DengueRecord.query.filter(
                DengueRecord.year == 2025,
                DengueRecord.case_id == 'QUALITY-013',
            ).first()
            archived.is_archived = True
            DengueRecord.query.filter(DengueRecord.year == 2023).update(
                {DengueRecord.is_archived: True}
            )
            db.session.commit()

            self.assertNotIn(2023, get_dashboard_years())
            self.assertNotIn(2023, get_available_report_years())
            summary = get_report_summary_for_year(2025)
            self.assertEqual(summary['total_records'], 13)
            trend = get_dashboard_trend(2025)
            self.assertEqual(trend['actual'][9], 4)
            self.assertEqual(trend['baseline_75th'][9], 0)

        with app.test_client() as client:
            with client.session_transaction() as session:
                session['_user_id'] = str(user_id)
                session['_fresh'] = True

            dashboard = client.get('/dashboard?year=2025').get_data(as_text=True)
            reports = client.get('/reports').get_data(as_text=True)
            cases_csv = client.get('/reports/cases.csv?year=2025').get_data(as_text=True)

        self.assertNotIn('2023', reports)
        self.assertNotIn('QUALITY-001', cases_csv)
        self.assertIn('2025', dashboard)

    def test_record_edit_is_reflected_in_dashboard_and_report_aggregates(self):
        with app.app_context():
            user_id = User.query.filter_by(username='qualitytester').first().id
            record = DengueRecord.query.filter_by(case_id='QUALITY-013').first()
            record_id = record.id

        with app.test_client() as client:
            with client.session_transaction() as session:
                session['_user_id'] = str(user_id)
                session['_fresh'] = True

            response = client.post(f'/records/edit/{record_id}', data={
                'morbidity_month': '4',
                'morbidity_week': '30',
                'district': 'Talomo',
                'barangay': 'Peak Barangay',
                'age': '24',
                'sex': 'F',
                'clinical_classification': 'Dengue without Warning Signs',
                'case_classification': 'Suspected',
            }, follow_redirects=False)
            dashboard_response = client.get('/dashboard?year=2025')
            report_response = client.get('/reports?year=2025')
            case_export = client.get('/reports/cases.csv?year=2025').get_data(as_text=True)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertEqual(report_response.status_code, 200)
        dashboard_body = dashboard_response.get_data(as_text=True)
        self.assertIn('TOTAL CONFIRMED CASES: 12', dashboard_body)
        self.assertIn('QUALITY-013,4,30', case_export)
        self.assertIn('Suspected', case_export)
        with app.app_context():
            summary = get_report_summary_for_year(2025)
            self.assertEqual(summary['month_counts'].get(3), 12)
            self.assertEqual(summary['month_counts'].get(4), 1)
            trend = get_dashboard_trend(2025)
            self.assertEqual(trend['actual'][9], 4)
            self.assertEqual(trend['actual'][29], 0)
            audit = RecordAuditLog.query.filter_by(record_id=record_id).all()
            self.assertEqual(
                {entry.field_changed for entry in audit},
                {'morbidity_month', 'morbidity_week', 'case_classification'},
            )


if __name__ == '__main__':
    unittest.main()
