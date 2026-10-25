"""Errors whose messages are safe to return across the MCP boundary."""


class AgilityError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status
