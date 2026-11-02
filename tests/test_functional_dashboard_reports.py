import csv
from datetime import datetime
from io import BytesIO, StringIO

import pandas as pd

from app import DengueRecord, User, app, db
from tests.functional_test_support import IsolatedDatabaseTestCase


class FunctionalDashboardAndReportsTest(IsolatedDatabaseTestCase):
    def test_dashboard_quantiles_naive_forecast_and_all_report_downloads(self):
        current_year = datetime.now().year
        current_week = max(4, min(datetime.now().isocalendar().week, 52))
        historical_counts = {
            current_year - 3: 2,
            current_year - 2: 6,
            current_year - 1: 10,
        }
        latest_week_case_id = f'CURRENT-{sum(historical_counts.values()) + 7:03d}'
        with app.app_context():
            analyst = User(username='report-analyst', role='Admin')
            analyst.set_password('analyst-password')
            db.session.add(analyst)

            record_number = 1
            for year, count in historical_counts.items():
                for _ in range(count):
                    db.session.add(DengueRecord(
                        case_id=f'HISTORY-{record_number:03d}',
                        year=year,
                        morbidity_month=3,
                        morbidity_week=current_week,
                        district='Talomo',
                        barangay='Historical Barangay',
                        age=24,
                        sex='F',
                        clinical_classification='Dengue without Warning Signs',
                        case_classification='Confirmed',
                        sync_status='Synced',
                    ))
                    record_number += 1

            for week, count in ((1, 1), (2, 2), (3, 3), (current_week, 7)):
                for _ in range(count):
                    db.session.add(DengueRecord(
                        case_id=f'CURRENT-{record_number:03d}',
                        year=current_year,
                        morbidity_month=3,
                        morbidity_week=week,
                        district='Talomo',
                        barangay='Current Barangay',
                        age=24,
                        sex='F',
                        clinical_classification='Dengue without Warning Signs',
                        case_classification='Confirmed',
                        sync_status='Synced',
                    ))
                    record_number += 1

            db.session.add(DengueRecord(
                case_id='ARCHIVED-EXCLUDED',
                year=current_year,
                morbidity_month=3,
                morbidity_week=current_week,
                district='Talomo',
                barangay='Archived Barangay',
                age=24,
                sex='F',
                clinical_classification='Dengue without Warning Signs',
                case_classification='Confirmed',
                sync_status='Synced',
                is_archived=True,
            ))
            db.session.commit()

        client = app.test_client()
        unauthorized = client.get('/reports/summary.pdf', follow_redirects=False)
        self.assertEqual(unauthorized.status_code, 302)
        self.assertTrue(unauthorized.headers['Location'].startswith('/login'))

        login = client.post(
            '/login',
            data={'username': 'report-analyst', 'password': 'analyst-password'},
            follow_redirects=False,
        )
        self.assertEqual(login.status_code, 302)

        trend_response = client.get(f'/api/dashboard/trend?year={current_year}')
        self.assertEqual(trend_response.status_code, 200)
        trend = trend_response.get_json()
        week_index = current_week - 1
        self.assertEqual(trend['actual'][week_index], 7)
        self.assertEqual(trend['median'][week_index], 6.0)
        self.assertEqual(trend['baseline_75th'][week_index], 8.0)
        self.assertEqual(trend['risk_level'], 'YELLOW')

        forecast_response = client.get(
            f'/api/dashboard/forecast?year={current_year}&week={current_week}&horizon=3'
        )
        self.assertEqual(forecast_response.status_code, 200)
        forecast = forecast_response.get_json()
        self.assertEqual(forecast['current_cases'], 7)
        self.assertEqual(forecast['predicted_cases'], 7)
        self.assertEqual(forecast['mean_error'], forecast['mean_error_naive'])
        self.assertEqual(
            [item['predicted_cases'] for item in forecast['forecasts']],
            [7, 7, 7],
        )

        dashboard = client.get(f'/dashboard?year={current_year}')
        self.assertEqual(dashboard.status_code, 200)
        dashboard_body = dashboard.get_data(as_text=True)
        self.assertIn('Naïve persistence', dashboard_body)
        self.assertIn('Historical median · P50', dashboard_body)
        self.assertNotIn('ARCHIVED-EXCLUDED', dashboard_body)

        reports = client.get(f'/reports?year={current_year}')
        self.assertEqual(reports.status_code, 200)
        self.assertIn(
            f'/reports/summary.pdf?year={current_year}',
            reports.get_data(as_text=True),
        )

        pdf = client.get(f'/reports/summary.pdf?year={current_year}')
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf.mimetype, 'application/pdf')
        self.assertTrue(pdf.data.startswith(b'%PDF-'))

        summary_csv = client.get(f'/reports/summary.csv?year={current_year}')
        self.assertEqual(summary_csv.status_code, 200)
        summary_rows = list(csv.DictReader(StringIO(summary_csv.get_data(as_text=True))))
        self.assertTrue(summary_rows)
        self.assertEqual({row['year'] for row in summary_rows}, {str(current_year)})

        cases_csv = client.get(f'/reports/cases.csv?year={current_year}')
        self.assertEqual(cases_csv.status_code, 200)
        case_rows = list(csv.DictReader(StringIO(cases_csv.get_data(as_text=True))))
        exported_ids = {row['case_id'] for row in case_rows}
        self.assertIn(latest_week_case_id, exported_ids)
        self.assertNotIn('ARCHIVED-EXCLUDED', exported_ids)

        cases_excel = client.get(f'/reports/cases.xlsx?year={current_year}')
        self.assertEqual(cases_excel.status_code, 200)
        excel_rows = pd.read_excel(BytesIO(cases_excel.data))
        self.assertIn(latest_week_case_id, set(excel_rows['case_id']))
        self.assertNotIn('ARCHIVED-EXCLUDED', set(excel_rows['case_id']))
