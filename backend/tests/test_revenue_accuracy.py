"""Real PostgreSQL/Redis regressions for local check-in months and decimal totals."""
import os
from decimal import Decimal

import pytest
from sqlalchemy import text
from test_revenue_isolation import api, sign_in

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    not os.getenv('TEST_DATABASE_URL'), reason='Set TEST_DATABASE_URL for integration tests')]


@pytest.mark.parametrize('tenant,zone,start,end,dst_before,dst_after', [
    ('tenant-a', 'Europe/Paris', '2024-02-29T23:00:00+00:00', '2024-03-31T22:00:00+00:00',
     '2024-03-31T00:59:59+00:00', '2024-03-31T01:00:00+00:00'),
    ('tenant-b', 'America/New_York', '2024-03-01T05:00:00+00:00', '2024-04-01T04:00:00+00:00',
     '2024-03-10T06:59:59+00:00', '2024-03-10T07:00:00+00:00'),
])
async def test_local_month_boundaries_dst_and_cache(api, tenant, zone, start, end, dst_before, dst_after):
    from datetime import datetime, timedelta
    from app.core.database_pool import db_pool
    client, redis = api
    headers = await sign_in(client, tenant)
    await db_pool.initialize()
    dates = [datetime.fromisoformat(start) - timedelta(microseconds=1), datetime.fromisoformat(start),
             datetime.fromisoformat(dst_before), datetime.fromisoformat(dst_after),
             datetime.fromisoformat(end) - timedelta(microseconds=1), datetime.fromisoformat(end)]
    async with db_pool.get_session() as session:
        await session.execute(text("INSERT INTO properties(id,tenant_id,name,timezone) VALUES ('accuracy',:t,'Accuracy',:z)"), {'t':tenant,'z':zone})
        for i, date in enumerate(dates):
            await session.execute(text("""INSERT INTO reservations(id,property_id,tenant_id,check_in_date,check_out_date,total_amount)
                VALUES (:id,'accuracy',:t,:d,:out,1.005)"""), {'id':f'accuracy-{i}','t':tenant,'d':date,'out':date+timedelta(days=40)})
        await session.commit()
    try:
        for month, total, count in [(3,'4.020',4),(4,'1.005',1),(3,'4.020',4),(None,'6.030',6)]:
            params = {'property_id':'accuracy'}
            if month: params.update(month=month, year=2024)
            response = await client.get('/api/v1/dashboard/summary', params=params, headers=headers)
            assert response.status_code == 200, response.text
            assert Decimal(str(response.json()['total_revenue'])) == Decimal(total)
            assert response.json()['reservations_count'] == count
        assert len(await redis.keys('revenue:*')) == 3
    finally:
        async with db_pool.get_session() as session:
            await session.execute(text("DELETE FROM reservations WHERE property_id='accuracy'"))
            await session.execute(text("DELETE FROM properties WHERE id='accuracy'"))
            await session.commit()


@pytest.mark.parametrize('amounts,currencies,expected,display,status', [
    (['333.333','333.333','333.334'],['USD']*3,'1000.000','1000.00',200),
    (['1.005'],['EUR'],'1.005','1.01',200),
    (['10.075'],['USD'],'10.075','10.08',200),
    (['-1.005'],['USD'],'-1.005','-1.00',200),
    (['0.001']*10,['USD']*10,'0.010','0.01',200),
    (['1.000','2.000'],['USD','EUR'],None,None,422),
    (['1.000'],[None],None,None,422),
])
async def test_exact_money_and_currency(api, amounts, currencies, expected, display, status):
    from app.core.database_pool import db_pool
    client, redis = api
    headers = await sign_in(client, 'tenant-a')
    await db_pool.initialize()
    async with db_pool.get_session() as session:
        await session.execute(text("INSERT INTO properties(id,tenant_id,name,timezone) VALUES ('accuracy','tenant-a','Accuracy','Europe/Paris')"))
        for i,(amount,currency) in enumerate(zip(amounts,currencies)):
            await session.execute(text("""INSERT INTO reservations(id,property_id,tenant_id,check_in_date,check_out_date,total_amount,currency)
                VALUES (:id,'accuracy','tenant-a','2024-03-15','2024-04-15',:amount,:currency)"""),
                {'id':f'accuracy-{i}','amount':Decimal(amount),'currency':currency})
        await session.commit()
    try:
        for _ in range(2):
            response = await client.get('/api/v1/dashboard/summary?property_id=accuracy', headers=headers)
            assert response.status_code == status, response.text
            if status == 200:
                assert response.json()['total_revenue'] == expected
                assert response.json()['display_total_revenue'] == display
                assert response.json()['currency'] == currencies[0]
        if status != 200: assert not await redis.keys('revenue:*')
    finally:
        async with db_pool.get_session() as session:
            await session.execute(text("DELETE FROM reservations WHERE property_id='accuracy'"))
            await session.execute(text("DELETE FROM properties WHERE id='accuracy'"))
            await session.commit()


@pytest.mark.parametrize('params', [{'month':3},{'year':2024},{'month':13,'year':2024},{'month':3,'year':0}])
async def test_invalid_period_is_rejected(api, params):
    client,_ = api
    response = await client.get('/api/v1/dashboard/summary', params={'property_id':'prop-001',**params},
                                headers=await sign_in(client,'tenant-a'))
    assert response.status_code == 422


async def test_monthly_seed_totals_and_tenant_isolation(api):
    client, redis = api
    headers = {t: await sign_in(client,t) for t in ('tenant-a','tenant-b')}
    for i,tenant in enumerate(['tenant-a','tenant-b'] * 3):
        if i == 2: await redis.flushdb()
        response = await client.get('/api/v1/dashboard/summary',
            params={'property_id':'prop-001','month':3,'year':2024,'tenant_id':'tenant-a'}, headers=headers[tenant])
        assert response.status_code == 200
        assert Decimal(response.json()['total_revenue']) == (2250 if tenant == 'tenant-a' else 0)
    assert (await client.get('/api/v1/dashboard/summary?property_id=prop-002&month=3&year=2024',
                            headers=headers['tenant-b'])).status_code == 404


@pytest.mark.parametrize('month,year,start,end', [
    (2,2024,'2024-01-31 23:00:00+00','2024-02-29 23:00:00+00'),
    (12,2024,'2024-11-30 23:00:00+00','2024-12-31 23:00:00+00'),
])
async def test_leap_month_and_year_rollover(api, month, year, start, end):
    from datetime import datetime, timedelta
    from app.core.database_pool import db_pool
    client,_ = api
    headers = await sign_in(client,'tenant-a')
    await db_pool.initialize()
    async with db_pool.get_session() as session:
        await session.execute(text("INSERT INTO properties(id,tenant_id,name,timezone) VALUES ('accuracy','tenant-a','Accuracy','Europe/Paris')"))
        for i,date in enumerate([datetime.fromisoformat(start),datetime.fromisoformat(end)]):
            await session.execute(text("INSERT INTO reservations(id,property_id,tenant_id,check_in_date,check_out_date,total_amount) VALUES (:id,'accuracy','tenant-a',:d,:out,10)"),
                                  {'id':f'accuracy-{i}','d':date,'out':date+timedelta(days=1)})
        await session.commit()
    try:
        response = await client.get('/api/v1/dashboard/summary',params={'property_id':'accuracy','month':month,'year':year},headers=headers)
        assert response.status_code == 200
        assert Decimal(response.json()['total_revenue']) == 10
    finally:
        async with db_pool.get_session() as session:
            await session.execute(text("DELETE FROM reservations WHERE property_id='accuracy'"))
            await session.execute(text("DELETE FROM properties WHERE id='accuracy'"))
            await session.commit()
