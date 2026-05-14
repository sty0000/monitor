from __future__ import annotations


class NotificationError(RuntimeError):
    pass


class TransportError(NotificationError):
    pass


class HttpError(NotificationError):
    pass


class BusinessError(NotificationError):
    pass

