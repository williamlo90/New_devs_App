from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.mark.asyncio
async def test_supabase_login_does_not_change_shared_service_client(monkeypatch):
    from app.api.v1 import login
    import supabase as sdk

    monkeypatch.setattr(login.settings, 'supabase_url', 'https://example.supabase.co')
    monkeypatch.setattr(login.settings, 'supabase_anon_key', 'test-anon-key')
    shared = MagicMock()
    shared.service.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
    monkeypatch.setattr(login, 'supabase', shared)
    user = SimpleNamespace(id='user-test', email='test@example.com',
                           app_metadata={'tenant_id':'tenant-a'}, user_metadata={})
    shared.auth.sign_in_with_password.return_value.user = user
    isolated = MagicMock()
    isolated.auth.sign_in_with_password.return_value.user = user
    factory = MagicMock(return_value=isolated)
    monkeypatch.setattr(sdk, 'create_client', factory)

    response = await login.login(login.LoginRequest(email=user.email, password='test-password'))
    assert response.user['tenant_id'] == 'tenant-a'
    shared.auth.sign_in_with_password.assert_not_called()
    isolated.auth.sign_in_with_password.assert_called_once_with({'email':user.email,'password':'test-password'})
    assert factory.call_args.args == ('https://example.supabase.co','test-anon-key')
    options = factory.call_args.kwargs['options']
    assert not options.persist_session
    assert not options.auto_refresh_token
    assert options.httpx_client.is_closed
