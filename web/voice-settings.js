export function createVoiceSettings({stopMic,onDiagnostics=()=>{}}) {
  const $=id=>document.getElementById(id);
  let state=null,saved=null,dirty=false,connectionId='',testing=false;
  const section=document.createElement('section');section.id='voiceSettings';section.className='settings-content';section.hidden=true;section.setAttribute('role','tabpanel');section.setAttribute('aria-labelledby','voiceTab');
  section.innerHTML=`<p class="subtle">识别、合成和独立问答分别选择。厂商凭据可以复用；Codex / Claude 的来源仍在“模型与会话”设置。</p><p id="voiceConfigStatus" role="status"></p><div id="voiceProfiles"></div><details id="voiceConnections"><summary>厂商连接与凭据</summary><label for="voiceConnection">选择连接</label><select id="voiceConnection"></select><button type="button" id="voiceConnectionNew">添加连接</button><label for="voiceConnectionName">连接名称</label><input id="voiceConnectionName" maxlength="100"><label for="voiceProvider">供应商 / 协议</label><select id="voiceProvider"></select><label for="voiceBaseUrl">服务地址（兼容服务需填写，其他可使用默认）</label><input id="voiceBaseUrl" placeholder="https://… 或 http://127.0.0.1:…"><div id="voiceStreamFields"><label for="voiceStreamUrl">实时识别地址（完整 WS/WSS 地址，不从 HTTP 地址推导）</label><input id="voiceStreamUrl" placeholder="wss://…/api-ws/v1/inference"></div><label for="voiceApiKey">API Key</label><input id="voiceApiKey" type="password" autocomplete="new-password" placeholder="留空保留已有密钥"><p class="subtle">密钥只保存在本机后端，不写入聊天记录或导出配置。</p></details>`;
  $('settingsDialog').insertBefore(section,document.querySelector('.dialog-footer'));
  const tab=document.createElement('button');tab.id='voiceTab';tab.textContent='语音模型';tab.setAttribute('role','tab');tab.setAttribute('aria-selected','false');tab.setAttribute('aria-controls','voiceSettings');tab.tabIndex=-1;document.querySelector('.settings-tabs').append(tab);
  function status(text){$('voiceConfigStatus').textContent=text;}
  async function request(path,options={}){const r=await fetch(path,{...options,headers:{'Content-Type':'application/json',...options.headers}});const d=await r.json();if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:'配置请求失败');return d;}
  function option(select,value,label){const el=document.createElement('option');el.value=value;el.textContent=label;select.append(el);}
  function field(parent,label,id,value,choices){const l=document.createElement('label');l.htmlFor=id;l.textContent=label;const input=document.createElement(choices?'select':'input');input.id=id;if(choices)for(const [v,n]of choices)option(input,v,n);input.value=value||'';parent.append(l,input);return input;}
  function renderConnections(){
    const select=$('voiceConnection');select.replaceChildren();for(const c of state.connections)option(select,c.id,c.name);connectionId=state.connections.some(c=>c.id===connectionId)?connectionId:state.connections[0]?.id;select.value=connectionId;
    const c=state.connections.find(c=>c.id===connectionId);if(!c)return;
    const providers=$('voiceProvider');providers.replaceChildren();for(const [id,p]of Object.entries(state.catalog))option(providers,id,p.name);
    providers.value=c.provider;$('voiceConnectionName').value=c.name;$('voiceBaseUrl').value=c.baseUrl;$('voiceStreamUrl').value=c.streamUrl||'';$('voiceStreamFields').hidden=c.provider!=='qwen';$('voiceApiKey').value=c.apiKey||'';$('voiceApiKey').placeholder=c.hasKey?'已保存 · 留空保留':'填写凭据；本地免鉴权可留空';
  }
  function renderProfiles(){
    $('voiceProfiles').replaceChildren();
    for(const [kind,title]of [['asr','语音识别 ASR'],['tts','语音合成 TTS'],['llm','独立问答 / 分级 Flash']]){
      const card=document.createElement('fieldset');card.className='voice-profile';const legend=document.createElement('legend');legend.textContent=title;card.append(legend);
      const list=state.profiles.filter(p=>p.kind===kind);const selected=state.profiles.find(p=>p.id===state.active[kind]);
      const choose=field(card,'当前配置','voiceSelect-'+kind,state.active[kind],list.map(p=>[p.id,p.name]));
      choose.onchange=()=>{state.active[kind]=choose.value;dirty=true;renderProfiles();};
      const add=document.createElement('button');add.type='button';add.textContent='另存为新配置';add.onclick=()=>{const p={...selected,id:crypto.randomUUID(),name:selected.name+' 副本'};state.profiles.push(p);state.active[kind]=p.id;dirty=true;renderProfiles();};card.append(add);
      const connections=state.connections.filter(c=>state.catalog[c.provider].kinds.includes(kind));
      const inputs={name:field(card,'配置名称','voiceName-'+kind,selected.name),connectionId:field(card,'厂商连接','voiceConn-'+kind,selected.connectionId,connections.map(c=>[c.id,c.name])),model:field(card,'模型 ID','voiceModel-'+kind,selected.model)};
      if(kind==='asr'){inputs.language=field(card,'语言','voiceLanguage-asr',selected.language);inputs.mode=field(card,'识别方式','voiceMode-asr',selected.mode,[['batch','整句识别'],['stream','实时识别']]);}
      if(kind==='asr'&&state.connections.find(c=>c.id===selected.connectionId)?.provider==='qwen'&&selected.mode==='stream'){inputs.fallbackModel=field(card,'整句恢复模型（可选，留空禁用）','voiceFallbackModel-asr',selected.fallbackModel);inputs.fallbackBaseUrl=field(card,'整句恢复 HTTP 地址（与连接同源）','voiceFallbackBaseUrl-asr',selected.fallbackBaseUrl);}
      if(kind==='tts')inputs.voice=field(card,'音色 ID','voiceVoice-tts',selected.voice);
      for(const [key,input]of Object.entries(inputs))input.onchange=()=>{selected[key]=input.value;dirty=true;if(key==='mode'){renderProfiles();}if(key==='connectionId'){const c=state.connections.find(c=>c.id===input.value);selected.model=state.catalog[c.provider].models[kind]||'';selected.fallbackModel='';selected.fallbackBaseUrl='';if(kind==='asr')selected.mode=c.provider==='elevenlabs'?'stream':'batch';renderProfiles();}};
      const hint=document.createElement('p');hint.className='subtle';hint.textContent=kind==='asr'?'整句模式：讲话结束后提交整段音频；实时模式：讲话时发送音频。OpenAI Audio 使用 /audio/transcriptions；Chat Audio 使用 /chat/completions；千问实时需单独填写 WS 地址，恢复模型可选。':kind==='tts'?'使用该厂商支持的音色 ID。LiveKit 输出转换为播放器所需音频格式。':'仅作用于独立问答和分级模式的 Flash，不替换 Codex / Claude。';card.append(hint);
      const test=document.createElement('button');test.type='button';test.id='voiceTest-'+kind;test.textContent=kind==='asr'?'录音 5 秒并测试':kind==='tts'?'试听当前配置':'测试模型连接';test.onclick=()=>testProfile(kind);card.append(test);$('voiceProfiles').append(card);
    }
  }
  $('voiceConnection').onchange=()=>{connectionId=$('voiceConnection').value;renderConnections();};
  $('voiceConnectionNew').onclick=()=>{const c={id:crypto.randomUUID(),name:'新连接',provider:'openai',baseUrl:state.catalog.openai.url,apiKey:''};state.connections.push(c);connectionId=c.id;dirty=true;renderConnections();renderProfiles();};
  for(const [id,key]of [['voiceConnectionName','name'],['voiceProvider','provider'],['voiceBaseUrl','baseUrl'],['voiceStreamUrl','streamUrl'],['voiceApiKey','apiKey']])$(id)[id==='voiceProvider'?'onchange':'oninput']=()=>{const c=state.connections.find(c=>c.id===connectionId);c[key]=$(id).value;dirty=true;if(key==='provider'){c.baseUrl=state.catalog[c.provider].url;c.streamUrl=c.provider==='qwen'?'wss://dashscope.aliyuncs.com/api-ws/v1/inference':'';c.apiKey='';c.hasKey=false;renderConnections();}renderProfiles();};
  async function load(){if(state)return;try{state=await request('/api/voice');saved=structuredClone(state);dirty=false;renderConnections();renderProfiles();status('配置已加载；保存后应用到当前会话。');}catch(e){status(e.message);}}
  async function save(){if(!state||!dirty)return;state=await request('/api/voice',{method:'PUT',body:JSON.stringify(state)});onDiagnostics(state.diagnostics);saved=structuredClone(state);dirty=false;renderConnections();renderProfiles();status('配置已保存');}
  async function record(){
    stopMic();const stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true}});let ctx;
    try{ctx=new AudioContext();await ctx.resume();await ctx.audioWorklet.addModule('/static/capture.js');const source=ctx.createMediaStreamSource(stream),capture=new AudioWorkletNode(ctx,'capture');source.connect(capture);const mute=ctx.createGain();mute.gain.value=0;capture.connect(mute);mute.connect(ctx.destination);const frames=[];capture.port.onmessage=e=>frames.push(new Uint8Array(e.data));await new Promise(r=>setTimeout(r,5000));source.disconnect();capture.disconnect();const length=frames.reduce((n,a)=>n+a.length,0),bytes=new Uint8Array(length);let offset=0;for(const frame of frames){bytes.set(frame,offset);offset+=frame.length;}let binary='';for(const byte of bytes)binary+=String.fromCharCode(byte);return btoa(binary);}finally{stream.getTracks().forEach(t=>t.stop());if(ctx)await ctx.close();}
  }
  async function testProfile(kind){
    if(testing)return;testing=true;document.querySelectorAll('[id^=voiceTest-]').forEach(b=>b.disabled=true);
    try{if(dirty)throw Error('请先点击“保存设置”，再重新打开进行测试。');status(kind==='asr'?'正在录音 5 秒，请讲话…':'正在测试…');const data={kind,active:state.active};if(kind==='asr')data.pcm=await record();const result=await request('/api/voice/test',{method:'POST',body:JSON.stringify(data)});if(result.pcm){const bytes=Uint8Array.from(atob(result.pcm),c=>c.charCodeAt(0));const pcm=new Int16Array(bytes.buffer);const ctx=new AudioContext();await ctx.resume();const buffer=ctx.createBuffer(1,pcm.length,result.rate),floats=buffer.getChannelData(0);for(let i=0;i<pcm.length;i++)floats[i]=pcm[i]/32768;const source=ctx.createBufferSource();source.buffer=buffer;source.connect(ctx.destination);source.onended=()=>ctx.close();source.start();}status('测试成功 · '+result.elapsedMs+' ms'+(result.text?' · '+result.text:''));}catch(e){status(e.message);}finally{testing=false;document.querySelectorAll('[id^=voiceTest-]').forEach(b=>b.disabled=false);}
  }
  return {load,save,discard:()=>{if(saved){state=structuredClone(saved);dirty=false;renderConnections();renderProfiles();}},selection:()=>saved?{...saved.active}:undefined};
}
