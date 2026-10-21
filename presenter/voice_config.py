"""Local connection secrets, independent profiles and revisioned selections."""
import copy
import json
import os
import sqlite3
import uuid
from pathlib import Path
from urllib.parse import urlparse
from cryptography.fernet import Fernet


CATALOG = {
    'chat-audio': {'name': 'OpenAI Chat Audio / 兼容 ASR', 'kinds': ['asr'], 'engine': 'native', 'url': 'https://api.openai.com/v1', 'models': {'asr': ''}},
    'qwen-tts': {'name': '千问 TTS', 'kinds': ['tts'], 'engine': 'livekit', 'url': 'https://dashscope.aliyuncs.com', 'models': {'tts': 'qwen3-tts-flash'}},
    'qwen': {'name': '千问', 'kinds': ['asr'], 'engine': 'native', 'url': 'https://dashscope.aliyuncs.com/compatible-mode/v1', 'models': {'asr': 'qwen3-asr-flash'}},
    'minimax': {'name': 'MiniMax · LiveKit', 'kinds': ['tts'], 'engine': 'livekit', 'url': '', 'models': {'tts': 'speech-2.6-hd'}},
    'openai': {'name': 'OpenAI · LiveKit', 'kinds': ['asr', 'tts', 'llm'], 'engine': 'livekit', 'url': 'https://api.openai.com/v1', 'models': {'asr': 'whisper-1', 'tts': 'tts-1', 'llm': ''}},
    'elevenlabs': {'name': 'ElevenLabs · LiveKit', 'kinds': ['asr', 'tts'], 'engine': 'livekit', 'url': '', 'models': {'asr': 'scribe_v2_realtime', 'tts': 'eleven_flash_v2_5'}},
    'compatible': {'name': 'OpenAI 兼容 / 自部署', 'kinds': ['asr', 'tts', 'llm'], 'engine': 'livekit', 'url': '', 'models': {'asr': '', 'tts': '', 'llm': ''}},
    'minimax-native': {'name': 'MiniMax · 原有连接', 'kinds': ['tts'], 'engine': 'native', 'url': '', 'models': {'tts': 'speech-2.6-hd'}},
}


def qwen_stream_url(base_url):
    # Known vendor defaults only. Never infer a WS endpoint on a custom HTTP host.
    host = urlparse(base_url).hostname
    if host in ('dashscope.aliyuncs.com', 'dashscope-intl.aliyuncs.com', 'dashscope-us.aliyuncs.com'):
        return 'wss://' + host + '/api-ws/v1/inference'
    return ''


class VoiceConfig:
    def __init__(self, directory, settings):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.settings = settings
        keyfile = self.directory / 'secrets.key'
        try:
            fd = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, 'wb') as f:
                f.write(Fernet.generate_key())
        self.cipher = Fernet(keyfile.read_bytes())
        self.path = self.directory / 'profiles.sqlite3'
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS configuration (id INTEGER PRIMARY KEY, data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS secrets (id TEXT PRIMARY KEY, value BLOB NOT NULL)')
            db.execute('INSERT OR IGNORE INTO configuration VALUES (1, ?)', (json.dumps(self.defaults()),))
            state = json.loads(db.execute('SELECT data FROM configuration WHERE id=1').fetchone()[0])
            upgraded = self.upgrade(copy.deepcopy(state))
            if upgraded != state:
                upgraded['revision'] += 1
                db.execute('UPDATE configuration SET data=? WHERE id=1', (json.dumps(upgraded),))

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def defaults(self):
        connections, profiles = [], []
        for provider, kind, prefix, model, voice in [
            ('qwen', 'asr', 'OMNI', self.settings.get('OMNI_MODEL', 'qwen3-asr-flash'), ''),
            ('minimax-native', 'tts', 'MINIMAX', self.settings.get('MINIMAX_MODEL', 'speech-2.6-hd'), self.settings.get('MINIMAX_VOICE_ID', 'male-qn-qingse')),
            ('compatible', 'llm', 'OPENAI', self.settings.get('OPENAI_MODEL', 'qwen-flash'), ''),
        ]:
            cid = 'default-' + kind
            connections.append(dict(id=cid, name='原有 ' + kind.upper(), provider=provider,
                baseUrl=self.settings.get(prefix+'_BASE_URL', 'https://dashscope.aliyuncs.com/compatible-mode/v1' if kind=='llm' else CATALOG[provider]['url']), secretRef='env:'+prefix+'_API_KEY',streamUrl='',streamSecretRef=''))
            if kind == 'asr':
                connections[-1]['streamUrl'] = self.settings.get('STREAMING_ASR_URL', qwen_stream_url(connections[-1]['baseUrl']))
                if self.settings.get('STREAMING_ASR_API_KEY'):
                    connections[-1]['streamSecretRef'] = 'env:STREAMING_ASR_API_KEY'
            profiles.append(dict(id=cid, name='原有 ' + kind.upper(), kind=kind, connectionId=cid,
                model=model, voice=voice, language='zh', mode='stream' if kind=='asr' and self.settings.get('STREAMING_ASR')!='off' else 'batch',fallbackModel='',fallbackBaseUrl=''))
            if kind=='asr' and profiles[-1]['mode']=='stream':
                profiles[-1]['model']=self.settings.get('STREAMING_ASR_MODEL','qwen-audio-3.0-asr-flash-streaming')
                profiles[-1]['fallbackModel'] = self.settings.get('OMNI_MODEL', 'qwen3-asr-flash')
                profiles[-1]['fallbackBaseUrl'] = connections[-1]['baseUrl']
        return dict(revision=0, connections=connections, profiles=profiles,
                    active={kind:'default-'+kind for kind in ('asr','tts','llm')})

    def get(self):
        with self.connect() as db:
            return json.loads(db.execute('SELECT data FROM configuration WHERE id=1').fetchone()[0])

    def upgrade(self, state):
        for c in state['connections']:
            if 'streamUrl' not in c:
                c['streamUrl'] = (self.settings.get('STREAMING_ASR_URL') if c['id']=='default-asr' else '') or (qwen_stream_url(c['baseUrl']) if c['provider']=='qwen' else '')
            c.setdefault('streamSecretRef', 'env:STREAMING_ASR_API_KEY' if c['id']=='default-asr' and c.get('secretRef')=='env:OMNI_API_KEY' and self.settings.get('STREAMING_ASR_API_KEY') else '')
        for p in state['profiles']:
            c = next(c for c in state['connections'] if c['id']==p['connectionId'])
            legacy = p['kind']=='asr' and c['provider']=='qwen' and p['mode']=='stream' and bool(qwen_stream_url(c['baseUrl']))
            p.setdefault('fallbackModel', self.settings.get('OMNI_MODEL', 'qwen3-asr-flash') if legacy else '')
            p.setdefault('fallbackBaseUrl', c['baseUrl'] if legacy else '')
        return state

    def secret(self, ref):
        if ref.startswith('env:'):
            return self.settings.get(ref[4:])
        with self.connect() as db:
            row = db.execute('SELECT value FROM secrets WHERE id=?', (ref,)).fetchone()
        return self.cipher.decrypt(row[0]).decode() if row else ''

    def public(self):
        state = self.get()
        for c in state['connections']:
            c['hasKey'] = bool(self.secret(c.pop('secretRef', '')))
            c['hasStreamKey'] = bool(self.secret(c.pop('streamSecretRef',''))) or c['hasKey']
        return {**state, 'catalog': CATALOG}

    def save(self, incoming):
        """Complete document update, CAS, preserving keys when omitted."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = json.loads(db.execute('SELECT data FROM configuration WHERE id=1').fetchone()[0])
            if incoming.get('revision') != previous['revision']:
                raise ValueError('配置已被更新，请重新打开设置')
            connections, profiles = incoming.get('connections'), incoming.get('profiles')
            if not isinstance(connections, list) or not isinstance(profiles, list) or len(connections)>50 or len(profiles)>100:
                raise ValueError('配置数量或格式不正确')
            state = dict(revision=previous['revision']+1, connections=[], profiles=[], active=incoming.get('active'))
            ids = set()
            for c in connections:
                if not isinstance(c, dict) or c.get('provider') not in CATALOG:
                    raise ValueError('不支持的供应商')
                cid, name = c.get('id'), c.get('name')
                if not isinstance(cid, str) or not 1<=len(cid)<=80 or cid in ids or not isinstance(name, str) or not 1<=len(name)<=100:
                    raise ValueError('连接名称或 ID 不正确')
                ids.add(cid)
                url = c.get('baseUrl', '')
                if not isinstance(url, str) or len(url)>1000:
                    raise ValueError('服务地址不正确')
                if url:
                    parsed=urlparse(url)
                    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                        raise ValueError('地址只支持 HTTP/HTTPS，不能包含凭据、查询参数或片段')
                old = next((x for x in previous['connections'] if x['id']==cid), {})
                stream_url = c.get('streamUrl', old.get('streamUrl', ''))
                if not isinstance(stream_url, str) or len(stream_url)>1000:
                    raise ValueError('实时识别地址不正确')
                if stream_url:
                    parsed = urlparse(stream_url)
                    if parsed.scheme not in ('ws','wss') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                        raise ValueError('实时识别地址只支持 WS/WSS，不能包含凭据、查询参数或片段')
                key = c.get('apiKey')
                if key is not None and (not isinstance(key,str) or len(key)>8192):
                    raise ValueError('凭据格式不正确')
                # Changing a destination must not silently forward a previous secret.
                old_stream = old.get('streamUrl','')
                changed = bool(old) and (old['provider']!=c['provider'] or old['baseUrl']!=url or old_stream!=stream_url)
                if changed and key is None:
                    raise ValueError('修改厂商或地址后，请重新填写凭据（本地免鉴权可留空）')
                ref = old.get('secretRef','')
                if key is not None:
                    ref = uuid.uuid4().hex
                    db.execute('INSERT INTO secrets VALUES (?,?)',(ref,self.cipher.encrypt(key.encode())))
                state['connections'].append(dict(id=cid,name=name,provider=c['provider'],baseUrl=url,streamUrl=stream_url,secretRef=ref,
                                                streamSecretRef=old.get('streamSecretRef','') if key is None else ''))
            pids = set()
            for p in profiles:
                if not isinstance(p,dict): raise ValueError('模型配置不正确')
                pid, kind=p.get('id'),p.get('kind')
                conn=next((c for c in state['connections'] if c['id']==p.get('connectionId')),None)
                if not isinstance(pid,str) or not 1<=len(pid)<=80 or pid in pids or not conn or kind not in CATALOG[conn['provider']]['kinds']:
                    raise ValueError('模型与连接类型不匹配')
                pids.add(pid)
                clean={k:p.get(k,'') for k in ('id','name','kind','connectionId','model','voice','language','mode','fallbackModel','fallbackBaseUrl')}
                if any(not isinstance(v,str) or len(v)>300 for v in clean.values()) or not clean['name'] or not clean['model']:
                    raise ValueError('请填写模型配置名称和模型 ID')
                if kind=='tts' and not clean['voice']: raise ValueError('请填写音色 ID')
                if kind=='asr' and clean['mode'] not in ('batch','stream'): raise ValueError('请选择识别方式')
                if kind=='asr' and conn['provider'] in ('openai','compatible','chat-audio') and clean['mode']=='stream':
                    raise ValueError('当前 OpenAI Audio 适配器使用整句识别；实时接口需单独适配')
                if kind=='asr' and conn['provider']=='elevenlabs' and clean['mode']!='stream':
                    raise ValueError('当前 ElevenLabs 适配器使用实时识别')
                if kind=='asr' and conn['provider']=='qwen' and clean['mode']=='stream' and clean['fallbackBaseUrl']:
                    parsed = urlparse(clean['fallbackBaseUrl'])
                    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                        raise ValueError('整句恢复地址只支持不含凭据的 HTTP/HTTPS')
                    # Fallback shares the selected credential: never forward it to another host.
                    base = urlparse(conn['baseUrl'] or CATALOG[conn['provider']]['url'])
                    if (parsed.scheme, parsed.netloc) != (base.scheme, base.netloc):
                        raise ValueError('整句恢复地址必须与所选连接同源；更换服务请新建连接')
                if conn['provider']=='qwen' and kind=='asr' and clean['mode']=='stream' and not conn['streamUrl']:
                    raise ValueError('千问实时识别需要显式填写 WS/WSS 地址')
                state['profiles'].append(clean)
            active=state['active']
            if not isinstance(active,dict) or set(active)!=set(('asr','tts','llm')) or any(not any(p['id']==active[k] and p['kind']==k for p in state['profiles']) for k in active):
                raise ValueError('每种能力必须选择一份有效配置')
            db.execute('UPDATE configuration SET data=? WHERE id=1',(json.dumps(state),))
        return self.public()

    def resolve(self, active=None):
        state=self.get()
        selected=state['active'] if active is None else active
        if not isinstance(selected,dict) or set(selected)!=set(('asr','tts','llm')):
            raise ValueError('每种能力必须选择一份有效配置')
        result={}
        for kind in ('asr','tts','llm'):
            p=next((p for p in state['profiles'] if p['id']==selected.get(kind) and p['kind']==kind),None)
            if not p: raise ValueError('模型配置已不存在，请重新选择')
            c=next(c for c in state['connections'] if c['id']==p['connectionId'])
            key = self.secret(c['secretRef'])
            result[kind]={**copy.deepcopy(p),'provider':c['provider'],'baseUrl':c['baseUrl'],'streamUrl':c.get('streamUrl',''),'apiKey':key,
                          'streamApiKey':self.secret(c.get('streamSecretRef','')) or key}
        return result
