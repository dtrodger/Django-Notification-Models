"""
Notification app models
"""

import smtplib
import logging
import os
from functools import lru_cache
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from mimetypes import guess_type
from email.mime.base import MIMEBase
from email.encoders import encode_base64
import datetime

from django.db import models
from django.template.loader import render_to_string
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.contrib.postgres.fields import JSONField
import slack_sdk
from twilio import rest as twilio_rest

from api.apps.notifier import utils as notification_utils
from api import utils


log = logging.getLogger(__name__)


class SlackConnector(models.Model):
    """
    SlackConnector model
    """

    name = models.CharField(max_length=100, unique=True)
    token = models.CharField(max_length=256)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} Slack connector"

    @property
    def slack_client(self):
        """
        Returns an authenticated Slack SDK
        """

        try:
            slack_client = slack_sdk.WebClient(self.token)
        except Exception as e:
            raise notification_utils.SlackException(
                f"{self} failed to set up Slack client with {e}"
            )

        return slack_client

    def send_contextual_template_notification(
        self,
        slack_client,
        contextual_notification_template,
        gaia_user=None,
        notification_schedule=None,
        channel=None,
    ):
        """
        Sends a Slack ContextualTemplateNotification
        """

        message = contextual_notification_template.render_to_string()
        if gaia_user:
            self.send_message_to_user(slack_client, gaia_user, message)
        elif channel:
            self.send_message_to_channel(slack_client, channel, message)

        Notification = utils.go("api.apps.notifier.models.Notification")
        notification = Notification.objects.create(
            contextual_notification_template=contextual_notification_template,
            notification_schedule=notification_schedule,
            gaia_user=gaia_user,
            slack_connector=self,
        )
        log.info(f"Sent {notification} with ID {notification.id}")

    def send_message_to_channel(self, slack_client, channel, message):
        """
        Sends a notification to a Slack channel
        """

        try:
            slack_client.chat_postMessage(channel=channel, text=message)
        except Exception as e:
            raise notification_utils.SlackException(
                f"{self} failed to connect to Twilio with {e}"
            )

    def send_message_to_user(self, slack_client, gaia_user, message):
        """
        Sends a notification to a Slack channel
        """

        try:
            slack_client.chat_postMessage(user=gaia_user.slack_user, text=message)
        except Exception as e:
            raise notification_utils.SlackException(
                f"{self} failed to connect to Twilio with {e}"
            )


class TwilioConnector(models.Model):
    """
    TwilioConnector model
    """

    name = models.CharField(max_length=100, unique=True)
    account_sid = models.CharField(max_length=256)
    auth_token = models.CharField(max_length=256)
    sender = models.CharField(max_length=256)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} Twilio connector"

    @property
    def twilio_client(self):
        """
        Returns an authenticated Twilio SDK
        """

        try:
            twilio_client = twilio_rest.Client(self.account_sid, self.auth_token)
        except Exception as e:
            raise notification_utils.TwilioException(
                f"{self} failed to set up Twilio client with {e}"
            )

        return twilio_client

    def send_contextual_template_notification(
        self,
        twilio_client,
        gaia_user,
        contextual_notification_template,
        notification_schedule=None,
    ):
        """
        Sends a SMS ContextualTemplateNotification
        """

        self.send_sms(
            twilio_client,
            gaia_user.phone_number,
            contextual_notification_template.render_to_string(),
        )
        Notification = utils.go("api.apps.notifier.models.Notification")
        notification = Notification.objects.create(
            contextual_notification_template=contextual_notification_template,
            notification_schedule=notification_schedule,
            gaia_user=gaia_user,
            twilio_connector=self,
        )
        log.info(f"Sent {notification} with ID {notification.id}")

    def send_sms(self, twilio_client, recipient, message):
        """
        Sends a SMS message through Twilio
        """

        try:
            twilio_client.messages.create(
                to=recipient,
                from_=self.sender,
                body=message,
            )
            log.debug(f"Sent SMS to {recipient}")
        except Exception as e:
            raise notification_utils.TwilioException(f"{self} failed to SMS with {e}")


class SMTPConnector(models.Model):
    """
    SMTPConnector model
    """

    name = models.CharField(max_length=100, unique=True)
    host = models.CharField(max_length=100, unique=True)
    tls = models.BooleanField(default=True)
    port = models.IntegerField(default=587)
    user = models.CharField(max_length=254, blank=True, null=True)
    password = models.CharField(max_length=254, blank=True, null=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} SMTP connector"

    @staticmethod
    @lru_cache
    def logo_img():
        """
        Logo image data to embed in an email body
        """

        with open(
            os.path.join(
                settings.BASE_DIR,
                "apps",
                "core",
                "static",
                "core",
                "img",
                "logo.png",
            ),
            "rb",
        ) as fh:
            logo_data = fh.read()

        logo = MIMEImage(logo_data)
        logo.add_header("Content-ID", "<email_logo.png>")

        return logo

    @property
    def smtp_client(self):
        """
        Returns an authenticated SMTP client
        """

        try:
            client = smtplib.SMTP(self.host, self.port)
            if self.tls:
                client.ehlo()
                client.starttls()
                client.ehlo()

            client.login(self.user, self.password)
            log.debug(f"{self} set up SMTP client")
        except Exception as e:
            raise notification_utils.EmailException(
                f"{self} failed to set up SMTP client with {e}"
            )

        return client

    def send_contextual_template_notification(
        self,
        gaia_users_models,
        contextual_notification_template,
        notification_schedule=None,
    ):
        """
        Sends an contextual email notification
        """

        message = contextual_notification_template.render(gaia_users_models)
        gaia_user = gaia_users_models["gaia_user"]
        send_email_contextual_template_notification_task = utils.go("api.apps.notifier.tasks.send_email_contextual_template_notification_task")
        send_email_contextual_template_notification_task(
            self.id,
            gaia_user.id,
            contextual_notification_template.id,
            notification_schedule.id if notification_schedule else None,
            contextual_notification_template.context["email_subject"],
            html=contextual_notification_template.html,
            message=message
        )

    def send_email(
        self,
        smtp_client,
        recipient,
        subject,
        html=True,
        message=None,
        template=None,
        context=None,
        attachments=None,
    ):
        """
        Sends an email
        """

        email = MIMEMultipart()
        email["Subject"] = "{}\n".format(subject)
        email["To"] = recipient
        email["From"] = self.user

        if not context:
            context = {}

        if not message:
            message = render_to_string(template, context)

        if html:
            email.attach(MIMEText(message.encode("utf-8"), "html", _charset="utf-8"))
            email.attach(self.logo_img())
            email.content_subtype = "html"
            email.mixed_subtype = "related"
        else:
            email.attach(MIMEText(message.encode("utf-8"), "plain", _charset="utf-8"))

        if attachments:
            for attachement in attachments:
                mimetype, encoding = guess_type(attachement)
                mimetype = mimetype.split("/", 1)
                with open(attachement, "rb") as fh:
                    attachment = MIMEBase(mimetype[0], mimetype[1])
                    attachment.set_payload(fh.read())

                encode_base64(attachment)
                attachment.add_header(
                    "Content-Disposition",
                    "attachment",
                    filename=attachement.split("/")[-1],
                )
                email.attach(attachment)
                log.debug(f"{self} added email attachment {attachment}")

        try:
            smtp_client.sendmail(self.user, recipient, email.as_string())
            log.info(f"Sent email with subject {subject} to {recipient}")
        except Exception as e:
            raise notification_utils.EmailException(
                f"{self} failed to send email with {e}"
            )


class NotificationSchedule(models.Model):
    """
    NotificationSchedule model
    """

    end_trigger_choices = (
        ("Until job start", "Until job start"),
        ("End date on subject group", "End date on subject group"),
    )
    start_trigger_choices = (
        ("Before job start", "Before job start"),
        ("After job end", "After job end"),
        ("After photos available", "After photos available"),
        ("After job saved", "After job saved"),
        ("After job location changed", "After job location changed"),
        ("After start date on subject group", "After start date on subject group"),
        ("After end date on subject group", "After end date on subject group"),
    )
    trigger_type_choices = (("subject group", "subject group"), ("job", "job"))
    name = models.CharField(max_length=256, unique=True)
    active = models.BooleanField(default=True)
    all_clients = models.BooleanField(default=True)
    all_subject_groups = models.BooleanField(default=True)
    recurring = models.BooleanField(default=False, null=True, blank=True)
    recurrence_delta = models.CharField(
        max_length=7,
        choices=(
            ("seconds", "seconds"),
            ("minutes", "minutes"),
            ("hours", "hours"),
            ("days", "days"),
            ("weeks", "weeks"),
        ),
        default="days",
    )
    recurrence_delta_count = models.IntegerField(
        default=1, validators=[MaxValueValidator(1000), MinValueValidator(1)]
    )
    trigger_type = models.CharField(choices=trigger_type_choices, max_length=20)
    start_at = models.DateTimeField(null=True, blank=True)
    start_trigger = models.CharField(
        null=True, max_length=200, choices=start_trigger_choices
    )
    end_at = models.DateTimeField(null=True, blank=True)
    end_trigger = models.CharField(
        null=True, max_length=200, choices=end_trigger_choices
    )
    last_sent_at = models.DateTimeField(null=True, blank=True)
    clients = models.ManyToManyField(
        "client.Client",
        related_name="notification_schedules",
        blank=True,
    )
    clients_persons = models.BooleanField(null=True, blank=True)
    clients_schools = models.BooleanField(null=True, blank=True)
    clients_commercial_others = models.BooleanField(null=True, blank=True)
    subjects_booked = models.BooleanField(null=True)
    subjects_parents_booked = models.BooleanField(null=True)
    subjects_not_booked = models.BooleanField(null=True)
    subjects_parents_not_booked = models.BooleanField(null=True)
    employees = models.BooleanField(null=True)
    jobs_employees = models.BooleanField(null=True, blank=True)
    jobs_clients = models.BooleanField(null=True, blank=True)
    jobs_student_upload_subjects = models.BooleanField(null=True, blank=True)
    jobs_student_upload_client = models.BooleanField(null=True, blank=True)
    subject_groups = models.ManyToManyField(
        "client.SubjectGroup", related_name="notification_schedules", blank=True
    )
    contextual_notification_template = models.ForeignKey(
        "ContextualNotificationTemplate",
        related_name="notification_schedules",
        on_delete=models.SET_NULL,
        null=True,
    )
    slack_channel = models.CharField(max_length=255, null=True, blank=True)
    slack_users = models.BooleanField(default=False, null=True, blank=True)
    slack_connector = models.ForeignKey(
        SlackConnector,
        related_name="notification_schedules",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    twilio_connector = models.ForeignKey(
        TwilioConnector,
        related_name="notification_schedules",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    smtp_connector = models.ForeignKey(
        SMTPConnector,
        related_name="notification_schedules",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} notification schedule"

    def run_notification_schedule(self, job=None, subject_group=None):
        gaia_users_models = None
        if job:
            within_notification_window = self.job_within_notification_window(job)
            if within_notification_window:
                gaia_users_models = self.job_gaia_users_models(job)

        if subject_group:
            within_notification_window = self.subject_group_within_notification_window(subject_group)
            if within_notification_window:
                gaia_users_models = self.job_gaia_users_models(subject_group)

        if gaia_users_models:
            self.send_notifications(gaia_users_models)

    def within_recurring_notification_window(self):
        """
        Determines if a recurring notification is within time range to resend
        """

        within_notification_window = True
        instant = utils.get_local_now()
        if self.recurrence_delta == "seconds":
            delta = datetime.timedelta(seconds=self.recurrence_delta_count)
        elif self.recurrence_delta == "minutes":
            delta = datetime.timedelta(minutes=self.recurrence_delta_count)
        elif self.recurrence_delta == "hours":
            delta = datetime.timedelta(hours=self.recurrence_delta_count)
        elif self.recurrence_delta == "days":
            delta = datetime.timedelta(days=self.recurrence_delta_count)
        elif self.recurrence_delta == "weeks":
            delta = datetime.timedelta(weeks=self.recurrence_delta_count)

        if self.last_sent_at and (instant - self.last_sent_at) < delta:
            within_notification_window = False

        return within_notification_window

    def subject_group_within_notification_window(self, subject_group):
        """
        Determines if a schedule is within start and end triggers for notifications
        """

        instant = utils.get_local_now()
        after_start_trigger = False
        if (
            self.start_trigger == "After photos available"
            and subject_group.photos_available
        ):
            after_start_trigger = True
        elif (
            self.start_trigger == "After start date on subject group"
            and instant > subject_group.start_time
        ):
            after_start_trigger = True
        elif (
            self.start_trigger == "After end date on subject group"
            and instant > subject_group.end_time
        ):
            after_start_trigger = True

        after_end_trigger = False
        within_notification_window = False
        if after_start_trigger and self.end_at and instant > self.end_at:
            after_end_trigger = True
        elif (
            after_start_trigger
            and self.end_trigger == "End date on subject group"
            and instant > subject_group.end_time
        ):
            after_end_trigger = True
        elif after_start_trigger:
            within_notification_window = True

        if within_notification_window and self.recurring:
            within_notification_window = self.within_recurring_notification_window()

        if after_end_trigger or (not self.recurring and within_notification_window and self.last_sent_at):
            self.active = False
            self.save(update_fields=["active"])

        return within_notification_window

    def job_within_notification_window(self, job):
        """
        Determines if a schedule is within start and end triggers for notifications
        """

        instant = utils.get_local_now()
        after_start_trigger = False
        if self.start_trigger == "Before job start" and instant < job.start_time:
            after_start_trigger = True
        elif job and self.start_trigger == "After job end" and instant > job.end_time:
            after_start_trigger = True
        elif job and self.start_trigger == "After job saved":
            after_start_trigger = True
        elif self.start_trigger == "After job location changed":
            # TODO
            after_start_trigger = True

        after_end_trigger = False
        within_notification_window = False
        if after_start_trigger and self.end_at and instant > self.end_at:
            after_end_trigger = True
        elif (
            after_start_trigger
            and self.end_trigger == "Until job start"
            and instant > job.start_time
        ):
            after_end_trigger = True
        elif after_start_trigger:
            within_notification_window = True

        if after_end_trigger and self.recurring:
            within_notification_window = self.within_recurring_notification_window()

        if after_end_trigger or (not self.recurring and within_notification_window):
            self.active = False
            self.save(update_fields=["active"])

        return within_notification_window

    def unique_gaia_users_models(
        self,
        gaia_users_models,
        gaia_user,
        subject_group=None,
        job=None,
        session=None,
        employee=None,
        client=None,
        subject=None
    ):
        """
        Helper to build a list of unique GaiaUser models
        """

        gaia_user_models = {
            "gaia_user": gaia_user,
        }
        if subject_group:
            gaia_user_models["subject_group"] = subject_group

        if job:
            gaia_user_models["job"] = job

        if session:
            gaia_user_models["session"] = session

        if employee:
            gaia_user_models["employee"] = employee

        if client:
            gaia_user_models["client"] = client

        if subject:
            gaia_users_models["subject"] = subject

        if gaia_user_models not in gaia_users_models:
            gaia_users_models.append(gaia_user_models)

        return gaia_users_models

    def job_gaia_users_models(self, job):
        """
        Returns GaiaUser models for a Job trigger notification
        """

        gaia_users_models = []
        if self.employees:
            for employee in job.employees.all():
                gaia_users_models = self.unique_gaia_users_models(
                    gaia_users_models,
                    employee.gaia_user,
                    job=job,
                    employee=employee,
                )

        if (
            self.clients_persons
            or self.clients_schools
            or self.clients_commercial_others
        ):
            for client in job.clients.all():
                if self.clients_persons and client.category == "Person":
                    gaia_users_models = self.unique_gaia_users_models(
                        gaia_users_models, client.gaia_user, job=job
                    )
                if (self.clients_schools and client.category == "School") or (
                    self.clients_commercial_others
                    and (client.category == "Commercial" or client.category == "Other")
                ):
                    for gaia_user in client.contacts.all():
                        gaia_users_models = self.unique_gaia_users_models(
                            gaia_users_models, gaia_user, job=job, client=client
                        )

        if (
            self.subjects_booked
            or self.subjects_parents_booked
            or self.subjects_not_booked
            or self.subjects_parents_not_booked
        ):
            for subject_group in job.subject_groups.all():
                for subject in subject_group.subjects.all():
                    subjects_session = job.subjects_session(subject)
                    if subjects_session and (
                        self.subjects_booked or self.subjects_parents_booked
                    ):
                        if self.subjects_booked:
                            gaia_users_models = (
                                self.unique_gaia_users_models(
                                    gaia_users_models,
                                    subject.gaia_user,
                                    session=subjects_session,
                                    subject=subject,
                                    job=job,
                                    subject_group=subject_group,
                                )
                            )
                        if self.subjects_parents_booked:
                            for gaia_user in subject.parents.all():
                                gaia_users_models = (
                                    self.unique_gaia_users_models(
                                        gaia_users_models,
                                        gaia_user,
                                        subject=subject,
                                        session=subjects_session,
                                        job=job,
                                        subject_group=subject_group,
                                    )
                                )

                    if not subjects_session and (
                        self.subjects_not_booked or self.subjects_parents_not_booked
                    ):
                        if self.subjects_not_booked:
                            gaia_users_models = (
                                self.unique_gaia_users_models(
                                    gaia_users_models,
                                    subject.gaia_user,
                                    subject=subject,
                                    session=subjects_session,
                                    job=job,
                                    subject_group=subject_group,
                                )
                            )
                        if self.subjects_parents_not_booked:
                            for gaia_user in subject.parents.all():
                                gaia_users_models = (
                                    self.unique_gaia_users_models(
                                        gaia_users_models,
                                        gaia_user,
                                        subject=subject,
                                        session=subjects_session,
                                        job=job,
                                        subject_group=subject_group,
                                    )
                                )

        return gaia_users_models

    def gaia_users_models_for_subject_group(self, subject_group):
        """
        Returns GaiaUsers for a SubjectGroup trigger notification
        """

        gaia_users_models = []
        for job in subject_group.jobs.all():
            if self.employees:
                for employee in job.employees.all():
                    gaia_users_models = self.unique_gaia_users_models(
                        gaia_users_models,
                        employee.gaia_user,
                        job=job,
                        employee=employee,
                        subject_group=subject_group,
                    )
            if (
                self.clients_persons
                or self.clients_schools
                or self.clients_commercial_others
            ):
                for client in job.clients.all():
                    if self.clients_persons and client.category == "Person":
                        gaia_users_models = self.unique_gaia_users_models(
                            gaia_users_models,
                            client.gaia_user,
                            job=job,
                            client=client,
                            subject_group=subject_group,
                        )
                    if (self.clients_schools and client.category == "School") or (
                        self.clients_commercial_others
                        and (
                            client.category == "Commercial"
                            or client.category == "Other"
                        )
                    ):
                        for gaia_user in client.contacts.all():
                            gaia_users_models = self.unique_gaia_users_models(
                                gaia_users_models,
                                gaia_user,
                                job=job,
                                client=client,
                                subject_group=subject_group,
                            )

        if self.clients_persons and subject_group.client.category == "Person":
            gaia_users_models = self.unique_gaia_users_models(
                gaia_users_models,
                subject_group.client.gaia_user,
                subject_group=subject_group,
                client=subject_group.client
            )
        if (self.clients_schools and subject_group.client.category == "School") or (
            self.clients_commercial_others
            and (
                subject_group.client.category == "Commercial"
                or subject_group.client.category == "Other"
            )
        ):
            gaia_users_models = self.unique_gaia_users_models(
                gaia_users_models,
                subject_group.client.gaia_user,
                subject_group=subject_group,
                client=subject_group.client
            )
        if (
            self.subjects_booked
            or self.subjects_parents_booked
            or self.subjects_not_booked
            or self.subjects_parents_not_booked
        ):
            for subject in subject_group.subjects.all():
                if not subject_group.jobs.exists():
                    subjects_session = None
                    if self.subjects_not_booked:
                        gaia_users_models = self.unique_gaia_users_models(
                            gaia_users_models,
                            subject.gaia_user,
                            session=subjects_session,
                            subject_group=subject_group,
                        )
                    if self.subjects_parents_not_booked:
                        for gaia_user in subject.parents.all():
                            gaia_users_models = (
                                self.unique_gaia_users_models(
                                    gaia_users_models,
                                    gaia_user,
                                    session=subjects_session,
                                    subject_group=subject_group,
                                )
                            )
                else:
                    for job in subject_group.jobs.all():
                        subjects_session = job.subjects_session(subject)
                        if subjects_session and (
                            self.subjects_booked or self.subjects_parents_booked
                        ):
                            if self.subjects_booked:
                                gaia_users_models = (
                                    self.unique_gaia_users_models(
                                        gaia_users_models,
                                        subject.gaia_user,
                                        session=subjects_session,
                                        job=job,
                                        subject_group=subject_group,
                                        subject=subject
                                    )
                                )
                            if self.subjects_parents_booked:
                                for gaia_user in subject.parents.all():
                                    gaia_users_models = (
                                        self.unique_gaia_users_models(
                                            gaia_users_models,
                                            gaia_user,
                                            session=subjects_session,
                                            job=job,
                                            subject_group=subject_group,
                                            subject=subject
                                        )
                                    )

                        if not subjects_session and (
                            self.subjects_not_booked or self.subjects_parents_not_booked
                        ):
                            if self.subjects_not_booked:
                                gaia_users_models = (
                                    self.unique_gaia_users_models(
                                        gaia_users_models,
                                        subject.gaia_user,
                                        job=job,
                                        subject_group=subject_group,
                                        subject=subject
                                    )
                                )
                            if self.subjects_parents_not_booked:
                                for gaia_user in subject.parents.all():
                                    gaia_users_models = (
                                        self.unique_gaia_users_models(
                                            gaia_users_models,
                                            gaia_user,
                                            job=job,
                                            subject_group=subject_group,
                                            subject=subject
                                        )
                                    )

        return gaia_users_models

    def send_notifications(self, gaia_users_models):
        """
        Sends the cohort notifications
        """

        if self.smtp_connector:
            self.send_email_notifications(gaia_users_models)

        if self.twilio_connector:
            self.send_sms_notifications(gaia_users_models)

        if self.slack_connector:
            self.send_slack_notifications(gaia_users_models)

    def send_slack_notifications(self, gaia_users_models):
        """
        Sends a Slack notification to the cohort
        """

        pass

    def send_sms_notifications(self, gaia_users_models):
        """
        Sends a SMS notification to the cohort
        """

        sent = False
        for gaia_user_models in gaia_users_models:
            self.twilio_connector.send_contextual_template_notification(
                gaia_user_models,
                self.contextual_notification_template,
                self
            )
            if not sent:
                sent = True

        if sent:
            self.set_last_sent_at()

    def send_email_notifications(self, gaia_users_models):
        """
        Sends an email notification to the cohort
        """

        sent = False
        for gaia_user_models in gaia_users_models:
            self.smtp_connector.send_contextual_template_notification(
                gaia_user_models,
                self.contextual_notification_template,
                self,
            )
            if not sent:
                sent = True

        if sent:
            self.set_last_sent_at()

    def set_last_sent_at(self):
        """
        Sets last_sent_at to current instant
        """

        self.last_sent_at = utils.get_local_now()
        self.save(update_fields=["last_sent_at"])


class NotificationTemplate(models.Model):
    """
    NotificationTemplate model
    """

    name = models.CharField(max_length=256, unique=True)
    path = models.TextField()

    def render(self, context=None):
        """
        Constructs the template
        """

        context = context if context else {}

        return render_to_string(self.path, context)

    def __str__(self):
        return f"{self.name} notification template"


class ContextualNotificationTemplate(models.Model):
    """
    ContextualNotificationTemplate model
    """

    notification_template = models.ForeignKey(
        NotificationTemplate,
        related_name="contextual_notification_templates",
        on_delete=models.SET_NULL,
        null=True,
    )
    html = models.BooleanField(null=True, blank=True)
    context = JSONField()

    def render(self, gaia_user_models=None):
        """
        Constructs the template
        """
        context = self.dynamic_context(gaia_user_models)
        return self.notification_template.render(context)

    def dynamic_context(self, gaia_user_models=None):
        """
        Gets context including database field values
        """

        def get_model_field(context_value, model):
            """
            Helper to get a model field
            """

            field = context_value.split(".")[1]
            return getattr(model, field) if model else None

        context = {}
        if not gaia_user_models:
            gaia_user_models = {}

        gaia_user = gaia_user_models.get("gaia_user")
        subject_group = gaia_user_models.get("subject_group")
        job = gaia_user_models.get("job")
        session = gaia_user_models.get("session")
        employee = gaia_user_models.get("employee")
        client = gaia_user_models.get("client")
        subject = gaia_user_models.get("subject")
        for key, context_value in self.context.items():
            if "@" not in context_value:
                context[key] = context_value
            elif "GaiaUser" in context_value:
                context[key] = get_model_field(context_value, gaia_user)
            elif "SubjectGroup" in context_value:
                context[key] = get_model_field(context_value, subject_group)
            elif "Job" in context_value:
                context[key] = get_model_field(context_value, job)
            elif "Session" in context_value:
                context[key] = get_model_field(context_value, session)
            elif "Employee" in context_value:
                context[key] = get_model_field(context_value, employee)
            elif "Client" in context_value:
                context[key] = get_model_field(context_value, client)
            elif "Subject" in context_value:
                context[key] = get_model_field(context_value, subject)

        return context

    def __str__(self):
        return f"Contextual {self.notification_template}"


class Notification(models.Model):
    """
    Notification model
    """

    contextual_notification_template = models.ForeignKey(
        ContextualNotificationTemplate,
        related_name="notifications",
        on_delete=models.SET_NULL,
        null=True,
    )
    notification_schedule = models.ForeignKey(
        NotificationSchedule,
        related_name="notifications",
        on_delete=models.SET_NULL,
        null=True,
    )
    gaia_user = models.ForeignKey(
        "user.GaiaUser",
        related_name="notifications",
        on_delete=models.SET_NULL,
        null=True,
    )
    slack_connector = models.ForeignKey(
        SlackConnector,
        related_name="notifications",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    twilio_connector = models.ForeignKey(
        TwilioConnector,
        related_name="notifications",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    smtp_connector = models.ForeignKey(
        SMTPConnector,
        related_name="notifications",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        if self.smtp_connector:
            notification_type = "Email"
        elif self.twilio_connector:
            notification_type = "SMS"
        elif self.slack_connector:
            notification_type = "Slack"

        return f"{notification_type} notification to {self.gaia_user}"
