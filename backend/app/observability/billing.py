"""Versioned local price estimates, independent of business execution and invoices."""
import hashlib
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator
from backend.app.core.config import settings
from backend.app.persistence.store import now

Price = Annotated[Decimal, Field(ge=0, le=1000000000, max_digits=22, decimal_places=12, allow_inf_nan=False)]
RATE_KEYS = ('input', 'cached_input', 'output', 'peak_input', 'peak_cached_input', 'peak_output')
DEFAULT_VERSION = '2026-09-22.1'
HOLIDAYS = [f'2026-{month:02d}-{day:02d}' for month, first, last in
            [(1,1,3),(2,15,23),(4,4,6),(5,1,5),(6,19,21),(9,25,27),(10,1,7)] for day in range(first,last+1)]
CALENDAR_SOURCE = 'https://www.beijing.gov.cn/fuwu/bmfw/sy/jrts/202511/t20251104_4258838.html'


class PricingOverrides(BaseModel):
    model_config = ConfigDict(extra='forbid')
    input: Price | None = None
    cached_input: Price | None = None
    output: Price | None = None
    peak_input: Price | None = None
    peak_cached_input: Price | None = None
    peak_output: Price | None = None
    currency: str | None = Field(default=None, pattern=r'^[A-Z]{3}$')
    peak_enabled: bool | None = Field(default=None, strict=True)
    timezone: str | None = Field(default=None, max_length=80)
    peak_windows: list[str] | None = Field(default=None, max_length=12)
    peak_weekdays: list[int] | None = Field(default=None, max_length=7)
    holiday_dates: list[str] | None = Field(default=None, max_length=1000)
    calendar_years: list[int] | None = Field(default=None, max_length=30)

    @field_validator(*RATE_KEYS, mode='before')
    @classmethod
    def no_boolean_prices(cls, value):
        if isinstance(value, bool):
            raise ValueError('价格必须是数字，不能是布尔值')
        return value

    @field_validator('timezone')
    @classmethod
    def valid_zone(cls, value):
        if value is not None:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError('无效时区')
        return value

    @field_validator('peak_windows')
    @classmethod
    def valid_windows(cls, value):
        if value is not None:
            import re
            spans = []
            for window in value:
                if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d-(?:[01]\d|2[0-3]):[0-5]\d', window):
                    raise ValueError('时段格式为 HH:MM-HH:MM；跨日请拆分')
                start, end = window.split('-')
                if start >= end:
                    raise ValueError('时段结束必须晚于开始')
                spans.append((start, end))
            spans.sort()
            if any(a[1] > b[0] for a, b in zip(spans, spans[1:])):
                raise ValueError('高峰时段不能重叠')
        return value

    @field_validator('peak_weekdays', 'calendar_years', mode='before')
    @classmethod
    def valid_integers(cls, value, info):
        if value is not None:
            lo, hi = (0, 6) if info.field_name == 'peak_weekdays' else (2000, 2100)
            if not isinstance(value, list) or any(type(v) is not int or not lo <= v <= hi for v in value):
                raise ValueError('无效的星期或日历年份')
        return value

    @field_validator('holiday_dates')
    @classmethod
    def valid_dates(cls, value):
        for day in value or []:
            if date.fromisoformat(day).isoformat() != day:
                raise ValueError('日期格式为 YYYY-MM-DD')
        return value


class PricingUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: int = Field(ge=0, strict=True)
    overrides: PricingOverrides


class PricingProfile(BaseModel):
    id: str
    label: str
    active: bool
    version: int
    default_version: str
    source: str | None
    calendar_source: str | None
    defaults: dict[str, Any]
    overrides: dict[str, Any]
    effective: dict[str, Any]
    field_sources: dict[str, str]


def profile_id():
    url = urlsplit(settings.model_base_url)
    if url.scheme == 'https' and url.netloc == 'api.deepseek.com' and url.path.rstrip('/') in {'', '/v1'} and not url.query and not url.fragment:
        if settings.model_name in {'deepseek-flash','deepseek-v4-flash','deepseek-v4-flash-vision-exp'}:
            return 'deepseek-flash'
        if settings.model_name == 'deepseek-v4-pro':
            return 'deepseek-v4-pro'
    identity = settings.model_base_url.rstrip('/') + ':' + settings.model_name
    return 'model-' + hashlib.sha256(identity.encode()).hexdigest()[:16]


def defaults(ident):
    rates = {
        'deepseek-flash': ('1', '.02', '4', '2', '.04', '8'),
        'deepseek-v4-pro': ('4.5', '.15', '13.5', '9', '.30', '27'),
    }
    known = ident in rates
    deepseek = ident.startswith('deepseek-') and known
    return {**dict(zip(RATE_KEYS, rates.get(ident, (None,) * 6))),
            'currency': 'CNY' if deepseek else None,
            'peak_enabled': deepseek, 'timezone': 'Asia/Shanghai',
            'peak_windows': ['09:00-12:00','14:00-18:00'], 'peak_weekdays': [0,1,2,3,4],
            'holiday_dates': list(HOLIDAYS) if deepseek else [], 'calendar_years': [2026] if deepseek else []}


def get_profile(db, ident, conn=None):
    active = {profile_id()}
    if ident not in {'deepseek-flash','deepseek-v4-pro'} | active:
        raise ValueError('未知计价配置；自定义配置按当前接口和模型隔离')
    base = defaults(ident)
    try:
        row = db.get('pricing:' + ident, conn)['body']
    except KeyError:
        row = None
    overrides = row['overrides'] if row else {}
    origin = 'custom'
    # Old env prices remain compatible until the first explicit UI save/reset.
    if row is None:
        origin = 'legacy_env'
        if ident == profile_id() and settings.model_input_price is not None:
            overrides.update({key: str(settings.model_input_price) for key in ('input','cached_input','peak_input','peak_cached_input')})
        if ident == profile_id() and settings.model_output_price is not None:
            overrides.update({key: str(settings.model_output_price) for key in ('output','peak_output')})
    effective = {**base, **overrides}
    return PricingProfile(id=ident, label={'deepseek-flash':'DeepSeek Flash','deepseek-v4-pro':'DeepSeek V4 Pro'}.get(ident, '当前主模型（自定义）'),
        active=ident in active, version=row['version'] if row else 0, default_version=DEFAULT_VERSION,
        source='https://api-docs.deepseek.com/zh-cn/quick_start/pricing/' if ident.startswith('deepseek-') else None,
        calendar_source=CALENDAR_SOURCE if ident.startswith('deepseek-') else None,
        defaults=base, overrides=overrides, effective=effective,
        field_sources={key:origin if key in overrides else 'default' if val is not None else 'unknown' for key,val in base.items()})


def profiles(db):
    return [get_profile(db, ident) for ident in dict.fromkeys([profile_id(), 'deepseek-flash','deepseek-v4-pro'])]


def save_profile(db, ident, update):
    overrides = update.overrides.model_dump(mode='json', exclude_none=True)
    with db.engine.connect() as conn:
        conn.exec_driver_sql('BEGIN IMMEDIATE')
        current = get_profile(db, ident, conn)
        if current.version != update.version:
            raise ValueError('计价配置已变化，请重新加载后再保存')
        effective = {**current.defaults, **overrides}
        if current.defaults['currency'] and effective['currency'] != current.defaults['currency']:
            required = RATE_KEYS if effective['peak_enabled'] else RATE_KEYS[:3]
            if any(key not in overrides for key in required):
                raise ValueError('更换币种时必须填写全部生效单价；系统不自动换汇')
        revision = {'version': current.version + 1, 'overrides': overrides, 'saved_at': now(), 'default_version': DEFAULT_VERSION}
        if current.version:
            db.update('pricing:' + ident, revision, conn=conn)
        else:
            db.insert('setting', revision, id='pricing:' + ident, conn=conn)
        db.insert('pricing_revision', {**revision, 'profile_id':ident, 'effective':effective}, scope=ident, conn=conn)
        conn.commit()
    return get_profile(db, ident)


def snapshot(db):
    return {**get_profile(db, profile_id()).model_dump(mode='json'), 'captured_at':now(), 'channel':'model'}


def period(at, config):
    if not config['peak_enabled']:
        return 'offpeak'
    local = datetime.fromisoformat(at).astimezone(ZoneInfo(config['timezone']))
    clock = local.strftime('%H:%M')
    if local.weekday() not in config['peak_weekdays'] or not any(start <= clock < end for start,end in (w.split('-') for w in config['peak_windows'])):
        return 'offpeak'
    if local.date().isoformat() in config['holiday_dates']:
        return 'offpeak'
    return 'peak' if local.year in config['calendar_years'] else 'unknown'


def normalize_usage(raw):
    raw = raw if isinstance(raw, dict) else {}
    def token(key, source=raw):
        value = source.get(key)
        return value if type(value) is int and value >= 0 else None
    inp = token('prompt_tokens')
    out = token('completion_tokens')
    hit, miss = token('prompt_cache_hit_tokens'), token('prompt_cache_miss_tokens')
    details = raw.get('prompt_tokens_details')
    alias = token('cached_tokens', details) if isinstance(details, dict) else None
    inconsistent = hit is not None and alias is not None and hit != alias
    hit = hit if hit is not None else alias
    if inp is not None and hit is None and miss is not None and miss <= inp:
        hit = inp - miss
    if inp is not None and hit is not None:
        inconsistent |= hit > inp or (miss is not None and hit + miss != inp)
        miss = inp - hit
    # An explicitly malformed cache field is not proof of zero hits.
    inconsistent |= any(key in raw and token(key) is None for key in ('prompt_cache_hit_tokens','prompt_cache_miss_tokens'))
    if isinstance(details, dict) and 'cached_tokens' in details and alias is None:
        inconsistent = True
    if inconsistent or inp is None:
        hit = miss = None
    return {'input_tokens':inp, 'output_tokens':out, 'cached_tokens':hit, 'uncached_tokens':miss,
            'cache_hit_rate':hit / inp if inp and hit is not None else None,
            'cache_status':'inconsistent' if inconsistent else 'reported' if hit is not None else 'unknown'}


def estimate(price_snapshot, raw_usage, ended_at=None):
    config = price_snapshot['effective']
    usage = normalize_usage(raw_usage)
    selected = period(price_snapshot['captured_at'], config)
    periods = {'peak','offpeak'} if selected == 'unknown' else {selected}
    # A boundary inside the request gives a range; never prorate by duration.
    if ended_at and config['peak_enabled']:
        start = datetime.fromisoformat(price_snapshot['captured_at'])
        end = datetime.fromisoformat(ended_at)
        cursor = start
        if (end-start).total_seconds() > 86400:
            periods = {'peak','offpeak'}
        else:
            while cursor <= end:
                p = period(cursor.isoformat(), config)
                periods.update({'peak','offpeak'} if p == 'unknown' else {p})
                cursor = cursor.replace(second=0, microsecond=0) + timedelta(minutes=1)
            p = period(ended_at, config)
            periods.update({'peak','offpeak'} if p == 'unknown' else {p})
    result = {'status':'unknown','amount':None,'minimum':None,'maximum':None,
              'currency':config['currency'],'period':selected,'usage':usage,'snapshot':price_snapshot,
              'reason':'缺少价格、币种或 usage；费用不是供应商账单'}
    inp, out, hit = usage['input_tokens'], usage['output_tokens'], usage['cached_tokens']
    if inp is None or not config['currency']:
        return result
    totals = []
    for p in sorted(periods):
        prefix = 'peak_' if p == 'peak' else ''
        rates = [config[prefix+k] for k in ('input','cached_input','output')]
        if rates[2] is None or (out is None and Decimal(str(rates[2])) != 0):
            return result
        for cached in ([hit] if hit is not None else [0,inp]):
            units = [inp-cached,cached,out or 0]
            if any(n and rate is None for n,rate in zip(units,rates)):
                return result
            totals.append(sum(Decimal(n)*Decimal(str(rate or 0)) for n,rate in zip(units,rates)) / Decimal(1000000))
    low, high = min(totals), max(totals)
    result.update(status='estimated' if low == high else 'range',amount=str(low) if low == high else None,
                  minimum=str(low),maximum=str(high),reason='按调用时价格快照估算；时段跨界或缓存字段缺失时显示区间，最终以供应商账单为准')
    return result


def summarize(calls):
    totals, unknown, ranged, cached, inputs = {}, 0, 0, 0, 0
    for row in calls:
        if row.get('status') == 'skipped':
            continue
        billing = row['body'].get('billing') or {}
        if billing.get('amount') is not None and billing.get('currency'):
            currency = billing['currency']
            totals[currency] = totals.get(currency, Decimal(0)) + Decimal(billing['amount'])
        elif billing.get('status') == 'range':
            ranged += 1
        else:
            unknown += 1
        usage = billing.get('usage') or {}
        if usage.get('cached_tokens') is not None and usage.get('input_tokens') is not None:
            cached += usage['cached_tokens']
            inputs += usage['input_tokens']
    return {'known_subtotals':{key:str(value) for key,value in totals.items()},'unknown_calls':unknown,
            'range_calls':ranged,'cache_hit_rate':cached/inputs if inputs else None,'cache_observed_input_tokens':inputs}
