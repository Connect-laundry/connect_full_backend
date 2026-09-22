"""HTTPS email backends: Render blocks outbound SMTP, so reset emails must be
deliverable through a provider API."""
from unittest.mock import patch

import pytest
from django.core.mail import EmailMultiAlternatives, get_connection


class _Resp:
    def __init__(self, status_code, text=''):
        self.status_code = status_code
        self.text = text


def _message(connection):
    msg = EmailMultiAlternatives('Reset Your Simame Password', 'plain body', 'Simame <sender@gmail.com>',
                                 ['customer@example.com'], connection=connection)
    msg.attach_alternative('<p>html body</p>', 'text/html')
    return msg


def test_brevo_sends_text_and_html_with_named_sender(settings):
    settings.BREVO_API_KEY = 'brevo-key'
    connection = get_connection('config.email_backends.BrevoEmailBackend')
    with patch('config.email_backends.requests.post', return_value=_Resp(201)) as post:
        assert _message(connection).send() == 1
    url, payload, headers = post.call_args.args[0], post.call_args.kwargs['json'], post.call_args.kwargs['headers']
    assert url == 'https://api.brevo.com/v3/smtp/email'
    assert headers['api-key'] == 'brevo-key'
    assert payload['sender'] == {'email': 'sender@gmail.com', 'name': 'Simame'}
    assert payload['to'] == [{'email': 'customer@example.com'}]
    assert payload['textContent'] == 'plain body' and payload['htmlContent'] == '<p>html body</p>'


def test_resend_sends_text_and_html(settings):
    settings.RESEND_API_KEY = 'resend-key'
    connection = get_connection('config.email_backends.ResendEmailBackend')
    with patch('config.email_backends.requests.post', return_value=_Resp(200)) as post:
        assert _message(connection).send() == 1
    assert post.call_args.args[0] == 'https://api.resend.com/emails'
    assert post.call_args.kwargs['headers']['Authorization'] == 'Bearer resend-key'
    assert post.call_args.kwargs['json']['html'] == '<p>html body</p>'


def test_provider_rejection_raises_so_it_is_not_reported_as_sent(settings):
    settings.BREVO_API_KEY = 'bad'
    connection = get_connection('config.email_backends.BrevoEmailBackend')
    with patch('config.email_backends.requests.post', return_value=_Resp(401, 'unauthorized')):
        with pytest.raises(Exception, match='brevo HTTP 401'):
            _message(connection).send()
