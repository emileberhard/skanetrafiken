from typing import ClassVar


class SkaneError(Exception):
    code: ClassVar[int]


class UsageError(SkaneError):
    code = 2


class UnavailableError(SkaneError):
    code = 3


class InvalidResponseError(SkaneError):
    code = 4
