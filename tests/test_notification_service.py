from monitor.notification_service import NotificationService
from monitor.notifiers.exceptions import BusinessError, NotificationError


class OkNotifier:
    def send(self, subject: str, body: str) -> None:
        return None


class FailingNotifier:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def send(self, subject: str, body: str) -> None:
        raise self.exc


def test_failover_uses_second_notifier() -> None:
    service = NotificationService(
        [("a", FailingNotifier(NotificationError("boom"))), ("b", OkNotifier())],
        fail_on_business_error=True,
    )
    result = service.send("s", "b")
    assert result.notifier == "b"


def test_business_error_can_stop_failover() -> None:
    service = NotificationService(
        [("a", FailingNotifier(BusinessError("bad"))), ("b", OkNotifier())],
        fail_on_business_error=False,
    )
    try:
        service.send("s", "b")
        assert False, "expected business error"
    except BusinessError:
        pass

