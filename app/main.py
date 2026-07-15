from __future__ import annotations
import asyncio
import csv, io, json, re, shutil, uuid
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
UPLOADS, OUTPUTS = ROOT / 'data' / 'uploads', ROOT / 'outputs'
UPLOADS.mkdir(parents=True, exist_ok=True); OUTPUTS.mkdir(parents=True, exist_ok=True)
app = FastAPI(title='OR 账号信息回填')
app.mount('/static', StaticFiles(directory=ROOT / 'app' / 'static'), name='static')
REQUIRED_VALUES = ('account_id', 'ak', 'mk', 'bank_card_tail')
REQUIRED_HEADERS = (*REQUIRED_VALUES, 'register_time', 'register_ip', 'charge_ip', 'remark')

class AppError(Exception): pass

# AdsPower `/api/v1/user/list` is limited to one request per second.
# This process-wide lock protects both the "test connection" and "process" endpoints.
_ADSPOWER_LOCK = asyncio.Lock()
_ADSPOWER_LAST_REQUEST = 0.0
ADSPOWER_MIN_INTERVAL = 1.15
ADSPOWER_RATE_RETRIES = 3

def norm(v: Any) -> str: return str(v or '').strip()
def norm_header(v: Any) -> str: return norm(v).lower()
def utc_beijing(value: str) -> str:
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(ZoneInfo('Asia/Shanghai')).strftime('%y/%m/%d %H:%M:%S')
def tail4(value: Any) -> str:
    digits = ''.join(re.findall(r'\d', norm(value)))
    return digits[-4:].zfill(4) if digits else ''

def read_csv(raw: bytes) -> tuple[list[str], list[dict[str,str]]]:
    for enc in ('utf-8-sig', 'utf-8', 'gb18030'):
        try:
            reader = csv.DictReader(io.StringIO(raw.decode(enc)))
            return reader.fieldnames or [], list(reader)
        except UnicodeDecodeError: continue
    raise AppError('CSV 编码无法识别，请使用 UTF-8 或 GB18030。')
def read_xlsx(raw: bytes) -> tuple[list[str], list[dict[str,str]]]:
    wb = load_workbook(io.BytesIO(raw), data_only=False, read_only=True)
    for ws in wb.worksheets:
        values = ws.iter_rows(values_only=True)
        headers = [norm(x) for x in next(values, [])]
        if 'account_id' in [norm_header(x) for x in headers]:
            rows = [{headers[i]: norm(v) for i, v in enumerate(row) if i < len(headers)} for row in values if any(v is not None for v in row)]
            return headers, rows
    raise AppError('XLSX 中未找到含 account_id 表头的工作表。')
def parse_upload(filename: str, raw: bytes):
    ext = Path(filename).suffix.lower()
    if ext == '.csv': return read_csv(raw)
    if ext == '.xlsx': return read_xlsx(raw)
    raise AppError('仅支持 .csv 或 .xlsx 文件。')
def aliases(headers: list[str]) -> dict[str,str]:
    result = {}
    for h in headers:
        n=norm_header(h)
        if n not in result: result[n]=h
    return result
def validation(headers: list[str], rows: list[dict[str,str]]) -> dict:
    cols=aliases(headers); missing_headers=[x for x in REQUIRED_HEADERS if x not in cols]
    empty={}
    if not missing_headers:
        for required in REQUIRED_VALUES:
            column=cols[required]; bad=[str(i+2) for i,r in enumerate(rows) if not norm(r.get(column))]
            if bad: empty[required]=bad
    ok=bool(rows) and not missing_headers and not empty
    return {'valid':ok,'row_count':len(rows),'headers':headers,'missing_headers':missing_headers,'empty_required_values':empty,'message':'文件字段与实际内容校验成功，可执行回填。' if ok else '文件校验未通过：' + ('缺少表头：'+ '、'.join(missing_headers)+'。' if missing_headers else '') + ('必填字段为空：'+ '；'.join(f'{k} 第{",".join(v)}行' for k,v in empty.items())+'。' if empty else '') + ('文件没有数据行。' if not rows else '')}

async def adspower_request(client: httpx.AsyncClient, url: str, params: dict, api_key: str) -> dict:
    """Serialize requests, wait 1.15s, and retry AdsPower's transient rate-limit reply."""
    global _ADSPOWER_LAST_REQUEST
    async with _ADSPOWER_LOCK:
        for attempt in range(ADSPOWER_RATE_RETRIES):
            delay = ADSPOWER_MIN_INTERVAL - (time.monotonic() - _ADSPOWER_LAST_REQUEST)
            if delay > 0:
                await asyncio.sleep(delay)
            resp = await client.get(url, params=params, headers={'Authorization': f'Bearer {api_key}'})
            _ADSPOWER_LAST_REQUEST = time.monotonic()
            if resp.status_code != 200:
                raise AppError(f'AdsPower 连接失败（HTTP {resp.status_code}）。请检查连接地址和 API Key。')
            try:
                body = resp.json()
            except ValueError as e:
                raise AppError('AdsPower 返回了无法识别的数据。') from e
            message = norm(body.get('msg') or body.get('message'))
            if body.get('code') in (0, None):
                return body
            if 'Too many request per second' in message and attempt < ADSPOWER_RATE_RETRIES - 1:
                await asyncio.sleep(ADSPOWER_MIN_INTERVAL * (attempt + 1))
                continue
            raise AppError('AdsPower 返回错误：' + message)
    raise AppError('AdsPower 请求频率受限，请稍后重试。')

async def adspower_profiles(base_url: str, api_key: str) -> list[dict]:
    base=base_url.rstrip('/')
    if not base.startswith(('http://','https://')): raise AppError('AdsPower 连接地址必须以 http:// 或 https:// 开头。')
    profiles=[]
    async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
        page=1
        while True:
            body = await adspower_request(client, f'{base}/api/v1/user/list', {'page':page,'page_size':100}, api_key)
            data=body.get('data') or {}; batch=data.get('list') or []
            if not isinstance(batch,list): raise AppError('AdsPower 返回格式异常。')
            profiles.extend(batch)
            if len(batch)<100: break
            page+=1
    return profiles

def ip_index(profiles: list[dict]) -> dict[str, list[str]]:
    result={}
    for p in profiles:
        username=norm(p.get('username')).lower()
        host=norm((p.get('user_proxy_config') or {}).get('proxy_host')) or norm(p.get('ip'))
        if username and host: result.setdefault(username,[]).append(host)
    return {k:list(dict.fromkeys(v)) for k,v in result.items()}
async def key_metadata(mk: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=35, trust_env=False) as c:
        r=await c.get('https://openrouter.ai/api/v1/keys',headers={'Authorization':f'Bearer {mk}'})
        if r.status_code != 200: raise AppError(f'OpenRouter MK 查询失败（HTTP {r.status_code}）。')
        d=r.json(); data=d.get('data',d.get('keys',d)) if isinstance(d,dict) else d
        return data if isinstance(data,list) else []
async def balance(key: str) -> float:
    async with httpx.AsyncClient(timeout=35, trust_env=False) as c:
        r=await c.get('https://openrouter.ai/api/v1/credits',headers={'Authorization':f'Bearer {key}'})
        if r.status_code != 200: raise AppError(f'OpenRouter 余额查询失败（HTTP {r.status_code}）。')
        d=r.json().get('data',r.json()); remaining=d.get('total_credits',0)-d.get('total_usage',0)
        return float(remaining)
def match(keys: list[dict], ak: str) -> dict|None:
    found=[]
    for k in keys:
        vals=[norm(k.get(x)) for x in ('key','label','hash','name')]
        if any(v==ak or (len(v)>=8 and (ak.startswith(v) or ak.endswith(v) or v.startswith(ak) or v.endswith(ak))) for v in vals): found.append(k)
    unique={json.dumps(x,sort_keys=True):x for x in found}
    if len(unique)==1: return next(iter(unique.values()))
    if not found and len(keys)==1: return keys[0]
    return None

@app.get('/')
async def root(): return FileResponse(ROOT/'app/static/index.html')
@app.post('/api/adspower/validate')
async def validate_adspower(base_url: str=Form(...), api_key: str=Form(...)):
    try:
        profiles=await adspower_profiles(base_url,api_key)
        return {'ok':True,'message':f'AdsPower 连接正常，已读取 {len(profiles)} 个浏览器配置。'}
    except (AppError,httpx.HTTPError) as e: return {'ok':False,'message':str(e)}
@app.post('/api/file/validate')
async def validate_file(file: UploadFile=File(...)):
    try:
        raw=await file.read(); headers,rows=parse_upload(file.filename or '',raw); result=validation(headers,rows)
        token=uuid.uuid4().hex
        (UPLOADS/f'{token}.bin').write_bytes(raw); (UPLOADS/f'{token}.json').write_text(json.dumps({'filename':file.filename},ensure_ascii=False))
        result['token']=token
        return result
    except AppError as e: return {'valid':False,'message':str(e),'missing_headers':[],'empty_required_values':{}}
@app.post('/api/process')
async def process(token: str=Form(...), base_url: str=Form(...), api_key: str=Form(...)):
    raw_path=UPLOADS/f'{token}.bin'; meta_path=UPLOADS/f'{token}.json'
    if not raw_path.exists() or not meta_path.exists(): raise HTTPException(404,'上传文件已失效，请重新上传。')
    filename=json.loads(meta_path.read_text())['filename']; headers,rows=parse_upload(filename,raw_path.read_bytes()); check=validation(headers,rows)
    if not check['valid']: raise HTTPException(422,check['message'])
    cols=aliases(headers); profiles=await adspower_profiles(base_url,api_key); ips=ip_index(profiles)
    report={'total_rows':len(rows),'time_filled':0,'ip_filled':0,'card_normalized':0,'errors':[],'low_balance':[]}
    for line,row in enumerate(rows,start=2):
        account=norm(row[cols['account_id']]).lower(); ak=norm(row[cols['ak']]); mk=norm(row[cols['mk']])
        try:
            keys=await key_metadata(mk); found=match(keys,ak)
            if not found or not found.get('created_at'): raise AppError('未匹配到唯一 API Key created_at')
            row[cols['register_time']]=utc_beijing(norm(found['created_at'])); report['time_filled']+=1
            ipvals=ips.get(account,[])
            if len(ipvals)!=1: raise AppError('AdsPower IP 缺失或重复')
            row[cols['register_ip']]=ipvals[0]; report['ip_filled']+=1
            row[cols['bank_card_tail']]=tail4(row[cols['bank_card_tail']]); report['card_normalized']+=1
            try: remaining=await balance(mk)
            except AppError: remaining=await balance(ak)
            if remaining < 1: report['low_balance'].append({'row':line,'account_id':account,'balance':remaining})
        except AppError as e: report['errors'].append({'row':line,'account_id':account,'error':str(e)})
    if report['errors'] or report['low_balance']:
        raise HTTPException(422,{'message':'任务未完成：存在回填异常或余额低于 1 的账号。','report':report})
    out=OUTPUTS/f'{Path(filename).stem}-已回填注册时间IP卡尾.csv'
    with out.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=headers); w.writeheader(); w.writerows(rows)
    # proof of reopen
    with out.open(encoding='utf-8-sig',newline='') as f: saved=list(csv.DictReader(f))
    if len(saved)!=len(rows): raise HTTPException(500,'输出文件重新读取验证失败。')
    return {'ok':True,'message':'全部账号回填完成，且余额均不低于 1。','report':report,'download_url':'/api/download/'+out.name}
@app.get('/api/download/{name}')
async def download(name: str):
    p=OUTPUTS/Path(name).name
    if not p.exists(): raise HTTPException(404,'文件不存在')
    return FileResponse(p,media_type='text/csv',filename=p.name)
