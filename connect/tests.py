from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Connection, ConnectionRequest, Report, RevealRequest, User
from .services import (
    DAY,
    complete_reveal,
    end_connection,
    establish_connection,
    partner_respond,
    process_timers,
    report_user,
    request_connection,
    start_reveal,
    woman_reconfirm,
)


class AppTestCase(TestCase):
    password = "safe-test-password"

    def user(self, email, gender, **extra):
        values = {
            "display_name": email.split("@")[0].title(),
            "gender": gender,
            "email_verified": True,
            "photo_status": User.PhotoStatus.APPROVED,
            "onboarding_complete": True,
            "request_available_at": timezone.now() - timedelta(seconds=1),
        }
        values.update(extra)
        return User.objects.create_user(email=email, password=self.password, **values)


class MatchingTests(AppTestCase):
    def test_request_matches_available_opposite_gender_and_starts_man_timer(self):
        now = timezone.now()
        man = self.user("m@gim.ac.in", User.Gender.MAN)
        woman = self.user("w@gim.ac.in", User.Gender.WOMAN)

        connection_request, connection = request_connection(man, now=now)

        self.assertEqual(connection_request.status, ConnectionRequest.Status.MATCHED)
        self.assertEqual(connection.man, man)
        self.assertEqual(connection.woman, woman)
        man.refresh_from_db()
        self.assertEqual(man.request_available_at, now + DAY)

    def test_unmatched_request_enters_lobby_then_expires_refunded(self):
        now = timezone.now()
        man = self.user("m@gim.ac.in", User.Gender.MAN)

        waiting, connection = request_connection(man, now=now)
        self.assertIsNone(connection)
        self.assertEqual(waiting.status, ConnectionRequest.Status.QUEUED)
        man.refresh_from_db()
        self.assertLessEqual(man.request_available_at, now)

        process_timers(now=now + DAY + timedelta(seconds=1))
        waiting.refresh_from_db()
        self.assertEqual(waiting.status, ConnectionRequest.Status.EXPIRED)

    def test_man_cannot_request_again_during_rolling_window(self):
        now = timezone.now()
        man = self.user("m@gim.ac.in", User.Gender.MAN)
        self.user("w@gim.ac.in", User.Gender.WOMAN)
        request_connection(man, now=now)

        with self.assertRaises(ValidationError):
            request_connection(man, now=now + timedelta(hours=2))

    def test_sequential_rematch_is_blocked_for_seven_days(self):
        now = timezone.now()
        man = self.user("m@gim.ac.in", User.Gender.MAN)
        woman = self.user("w@gim.ac.in", User.Gender.WOMAN)
        first = establish_connection(man, woman, now=now)
        end_connection(first, ended_by=woman, now=now + timedelta(hours=1))
        man.request_available_at = now
        man.save(update_fields=["request_available_at"])

        waiting, connection = request_connection(man, now=now + timedelta(days=1))
        self.assertIsNone(connection)
        self.assertEqual(waiting.status, ConnectionRequest.Status.QUEUED)

    def test_same_pair_can_have_concurrent_connections(self):
        man = self.user("m@gim.ac.in", User.Gender.MAN)
        woman = self.user("w@gim.ac.in", User.Gender.WOMAN)
        establish_connection(man, woman)
        establish_connection(man, woman)
        self.assertEqual(Connection.objects.filter(man=man, woman=woman, status=Connection.Status.ACTIVE).count(), 2)


class RevealTests(AppTestCase):
    def setUp(self):
        self.man = self.user("m@gim.ac.in", User.Gender.MAN)
        self.woman = self.user("w@gim.ac.in", User.Gender.WOMAN)
        self.connection = establish_connection(self.man, self.woman)

    def test_man_initiated_accept_opens_photo_window_immediately(self):
        reveal = start_reveal(self.connection, self.man)
        reveal = partner_respond(reveal, self.woman, accept=True)
        self.assertEqual(reveal.status, RevealRequest.Status.WINDOW_OPEN)
        self.assertIsNotNone(reveal.window_expires_at)

    def test_woman_initiated_requires_reconfirmation_before_photo_window(self):
        reveal = start_reveal(self.connection, self.woman)
        reveal = partner_respond(reveal, self.man, accept=True)
        self.assertEqual(reveal.status, RevealRequest.Status.AWAITING_WOMAN)

        reveal = woman_reconfirm(reveal, self.woman)
        self.assertEqual(reveal.status, RevealRequest.Status.WINDOW_OPEN)

    def test_window_auto_yes_reveals_both_identities(self):
        reveal = start_reveal(self.connection, self.man)
        reveal = partner_respond(reveal, self.woman, accept=True)
        process_timers(now=reveal.window_expires_at + timedelta(seconds=1))
        self.connection.refresh_from_db()
        reveal.refresh_from_db()
        self.assertTrue(self.connection.identities_revealed)
        self.assertEqual(reveal.status, RevealRequest.Status.REVEALED)

    def test_woman_can_confirm_during_window(self):
        reveal = start_reveal(self.connection, self.man)
        reveal = partner_respond(reveal, self.woman, accept=True)
        complete_reveal(reveal, woman=self.woman)
        self.connection.refresh_from_db()
        self.assertTrue(self.connection.identities_revealed)


class ModerationTests(AppTestCase):
    def test_four_distinct_reports_trigger_48_hour_suspension(self):
        reported = self.user("m@gim.ac.in", User.Gender.MAN)
        for number in range(4):
            reporter = self.user(f"w{number}@gim.ac.in", User.Gender.WOMAN)
            connection = establish_connection(reported, reporter)
            report_user(reporter, connection, "Repeated disrespectful messages", Report.Severity.STANDARD)
            end_connection(connection, ended_by=reporter)
        reported.refresh_from_db()
        self.assertTrue(reported.is_suspended)

    def test_severe_report_immediately_blocks_pending_review(self):
        reported = self.user("m@gim.ac.in", User.Gender.MAN)
        reporter = self.user("w@gim.ac.in", User.Gender.WOMAN)
        connection = establish_connection(reported, reporter)
        report_user(reporter, connection, "Immediate safety concern", Report.Severity.SEVERE)
        reported.refresh_from_db()
        self.assertTrue(reported.is_blocked)


class ViewTests(AppTestCase):
    def test_landing_and_authenticated_dashboard_render(self):
        client = Client()
        self.assertEqual(client.get(reverse("landing")).status_code, 200)
        user = self.user("m@gim.ac.in", User.Gender.MAN)
        client.login(email=user.email, password=self.password)
        response = client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Request connection")

    def test_chat_rejects_non_participant(self):
        man = self.user("m@gim.ac.in", User.Gender.MAN)
        woman = self.user("w@gim.ac.in", User.Gender.WOMAN)
        outsider = self.user("outside@gim.ac.in", User.Gender.MAN)
        connection = establish_connection(man, woman)
        client = Client()
        client.login(email=outsider.email, password=self.password)
        self.assertEqual(client.get(reverse("chat", args=[connection.pk])).status_code, 403)
