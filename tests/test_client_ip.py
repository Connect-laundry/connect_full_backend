import pytest
from django.test import RequestFactory, override_settings
from django.urls import reverse

from config.client_ip import get_client_ip

rf = RequestFactory()


def _request(xff=None, remote='10.0.0.1', **extra):
    meta = {'REMOTE_ADDR': remote, **extra}
    if xff is not None:
        meta['HTTP_X_FORWARDED_FOR'] = xff
    return rf.get('/', **meta)


@override_settings(TRUSTED_PROXY_COUNT=1, CLIENT_IP_HEADER='')
def test_client_cannot_spoof_by_prepending_x_forwarded_for():
    # Client sent "203.0.113.9"; the one trusted proxy appended the real peer.
    assert get_client_ip(_request('203.0.113.9, 198.51.100.7')) == '198.51.100.7'


@override_settings(TRUSTED_PROXY_COUNT=2, CLIENT_IP_HEADER='')
def test_two_trusted_hops_skip_the_edge_proxy():
    assert get_client_ip(_request('203.0.113.9, 198.51.100.7, 172.70.1.1')) == '198.51.100.7'


@override_settings(TRUSTED_PROXY_COUNT=2, CLIENT_IP_HEADER='')
def test_short_chain_falls_back_to_remote_addr():
    assert get_client_ip(_request('198.51.100.7', remote='10.1.1.1')) == '10.1.1.1'


@override_settings(TRUSTED_PROXY_COUNT=1, CLIENT_IP_HEADER='HTTP_TRUE_CLIENT_IP')
def test_edge_header_wins_when_configured_and_valid():
    assert get_client_ip(_request('203.0.113.9, 172.70.1.1', HTTP_TRUE_CLIENT_IP='198.51.100.7')) == '198.51.100.7'
    # A garbage header value falls back to the chain.
    assert get_client_ip(_request('203.0.113.9, 172.70.1.1', HTTP_TRUE_CLIENT_IP='not-an-ip')) == '172.70.1.1'


@override_settings(TRUSTED_PROXY_COUNT=0, CLIENT_IP_HEADER='')
def test_untrusted_chain_is_ignored_entirely():
    assert get_client_ip(_request('203.0.113.9', remote='10.2.2.2')) == '10.2.2.2'


@pytest.mark.django_db
@override_settings(IP_DIAGNOSTICS_ENABLED=False)
def test_ip_diagnostics_are_off_unless_enabled(client):
    assert client.get(reverse('request_ip_diagnostics')).status_code == 404


@pytest.mark.django_db
@override_settings(IP_DIAGNOSTICS_ENABLED=True, TRUSTED_PROXY_COUNT=1)
def test_ip_diagnostics_report_only_the_callers_own_chain(client):
    response = client.get(reverse('request_ip_diagnostics'), HTTP_X_FORWARDED_FOR='203.0.113.9, 198.51.100.7')
    body = response.json()
    assert response.status_code == 200
    assert body['x_forwarded_for_hops'] == 2
    assert body['resolved_client_ip'] == '198.51.100.7'
