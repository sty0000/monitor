from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Iterable


class SMTPNotifier:
    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        sender: str,
        recipients: Iterable[str],
        use_tls: bool,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.sender = sender
        self.recipients = list(recipients)
        self.use_tls = use_tls

    def send(self, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"] = self.sender
        msg["To"] = ", ".join(self.recipients)
        msg["Subject"] = subject
        msg.set_content(body)

        if self.use_tls:
            with smtplib.SMTP(self.host, self.port, timeout=20) as server:
                server.starttls()
                if self.user:
                    server.login(self.user, self.password)
                server.send_message(msg)
            return

        with smtplib.SMTP_SSL(self.host, self.port, timeout=20) as server:
            if self.user:
                server.login(self.user, self.password)
            server.send_message(msg)
