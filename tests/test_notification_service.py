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




def test_notification_service_records_channel_health() -> None:
    from monitor.notification_service import NotificationService

    class OkNotifier:
        def send(self, subject, body):
            return None

    service = NotificationService([("ok", OkNotifier())], fail_on_business_error=False)
    result = service.send("subject", "body")

    assert result.notifier == "ok"
    assert service.get_channel_health()["ok"]["status"] == "ok"
    assert service.get_channel_health()["ok"]["attempts"] == 1



def test_failover_business_error_can_continue_when_enabled() -> None:
    service = NotificationService(
        [("a", FailingNotifier(BusinessError("bad"))), ("b", OkNotifier())],
        fail_on_business_error=True,
    )

    result = service.send("s", "b")

    assert result.notifier == "b"
    assert result.attempted == ["a", "b"]
    health = service.get_channel_health()
    assert health["a"]["status"] == "error"
    assert health["b"]["status"] == "ok"


def test_notification_service_marks_all_failed_channels() -> None:
    service = NotificationService(
        [("a", FailingNotifier(NotificationError("boom"))), ("b", FailingNotifier(RuntimeError("nope")))],
        fail_on_business_error=True,
    )

    try:
        service.send("s", "b")
        assert False, "expected NotificationError"
    except NotificationError as exc:
        assert "All notifiers failed" in str(exc)

    health = service.get_channel_health()
    assert health["a"]["status"] == "error"
    assert health["b"]["status"] == "error"
    assert health["a"]["attempts"] == 1
    assert health["b"]["attempts"] == 1


def test_notification_service_direct_channel_does_not_failover() -> None:
    service = NotificationService(
        [("a", FailingNotifier(NotificationError("boom"))), ("b", OkNotifier())],
        fail_on_business_error=True,
    )

    try:
        service.send("s", "b", channel_name="a")
        assert False, "expected NotificationError"
    except NotificationError:
        pass

    health = service.get_channel_health()
    assert health["a"]["status"] == "error"
    assert health["b"]["status"] == "unknown"
