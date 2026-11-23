import logging
import re


class OAuthQueryRedactor(logging.Filter):
    """Redact callback query strings in Django request/server logs."""
    def filter(self, record):
        message = record.getMessage()
        redacted = re.sub(r'(/(?:calendar/manage/google|booking/gmail)/callback/)\?[^\s"\']*', r'\1?[redacted]', message)
        if message != redacted:
            record.msg, record.args = redacted, ()
        return True
