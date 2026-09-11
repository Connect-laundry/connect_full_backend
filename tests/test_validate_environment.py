from unittest import mock

from marketplace.management.commands.validate_environment import Command, FAIL


def test_validate_environment_explains_supabase_pooler_tenant_failure():
    command = Command()
    command.network = True
    error = (
        'connection failed: FATAL: (ENOTFOUND) tenant/user '
        'postgres.xgzbibidrxdabrteizkz not found host '
        'aws-0-us-west-1.pooler.supabase.com'
    )

    with mock.patch('django.db.utils.ConnectionHandler.__getitem__', side_effect=Exception(error)):
        status, detail = command.check_database()

    assert status == FAIL
    assert 'Supabase shared pooler could not find this tenant/user' in detail
    assert 'Replace DATABASE_URL' in detail
    assert 'postgres.<project-ref>' in detail

