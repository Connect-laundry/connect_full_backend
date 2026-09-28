"""Operations alerts to Simame admins (WhatsApp + SMS) for real order events.

Backend-only: the order code records an outbox event inside its own
transaction; a dispatcher sends it to the providers afterwards. Nothing here
can reject or roll back a booking.
"""
