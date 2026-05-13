"""
utils/email.py — Email Utility
================================
Pure Python utility — no Flask, no db, no app object needed.
IMPORTS FROM: nothing internal (Level 0 in the hierarchy)
"""

import os
import logging
from concurrent.futures import ThreadPoolExecutor
import yagmail

logger = logging.getLogger(__name__)

SENDER_EMAIL = os.environ.get('SENDER_EMAIL')
SENDER_PASSWORD = os.environ.get('SENDER_PASSWORD')
email_executor = ThreadPoolExecutor(max_workers=4)


def _send_email_sync(recipient_email, subject, body):
    """Synchronous internal function to send an email via yagmail."""
    if not SENDER_EMAIL or not SENDER_PASSWORD:
        logger.warning(
            f"--- EMAIL SKIPPED (Config missing) ---\n"
            f"To: {recipient_email}\nSub: {subject}\nBody: {body}\n---"
        )
        return False
    try:
        yagmail.SMTP(SENDER_EMAIL, SENDER_PASSWORD).send(
            to=recipient_email, subject=subject, contents=body
        )
        logger.info(f"Email sent to {recipient_email}")
        return True
    except Exception as e:
        logger.error(f"Email Error to {recipient_email}: {e}", exc_info=True)
        return False


def send_email(recipient_email, subject, body):
    """Send an email synchronously and return the delivery result."""
    return _send_email_sync(recipient_email, subject, body)


def send_email_async(recipient_email, subject, body):
    """Queue an email in the shared background pool for non-critical notifications."""
    return email_executor.submit(_send_email_sync, recipient_email, subject, body)
