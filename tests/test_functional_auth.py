import re

from app import User, app, db
from tests.functional_test_support import IsolatedDatabaseTestCase


class FunctionalAuthenticationTest(IsolatedDatabaseTestCase):
    def test_admin_provisions_temporary_password_change_and_blocked_login(self):
        anonymous = app.test_client()
        signup_response = anonymous.post(
            '/signup',
            data={'username': 'self-registered'},
            follow_redirects=True,
        )
        self.assertIn('Self-registration is disabled', signup_response.get_data(as_text=True))
        with app.app_context():
            self.assertIsNone(User.query.filter_by(username='self-registered').first())

        admin = app.test_client()
        admin_login = admin.post(
            '/login',
            data={'username': 'admin', 'password': 'Admin123!'},
            follow_redirects=False,
        )
        self.assertEqual(admin_login.status_code, 302)

        denied_creation = anonymous.post(
            '/admin/users/create',
            data={'username': 'not-created', 'role': 'Admin'},
            follow_redirects=False,
        )
        self.assertEqual(denied_creation.status_code, 302)
        with app.app_context():
            self.assertIsNone(User.query.filter_by(username='not-created').first())

        create_response = admin.post(
            '/admin/users/create',
            data={'username': 'provisioned-user', 'role': 'BHW', 'can_create': 'on'},
            follow_redirects=True,
        )
        self.assertEqual(create_response.status_code, 200)
        temporary_password_match = re.search(
            r'Temporary password:\s*([A-Za-z0-9_-]+)',
            create_response.get_data(as_text=True),
        )
        self.assertIsNotNone(temporary_password_match)
        temporary_password = temporary_password_match.group(1)

        with app.app_context():
            provisioned = User.query.filter_by(username='provisioned-user').one()
            provisioned_id = provisioned.id
            self.assertTrue(provisioned.must_change_password)
            self.assertTrue(provisioned.can_create)
            self.assertFalse(provisioned.can_edit)
            self.assertFalse(provisioned.can_delete)

        user = app.test_client()
        bad_login = user.post(
            '/login',
            data={'username': 'provisioned-user', 'password': 'wrong-password'},
            follow_redirects=False,
        )
        self.assertEqual(bad_login.status_code, 200)
        self.assertIn('Invalid username or password', bad_login.get_data(as_text=True))

        first_login = user.post(
            '/login',
            data={'username': 'provisioned-user', 'password': temporary_password},
            follow_redirects=False,
        )
        self.assertEqual(first_login.status_code, 302)
        self.assertEqual(first_login.headers['Location'], '/change-password')
        forced_change = user.get('/records', follow_redirects=False)
        self.assertEqual(forced_change.headers['Location'], '/change-password')

        password_change = user.post(
            '/change-password',
            data={'password': 'a-private-password-2026', 'confirmation': 'a-private-password-2026'},
            follow_redirects=False,
        )
        self.assertEqual(password_change.status_code, 302)
        self.assertEqual(password_change.headers['Location'], '/dashboard')
        self.assertEqual(user.get('/dashboard').status_code, 200)

        user.get('/logout', follow_redirects=False)
        permanent_login = user.post(
            '/login',
            data={'username': 'provisioned-user', 'password': 'a-private-password-2026'},
            follow_redirects=False,
        )
        self.assertEqual(permanent_login.status_code, 302)

        blocked = admin.post(
            f'/admin/users/{provisioned_id}/toggle-block',
            follow_redirects=False,
        )
        self.assertEqual(blocked.status_code, 302)
        user.get('/logout', follow_redirects=False)
        blocked_login = user.post(
            '/login',
            data={'username': 'provisioned-user', 'password': 'a-private-password-2026'},
            follow_redirects=False,
        )
        self.assertEqual(blocked_login.status_code, 200)
        self.assertIn('This account has been blocked', blocked_login.get_data(as_text=True))

        with app.app_context():
            self.assertTrue(db.session.get(User, provisioned_id).is_blocked)
