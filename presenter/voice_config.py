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
    'qwen-tts': {'name': '千问 TTS', 'kinds': ['tts'], 'engine': 'livekit', 'url': 'https://dashscope.aliyuncs.com', 'models': {'tts': 'qwen3-tts-flash'}},
    'qwen': {'name': '千问', 'kinds': ['asr'], 'engine': 'native', 'url': 'https://dashscope.aliyuncs.com/compatible-mode/v1', 'models': {'asr': 'qwen3-asr-flash'}},
    'minimax': {'name': 'MiniMax · LiveKit', 'kinds': ['tts'], 'engine': 'livekit', 'url': '', 'models': {'tts': 'speech-2.6-hd'}},
    'openai': {'name': 'OpenAI · LiveKit', 'kinds': ['asr', 'tts', 'llm'], 'engine': 'livekit', 'url': 'https://api.openai.com/v1', 'models': {'asr': 'whisper-1', 'tts': 'tts-1', 'llm': ''}},
    'elevenlabs': {'name': 'ElevenLabs · LiveKit', 'kinds': ['asr', 'tts'], 'engine': 'livekit', 'url': '', 'models': {'asr': 'scribe_v2_realtime', 'tts': 'eleven_flash_v2_5'}},
    'compatible': {'name': 'OpenAI 兼容 / 自部署', 'kinds': ['asr', 'tts', 'llm'], 'engine': 'livekit', 'url': '', 'models': {'asr': '', 'tts': '', 'llm': ''}},
    'minimax-native': {'name': 'MiniMax · 原有连接', 'kinds': ['tts'], 'engine': 'native', 'url': '', 'models': {'tts': 'speech-2.6-hd'}},
}


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
            db.execute('CREATE TABLE IF NOT EXISTS configuration (id INTEGER PRIMARY KEY, data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS secrets (id TEXT PRIMARY KEY, value BLOB NOT NULL)')
            db.execute('INSERT OR IGNORE INTO configuration VALUES (1, ?)', (json.dumps(self.defaults()),))

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
                baseUrl=self.settings.get(prefix+'_BASE_URL', CATALOG[provider]['url']), secretRef='env:'+prefix+'_API_KEY'))
            profiles.append(dict(id=cid, name='原有 ' + kind.upper(), kind=kind, connectionId=cid,
                model=model, voice=voice, language='zh', mode='stream' if kind=='asr' and self.settings.get('STREAMING_ASR')!='off' else 'batch'))
            if kind=='asr' and profiles[-1]['mode']=='stream':
                profiles[-1]['model']=self.settings.get('STREAMING_ASR_MODEL','qwen-audio-3.0-asr-flash-streaming')
        return dict(revision=0, connections=connections, profiles=profiles,
                    active={kind:'default-'+kind for kind in ('asr','tts','llm')})

    def get(self):
        with self.connect() as db:
            return json.loads(db.execute('SELECT data FROM configuration WHERE id=1').fetchone()[0])

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
                key = c.get('apiKey')
                if key is not None and (not isinstance(key,str) or len(key)>8192):
                    raise ValueError('凭据格式不正确')
                # Changing a destination must not silently forward a previous secret.
                changed = bool(old) and (old['provider']!=c['provider'] or old['baseUrl']!=url)
                if changed and key is None:
                    raise ValueError('修改厂商或地址后，请重新填写凭据（本地免鉴权可留空）')
                ref = old.get('secretRef','')
                if key is not None:
                    ref = uuid.uuid4().hex
                    db.execute('INSERT INTO secrets VALUES (?,?)',(ref,self.cipher.encrypt(key.encode())))
                state['connections'].append(dict(id=cid,name=name,provider=c['provider'],baseUrl=url,secretRef=ref))
            pids = set()
            for p in profiles:
                if not isinstance(p,dict): raise ValueError('模型配置不正确')
                pid, kind=p.get('id'),p.get('kind')
                conn=next((c for c in state['connections'] if c['id']==p.get('connectionId')),None)
                if not isinstance(pid,str) or not 1<=len(pid)<=80 or pid in pids or not conn or kind not in CATALOG[conn['provider']]['kinds']:
                    raise ValueError('模型与连接类型不匹配')
                pids.add(pid)
                clean={k:p.get(k,'') for k in ('id','name','kind','connectionId','model','voice','language','mode')}
                if any(not isinstance(v,str) or len(v)>300 for v in clean.values()) or not clean['name'] or not clean['model']:
                    raise ValueError('请填写模型配置名称和模型 ID')
                if kind=='tts' and not clean['voice']: raise ValueError('请填写音色 ID')
                if kind=='asr' and clean['mode'] not in ('batch','stream'): raise ValueError('请选择识别方式')
                if kind=='asr' and conn['provider'] in ('openai','compatible') and clean['mode']=='stream':
                    raise ValueError('当前 OpenAI Audio 适配器使用整句识别；实时接口需单独适配')
                if kind=='asr' and conn['provider']=='elevenlabs' and clean['mode']!='stream':
                    raise ValueError('当前 ElevenLabs 适配器使用实时识别')
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
            result[kind]={**copy.deepcopy(p),'provider':c['provider'],'baseUrl':c['baseUrl'],'apiKey':self.secret(c['secretRef'])}
        return result
