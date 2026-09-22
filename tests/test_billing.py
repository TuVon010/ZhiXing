from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend import billing
from backend.config import settings
from backend.db import Store
from backend.planner import model_json


@pytest.fixture
def configured(db, monkeypatch):
    monkeypatch.setattr(settings, 'model_base_url', 'https://api.deepseek.com/v1')
    monkeypatch.setattr(settings, 'model_name', 'deepseek-flash')
    monkeypatch.setattr(settings, 'model_input_price', None)
    monkeypatch.setattr(settings, 'model_output_price', None)
    monkeypatch.setattr(settings, 'jev_input_price', None)
    return db


def save(db, overrides, version=0, ident='deepseek-flash'):
    return billing.save_profile(db, ident, billing.PricingUpdate(version=version, overrides=overrides))


def price(db, at='2026-09-22T10:00:00+08:00'):
    return {**billing.snapshot(db), 'captured_at':at}


def usage(**extra):
    return {'prompt_tokens':10000,'prompt_cache_hit_tokens':8000,'completion_tokens':1000,**extra}


def test_partial_zero_reset_persistence_and_snapshot(configured):
    db=configured
    before=price(db)
    updated=save(db, {'cached_input':'0','peak_cached_input':'0','input':None})
    assert updated.effective['cached_input']=='0'
    assert updated.effective['input']=='1'
    assert updated.field_sources['input']=='default'
    reopened=Store(db.path)
    assert billing.get_profile(reopened,'deepseek-flash').version==1
    reopened.engine.dispose()
    assert Decimal(billing.estimate(before,usage())['amount'])==Decimal('.01232')
    assert Decimal(billing.estimate(price(db),usage())['amount'])==Decimal('.012')
    reset=save(db, {},1)
    assert reset.effective['cached_input']=='.02'
    assert len(db.list('pricing_revision'))==2


@pytest.mark.parametrize('overrides', [
    {'input':-1},{'input':'NaN'},{'input':'Infinity'},{'input':True},{'typo':2},
    {'peak_windows':['12:00-09:00']},{'peak_windows':['09:00-12:00','11:00-14:00']},
    {'peak_weekdays':[7]},{'peak_weekdays':[True]},{'timezone':'not/a/zone'},
    {'holiday_dates':['2026-02-30']},{'currency':'yuan'}])
def test_invalid_parameters(overrides):
    with pytest.raises(ValidationError):
        billing.PricingOverrides(**overrides)


def test_currency_change_requires_explicit_rates(configured):
    with pytest.raises(ValueError,match='更换币种'):
        save(configured,{'currency':'USD'})
    updated=save(configured,{'currency':'USD','peak_enabled':False,'input':'1','cached_input':'0','output':'2'})
    assert updated.effective['currency']=='USD'


def test_concurrent_edits_conflict(configured):
    def change(n):
        try:
            save(configured,{'input':str(n)})
            return 'saved'
        except ValueError:
            return 'conflict'
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(change,[2,3]))==['conflict','saved']


@pytest.mark.parametrize('at,expected',[
    ('2026-09-22T08:59:59+08:00','offpeak'),('2026-09-22T09:00:00+08:00','peak'),
    ('2026-09-22T12:00:00+08:00','offpeak'),('2026-09-22T14:00:00+08:00','peak'),
    ('2026-09-22T18:00:00+08:00','offpeak'),('2026-09-25T10:00:00+08:00','offpeak'),
    ('2026-09-20T10:00:00+08:00','offpeak'),('2027-09-22T10:00:00+08:00','unknown')])
def test_half_open_periods_holidays_and_unknown_year(at,expected):
    assert billing.period(at,billing.defaults('deepseek-flash'))==expected


def test_cache_unknown_conflict_and_boundary_range(configured):
    snap=price(configured)
    result=billing.estimate(snap,usage(prompt_tokens_details={'cached_tokens':8000},completion_tokens_details={'reasoning_tokens':600}))
    assert Decimal(result['amount'])==Decimal('.01232')
    assert result['usage']['cache_hit_rate']==.8
    missing=billing.estimate(snap,{'prompt_tokens':10000,'completion_tokens':1000})
    assert missing['status']=='range'
    assert Decimal(missing['minimum'])==Decimal('.0084')
    assert Decimal(missing['maximum'])==Decimal('.028')
    invalid=billing.estimate(snap,usage(prompt_cache_miss_tokens=4000))
    assert invalid['usage']['cache_status']=='inconsistent'
    assert invalid['amount'] is None
    cross=billing.estimate(price(configured,'2026-09-22T11:59:59+08:00'),usage(),'2026-09-22T12:00:01+08:00')
    assert Decimal(cross['minimum'])==Decimal('.00616')
    assert Decimal(cross['maximum'])==Decimal('.01232')


def test_unknown_gateway_isolated_and_legacy_reset(configured,monkeypatch):
    monkeypatch.setattr(settings,'model_base_url','https://gateway.example/v1')
    unknown=billing.profile_id()
    assert billing.estimate(price(configured),usage())['amount'] is None
    save(configured,{'currency':'CNY','input':'2','cached_input':'.1','output':'8'},ident=unknown)
    assert billing.estimate(price(configured),usage())['amount'] is not None
    monkeypatch.setattr(settings,'model_name','another-model')
    assert billing.profile_id()!=unknown
    assert billing.estimate(price(configured),usage())['amount'] is None
    monkeypatch.setattr(settings,'model_base_url','https://api.deepseek.com/v1')
    monkeypatch.setattr(settings,'model_name','deepseek-flash')
    monkeypatch.setattr(settings,'model_input_price',3)
    assert billing.get_profile(configured,'deepseek-flash').effective['input']=='3'
    assert save(configured,{}).effective['input']=='1'


def test_real_adapter_mocked_http_freezes_price_during_request(configured,monkeypatch):
    monkeypatch.setattr(settings,'model_api_key','test-secret')
    save(configured,{'peak_enabled':False})
    def handle(request):
        save(configured,{'input':'100','cached_input':'100','output':'100','peak_enabled':False},1)
        return httpx.Response(200,json={'usage':usage(),'choices':[{'message':{'content':'not json'}}]})
    original=httpx.Client
    with patch('backend.planner.httpx.Client',side_effect=lambda **kw:original(transport=httpx.MockTransport(handle),**kw)):
        with pytest.raises(ValueError):
            model_json(configured,[])
    call=configured.list('model_call')[0]
    assert call['status']=='failed'
    assert call['body']['billing']['snapshot']['version']==1
    assert Decimal(call['body']['billing']['amount'])==Decimal('.00616')


def test_summary_separates_currencies_and_weighted_cache():
    calls=[{'body':{'billing':{'amount':'1','currency':'CNY','usage':{'input_tokens':100,'cached_tokens':100}}}},
           {'body':{'billing':{'amount':'2','currency':'USD','usage':{'input_tokens':9900,'cached_tokens':0}}}},
           {'body':{'cost':99}}]
    result=billing.summarize(calls)
    assert result['known_subtotals']=={'CNY':'1','USD':'2'}
    assert result['cache_hit_rate']==.01
    assert result['unknown_calls']==1


def test_settings_api_auth_validation_and_conflict(configured,monkeypatch):
    import backend.main as main
    monkeypatch.setattr(main,'store',configured)
    with TestClient(main.app) as client:
        assert client.get('/api/pricing').status_code==401
        client.get('/api/session')
        assert client.get('/api/pricing').status_code==200
        payload={'version':0,'overrides':{'input':'0'}}
        assert client.post('/api/pricing/deepseek-flash',json=payload).status_code==403
        headers={'X-ZhiXing-Local':'1'}
        assert client.post('/api/pricing/deepseek-flash',json=payload,headers=headers).status_code==200
        assert client.post('/api/pricing/deepseek-flash',json=payload,headers=headers).status_code==400
        payload={'version':1,'overrides':{'input':-1}}
        assert client.post('/api/pricing/deepseek-flash',json=payload,headers=headers).status_code==422
