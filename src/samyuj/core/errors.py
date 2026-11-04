from __future__ import annotations

class SamyujError(Exception):
    pass

class ProtocolError(SamyujError):
    pass

class TransportError(SamyujError):
    pass

class SessionError(SamyujError):
    pass

class PhoneFsError(SamyujError):
    pass

class PhoneFsLost(PhoneFsError):
    pass

class MediaError(SamyujError):
    pass

class PlatformError(SamyujError):
    pass
