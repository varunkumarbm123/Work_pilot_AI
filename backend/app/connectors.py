import smtplib
from email.message import EmailMessage
from typing import Protocol

from app.config import AppConfig


class Connector(Protocol):
    name: str

    def publish(self, content: str, recipient: str | None) -> None: ...


class SampleDatabaseConnector:
    name = "sample database"

    def publish(self, content: str, recipient: str | None) -> None:
        return None


class SMTPConnector:
    name = "smtp"

    def __init__(self, config: AppConfig):
        if not all((
            config.smtp_host,
            config.smtp_port,
            config.smtp_username,
            config.smtp_password,
            config.smtp_from_email,
        )):
            raise ValueError("SMTP adapter requires SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, and SMTP_FROM_EMAIL.")
        self.config = config

    def publish(self, content: str, recipient: str | None) -> None:
        if not recipient or "@" not in recipient:
            raise ValueError("Provide a valid recipient email before approving with the SMTP connector.")
        message = EmailMessage()
        message["Subject"] = "Approved work item"
        message["From"] = self.config.smtp_from_email
        message["To"] = recipient
        message.set_content(content)
        with smtplib.SMTP(
            self.config.smtp_host,
            self.config.smtp_port,
            timeout=30,
        ) as client:
            client.starttls()
            client.login(self.config.smtp_username, self.config.smtp_password)
            client.send_message(message)


def get_connector(config: AppConfig) -> Connector:
    if config.connector_adapter == "sample":
        return SampleDatabaseConnector()
    if config.connector_adapter == "smtp":
        return SMTPConnector(config)
    raise ValueError("CONNECTOR_ADAPTER must be set to 'sample' or 'smtp'.")
