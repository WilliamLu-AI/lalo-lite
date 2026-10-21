import {marked} from 'marked';
import DOMPurify from 'dompurify';
import {createVoiceSettings} from './voice-settings.js';

const $=id=>document.getElementById(id);
let ws,epoch=0,session='',configured=false,follow=true,current=null,blocks=[],documentKey='sample';
let imageAssets={};
let audioPreferences={playbackSpeed:'1',microphoneDevice:'',speakerDevice:''};
let context,stream,capture,source,micGain,active=false,playCursor=0,playEnd=false,blocked=false,reconnectTimer;
let lastPong=Date.now(),micGeneration=0,micStarting=false;
let projectDir='',host='',responding=false,selectedText='',settingsDraft=null,settingsApplied=false;
let appliedConfig=null,pendingConfig=null,configSequence=0;
const nodes=new Set();
const voiceSettings=createVoiceSettings({stopMic});
let outputContext,holding=false,answerBubble=null,outputAnalyser,inputLevel=0;
const sentenceCounts=new Map(),sentenceEnds=new Set();
const sample='# 方案讲解助手\n\n边看方案，边与 AI 讨论。在 Codex 或 Claude Code 中运行 present-plan，带入方案和当前会话开始讲解。\n\n## 第一章：目标\n\n帮助读者理解方案的结构、设计理由与取舍。支持提问、插话，以及回到原来的讲解位置。\n\n## 第二章：语音\n\n播放期间麦克风持续工作。检测到用户讲话后停止播报，再识别问题并作答。回声消除由浏览器音频链路承担，需要在实际设备上验收。\n\n## 第三章：阅读器\n\nAI 提供段落编号，阅读器负责滚动、高亮和选区回传。只有播放真正完成后，才执行约定的下一段定位。\n';
function status(text){$('status').textContent=text;}
function renderControls(){
  const engaged=active||micStarting||responding;
  $('start').textContent=micStarting?'取消开启':active?'结束对话':responding?'停止讲解':'开始对话';
  $('start').disabled=!configured&&!engaged;
  $('start').dataset.active=String(engaged);
  $('start').setAttribute('aria-pressed',String(active));
  $('continue').disabled=!configured;$('continue').textContent=responding?'暂停讲解':'继续讲解';
  $('sendQuestion').disabled=!configured||!$('question').value.trim();
  document.querySelectorAll('[data-prompt]').forEach(button=>button.disabled=!configured);
  $('voiceHint').textContent=active?'麦克风已开启，可以直接插话。':micStarting?'请在浏览器提示中允许麦克风访问。':'开启麦克风后，可直接插话提问。';
}
function updateFollow(){
  $('follow').textContent=follow?'自动跟随：开':'恢复跟随';
  $('follow').setAttribute('aria-pressed',String(follow));
  $('resumeFollow').hidden=follow;
}

function send(data){if(ws?.readyState===WebSocket.OPEN)ws.send(JSON.stringify(data));}
function message(role,text){$('chatEmpty')?.remove();const div=document.createElement('div');div.className='message '+role;const label=document.createElement('small');label.textContent=role==='user'?'你':'讲解助手';div.append(label,document.createTextNode(text));$('messages').append(div);while($('messages').children.length>100)$('messages').firstChild.remove();$('messages').scrollTop=$('messages').scrollHeight;return div;}
async function audioContext(){if(!context){context=new AudioContext();}await context.resume();return context;}
function stopPlayback(){holding=false;outputContext?.resume().catch(()=>{});sentenceCounts.clear();sentenceEnds.clear();answerBubble=null;for(const node of nodes){node.onended=null;try{node.stop();}catch{}node.disconnect();}nodes.clear();playCursor=0;playEnd=false;}
function interrupt(){responding=false;renderControls();blocked=true;stopPlayback();send({type:'interrupt'});}
function playbackDone(){if(playEnd&&nodes.size===0&&!blocked){playEnd=false;responding=false;renderControls();send({type:'playback_done',epoch});}}
function sentenceDone(index){if(sentenceEnds.has(index)&&!sentenceCounts.get(index)){sentenceEnds.delete(index);sentenceCounts.delete(index);send({type:'sentence_played',epoch,sentence:index});}}
function updateAnswer(text){if(!answerBubble)answerBubble=message('assistant',text);else {answerBubble.lastChild.textContent=text;$('messages').scrollTop=$('messages').scrollHeight;}}
async function play(data){
  const ticket=epoch,sid=session;if(blocked)return;
  const ctx=outputContext||(outputContext=new AudioContext());if(holding)await ctx.suspend();else await ctx.resume();if(ticket!==epoch||sid!==session||blocked)return;
  const bytes=Uint8Array.from(atob(data.pcm),c=>c.charCodeAt(0));
  const pcm=new Int16Array(bytes.buffer,0,Math.floor(bytes.length/2));if(!pcm.length)return;
  const buffer=ctx.createBuffer(1,pcm.length,data.rate);const floats=buffer.getChannelData(0);for(let i=0;i<pcm.length;i++)floats[i]=pcm[i]/32768;
  if(ctx.setSinkId&&ctx.sinkId!==audioPreferences.speakerDevice)await ctx.setSinkId(audioPreferences.speakerDevice);if(ticket!==epoch||sid!==session||blocked)return;const node=ctx.createBufferSource();node.buffer=buffer;node.playbackRate.value=Number(audioPreferences.playbackSpeed);if(!outputAnalyser){outputAnalyser=ctx.createAnalyser();outputAnalyser.fftSize=256;outputAnalyser.connect(ctx.destination);}node.connect(outputAnalyser);nodes.add(node);
  if(Number.isInteger(data.sentence))sentenceCounts.set(data.sentence,(sentenceCounts.get(data.sentence)||0)+1);
  node.onended=()=>{nodes.delete(node);node.disconnect();if(ticket===epoch&&sid===session){if(Number.isInteger(data.sentence)){sentenceCounts.set(data.sentence,Math.max(0,(sentenceCounts.get(data.sentence)||0)-1));sentenceDone(data.sentence);}playbackDone();}};
  playCursor=Math.max(ctx.currentTime+.04,playCursor);if(nodes.size===1)send({type:'client_event',event:'output_started'});node.start(playCursor);playCursor+=buffer.duration/Number(audioPreferences.playbackSpeed);
}
function view(){send({type:'view',current,follow,selection:selectedText});}
function navigate(id,force=false){if(!id)return false;const el=document.getElementById(id);if(!el||!$('reader').contains(el))return false;const before=$('reader').scrollTop;el.closest('details')?.setAttribute('open','');$('reader').querySelectorAll('.active').forEach(e=>e.classList.remove('active'));el.classList.add('active');if(follow||force)el.scrollIntoView({block:el.offsetHeight>$('reader').clientHeight-120?'start':'center',behavior:'instant'});current=id;const index=blocks.findIndex(b=>b.id===id);const section=blocks.slice(0,index+1).reverse().find(b=>b.heading);$('chapterTitle').textContent=section?.text||'正在阅读';$('headerChapter').textContent=section?.text||'';const headings=blocks.filter(b=>b.heading);$('chapterProgress').textContent=`${Math.max(0,headings.indexOf(section)+1)} / ${headings.length} 章节`;$('outline').querySelectorAll('button').forEach(button=>{if(button.dataset.target===section?.id)button.setAttribute('aria-current','location');else button.removeAttribute('aria-current');});sessionStorage.setItem('position:'+documentKey,id);view();requestAnimationFrame(()=>{const r=el.getBoundingClientRect(),v=$('reader').getBoundingClientRect();send({type:'client_event',event:'navigation_applied',build:'follow-20261017b',blockId:id,follow,force,before,after:$('reader').scrollTop,targetTop:r.top,targetBottom:r.bottom,readerTop:v.top,readerBottom:v.bottom,visible:r.bottom>v.top&&r.top<Math.min(v.bottom,innerHeight-100)});});return true;}
function loadDocument(text,name='示例方案',html=false){
  interrupt();configured=false;selectedText='';$('docTitle').textContent=name;$('outlineDocTitle').textContent=name;let hash=2166136261;for(let i=0;i<text.length;i++)hash=Math.imul(hash^text.charCodeAt(i),16777619);documentKey=name+':'+(hash>>>0);
  const safe=DOMPurify.sanitize(html?text:marked.parse(text),{FORBID_TAGS:['style','form','input','button','iframe','object','embed','audio','video','svg','math'],FORBID_ATTR:['style','srcset','href','id','name']});
  const template=document.createElement('template');template.innerHTML=safe;
  let imageNumber=0;
  template.content.querySelectorAll('[src]').forEach(el=>{if(el.tagName!=='IMG')el.removeAttribute('src');});
  template.content.querySelectorAll('figure').forEach(f=>f.replaceWith(...f.childNodes));
  template.content.querySelectorAll('img').forEach(img=>{
    const original=img.getAttribute('src')||'';const asset=imageAssets[original]||imageAssets[original.split('/').pop()];
    const src=asset||(/^data:image\/(png|jpeg|webp);base64,[a-zA-Z0-9+/=]+$/.test(original)?original:'');
    img.removeAttribute('src');
    const figure=document.createElement('figure');figure.dataset.imageIndex=String(++imageNumber);
    const caption=document.createElement('figcaption');caption.textContent='图 '+imageNumber+(img.alt?' · '+img.alt:'');
    img.replaceWith(figure);figure.append(img,caption);
    if(src&&src.length<220000){img.src=src;img.dataset.loaded='true';}else{img.alt='图片未加载，请在源文档中检查图片后重新运行 Skill';}
  });
  template.content.querySelectorAll('p').forEach(p=>{if(p.querySelector('figure')&&!p.textContent.replace(/图 \d+.*$/,'').trim()){p.replaceWith(...p.childNodes);}});
  template.content.querySelectorAll('p').forEach(p=>{for(const f of p.querySelectorAll('figure'))p.before(f);if(!p.textContent.trim())p.remove();});
  $('reader').replaceChildren(template.content);
  // Strip document metadata and isolate presentation from imported content.
  $('reader').querySelectorAll('meta,link,title,base,script').forEach(e=>e.remove());
  let elements=[...$('reader').querySelectorAll('h1,h2,h3,h4,h5,h6,p,pre,table,ul,ol,blockquote,figure')].filter(e=>!e.parentElement.closest('pre,table,ul,ol,blockquote,figure'));
  if(!elements.length&&$('reader').textContent.trim()){const p=document.createElement('p');p.textContent=$('reader').textContent;$('reader').replaceChildren(p);elements=[p];}
  blocks=elements.map((el,i)=>{el.id='block-'+i;el.dataset.block=el.id;el.tabIndex=0;return{id:el.id,text:el.textContent,heading:/^H[1-6]$/.test(el.tagName),...(el.tagName==='FIGURE'?{imageIndex:Number(el.dataset.imageIndex),image:el.querySelector('img[data-loaded]')?.src||''}:{})};});
  $('outline').replaceChildren();for(const b of blocks.filter(b=>b.heading||b.imageIndex)){const button=document.createElement('button');button.textContent=b.text;button.dataset.target=b.id;button.classList.toggle('image-entry',!!b.imageIndex);button.classList.toggle('subheading',Number(document.getElementById(b.id).tagName.slice(1))>2);button.onclick=()=>{interrupt();navigate(b.id,true);setOutline(false);};$('outline').append(button);}
  current=null;follow=true;updateFollow();navigate(sessionStorage.getItem('position:'+documentKey)||blocks[0]?.id,true);configure();
}
function formConfig(){return {mode:$('mode').value,source:$('source').value.trim(),host,background:$('background').value,codexModel:$('codexModel').value,voiceProfiles:voiceSettings.selection()};}
function syncSettings(){
  $('codexOptions').hidden=!['codex','tiered'].includes($('mode').value);
  $('sourceOptions').hidden=$('mode').value==='cloud';
}
function showBinding(data){
  const mode=data.mode||appliedConfig?.mode;
  const source=data.source??appliedConfig?.source??'';
  const model=data.model||data.codexModel||appliedConfig?.codexModel||'默认模型';
  const deepHost=data.host||host||'codex';
  const name=mode==='tiered'?'分级 · Flash + '+(deepHost==='claude'?'Claude Code':'Codex'):mode==='codex'?'Codex · '+model:mode==='claude'?'Claude Code':'独立问答模型';
  const state=mode==='cloud'?'仅使用文档与补充背景':source?(data.thread||data.session?'上下文已继承':'源会话已绑定 · 等待建立分叉'):'新会话 · 未继承聊天历史';
  $('sourceLabel').textContent=name+' · '+state;
  $('bindingState').textContent=state+(source?' · '+source:'');
  $('sourceLabel').title=source?'源会话：'+source:'从当前聊天运行 present-plan 可继承上下文';
}
function configure(saveDraft=false){
  if(saveDraft||!appliedConfig)appliedConfig=formConfig();
  configured=false;interrupt();
  pendingConfig={...appliedConfig,configId:++configSequence};
  send({type:'configure',blocks,...pendingConfig,cwd:projectDir});
  $('configState').textContent='正在应用…';renderControls();
}
function connect(){
  ws=new WebSocket('ws://'+location.host+'/ws');let received=Promise.resolve();
  ws.onmessage=e=>{received=received.then(async()=>{
    const data=JSON.parse(e.data);lastPong=Date.now();
    if(data.type==='ready'){const tieredOption=$('mode').querySelector('[value="tiered"]');tieredOption.disabled=!data.capabilities?.tiered;if($('mode').value==='tiered'&&tieredOption.disabled){$('mode').value=host==='claude'?'claude':'codex';appliedConfig=null;}await voiceSettings.load();session=data.session;epoch=data.epoch;blocked=true;stopPlayback();$('connection').textContent='已连接';configure();return;}
    if(data.session!==session||data.epoch<epoch)return;
    if(data.epoch>epoch){epoch=data.epoch;stopPlayback();}
    if(data.type==='interrupted'){stopPlayback();blocked=true;responding=false;}
    if(data.type==='turn_started'){blocked=false;playEnd=false;responding=true;}
    if(data.type==='turn_empty'){responding=false;}
    if(data.type==='configured'){if(data.configId!==undefined&&data.configId!==pendingConfig?.configId)return;configured=true;blocked=false;$('configState').textContent=`已加载 ${data.blocks} 个段落`;$('locatorInfo').textContent=data.locator==='flash'?'段落定位：Flash':data.locator==='jev'?'章节定位：Jev':'段落定位：讲解模型';showBinding(data);try{localStorage.setItem('lalo-model-preference',JSON.stringify({mode:data.mode||appliedConfig.mode,model:data.codexModel??appliedConfig.codexModel}));}catch{}status(active?'正在聆听':'准备就绪');if($('settingsDialog').open&&settingsApplied)$('settingsDialog').close();view();}
    if(data.type==='context_ready')showBinding(data);
    if(data.type==='microphone_revoked'){stopMic();stopPlayback();responding=false;status('另一页面已开始对话，本页麦克风已停止');}
    if(data.type==='status')status(data.message);
    if(data.type==='location'){
      const descriptions={selected:'已匹配章节',uncertain:'判断不确定，保持当前位置',timeout:'请求超时，保持当前位置',http_451:'服务限制访问，保持当前位置',not_configured:'未配置密钥，保持当前位置',document_too_large:'文档超出定位范围，保持当前位置',empty_document:'文档为空',invalid_response:'响应无效，保持当前位置',network_error:'网络错误，保持当前位置'};
      $('locatorInfo').textContent='Jev：'+(descriptions[data.status]||'定位不可用，保持当前位置')+(data.elapsedMs!==undefined?`（${data.elapsedMs} ms）`:'');
    }
    if(data.type==='playback_hold'){holding=true;if(outputContext)await outputContext.suspend();}
    if(data.type==='playback_resume'){holding=false;if(outputContext)await outputContext.resume();status('继续讲解');}
    if(data.type==='transcript_partial')status('你：'+data.text);
    if(data.type==='backchannel'&&data.text)message('user',data.text);
    if(data.type==='transcript')message('user',data.text);
    if(data.type==='answer_partial'&&!blocked)updateAnswer(data.text);
    if(data.type==='navigate_current'&&!blocked)navigate(data.blockId,data.force===true);
    if(data.type==='sentence_end'&&!blocked){sentenceEnds.add(data.sentence);sentenceDone(data.sentence);}
    if(data.type==='answer'&&!blocked){navigate(data.blockId,data.force===true);updateAnswer(data.text);}
    if(data.type==='audio')await play(data);
    if(data.type==='audio_end'&&!blocked){playEnd=true;playbackDone();}
    if(data.type==='navigate'&&!blocked){const moved=navigate(data.blockId);if(moved&&follow&&$('auto').checked)await ask('请逐段讲解当前段落，讲完后前进到下一段。');}
    if(data.type==='error'){responding=false;stopPlayback();const stage={asr:'语音识别',model:'模型回答',tts:'语音合成'}[data.stage]||'请求';const detail=stage+'失败：'+data.message;status(detail);message('assistant',detail);}
    renderControls();
  }).catch(err=>{responding=false;renderControls();send({type:'client_event',event:'client_failed'});status('处理失败：'+err.message);});};
  ws.onclose=()=>{configured=false;responding=false;blocked=true;stopPlayback();stopMic();$('connection').textContent='连接断开，正在重连';status('语音已停止。连接恢复后请重新开始语音。');reconnectTimer=setTimeout(connect,1500);};
  ws.onerror=()=>status('连接失败');
}
async function startMic(){
  if(active||micStarting||!configured){if(!configured)status('请先等待配置完成');return;}
  micStarting=true;renderControls();const generation=++micGeneration;
  let pendingStream;
  try{
    const ctx=await audioContext();if(generation!==micGeneration)return;pendingStream=await navigator.mediaDevices.getUserMedia({audio:{...(audioPreferences.microphoneDevice?{deviceId:{exact:audioPreferences.microphoneDevice}}:{}),echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});
    if(generation!==micGeneration){pendingStream.getTracks().forEach(t=>t.stop());return;}
    await ctx.audioWorklet.addModule('/static/capture.js');
    if(generation!==micGeneration){pendingStream.getTracks().forEach(t=>t.stop());return;}
    stream=pendingStream;
    source=ctx.createMediaStreamSource(stream);capture=new AudioWorkletNode(ctx,'capture');micGain=ctx.createGain();micGain.gain.value=0;
    source.connect(capture);capture.connect(micGain);micGain.connect(ctx.destination);
    capture.port.onmessage=e=>{if(!active)return;const pcm=new Int16Array(e.data);let sum=0;for(const x of pcm)sum+=(x/32768)**2;$('level').style.width=Math.min(100,(inputLevel=Math.sqrt(sum/pcm.length))*500)+'%';if(ws.readyState===WebSocket.OPEN){if(ws.bufferedAmount>64000){stopMic();interrupt();status('网络积压，语音已停止，请重新开始');return;}ws.send(e.data);}};
    active=true;refreshDevices();send({type:'client_event',event:'mic_started'});const settings=stream.getAudioTracks()[0].getSettings();$('audioInfo').textContent=`麦克风 ${settings.sampleRate||'?'} Hz · 回声消除 ${settings.echoCancellation?'已启用':'未启用'} · 持续监听`;
    stream.getAudioTracks()[0].onended=()=>{stopMic();interrupt();status('麦克风已断开，请重新选择设备后开始');};
    micStarting=false;renderControls();status('正在聆听');
  }catch(err){pendingStream?.getTracks().forEach(t=>t.stop());if(generation===micGeneration){stopMic();send({type:'client_event',event:'mic_failed'});status('麦克风启动失败：'+err.message);}}finally{if(generation===micGeneration){micStarting=false;renderControls();}}
}
function stopMic(){if(active)send({type:'client_event',event:'mic_stopped'});micGeneration++;micStarting=false;active=false;stream?.getTracks().forEach(t=>{t.onended=null;t.stop();});capture?.disconnect();source?.disconnect();micGain?.disconnect();stream=capture=source=micGain=null;$('level').style.width='0%';$('audioInfo').textContent='麦克风尚未开启';renderControls();}
async function ask(text){if(!configured){status('尚未连接或配置完成');return;}try{await audioContext();interrupt();responding=true;renderControls();status('正在准备讲解…');view();send({type:'text',text});}catch(err){status('音频启动失败：'+err.message);responding=false;renderControls();}}
$('start').onclick=()=>{if(active||micStarting){stopMic();interrupt();status('对话已结束');}else if(responding){interrupt();status('已停止讲解');}else startMic();};
$('configure').onclick=async()=>{try{await voiceSettings.save();}catch(e){$('voiceConfigStatus').textContent=e.message;settingsTab('voice');return;}settingsApplied=true;for(const key of Object.keys(audioPreferences))audioPreferences[key]=$(key).value;stopMic();configure(true);};
$('settingsOpen').onclick=()=>{voiceSettings.load();settingsApplied=false;settingsDraft={mode:$('mode').value,source:$('source').value,background:$('background').value,codexModel:$('codexModel').value,...audioPreferences};for(const [key,value] of Object.entries(audioPreferences))$(key).value=value;syncSettings();refreshDevices();$('settingsDialog').showModal();};
$('settingsDialog').addEventListener('close',()=>{if(settingsDraft&&!settingsApplied){voiceSettings.discard();for(const [key,value] of Object.entries(settingsDraft))$(key).value=value;}settingsDraft=null;syncSettings();});
async function refreshDevices(){
  const supportsOutput='setSinkId' in AudioContext.prototype;$('speakerDevice').disabled=!supportsOutput;
  try{const devices=await navigator.mediaDevices.enumerateDevices();for(const [id,kind] of [['microphoneDevice','audioinput'],['speakerDevice','audiooutput']]){const select=$(id),chosen=select.value;select.replaceChildren(new Option('系统默认',''));for(const device of devices.filter(d=>d.kind===kind&&d.deviceId&&d.deviceId!=='default'))select.add(new Option(device.label||'未命名设备',device.deviceId));select.value=[...select.options].some(o=>o.value===chosen)?chosen:'';}
    $('deviceHint').textContent=supportsOutput?'设备与播放速度在保存后生效。设备名称需要麦克风权限。':'当前浏览器使用系统默认扬声器，可在系统声音设置中更改。';
  }catch{$('deviceHint').textContent='无法列出设备，将使用系统默认。';}
}
$('mode').onchange=syncSettings;
$('settingsClose').onclick=()=>$('settingsDialog').close();
function setOutline(expanded){document.body.classList.toggle('outline-open',expanded);$('drawerBackdrop').hidden=!expanded;for(const id of ['outlineToggle','directoryOpen'])$(id).setAttribute('aria-expanded',String(expanded));if(!expanded)$('directoryOpen').focus({preventScroll:true});}
function toggleOutline(){setOutline(!document.body.classList.contains('outline-open'));if(document.body.classList.contains('outline-open'))$('outlineToggle').focus();}
$('drawerBackdrop').onclick=()=>setOutline(false);
$('focusReading').onclick=()=>{setOutline(false);setChat(false);$('reader').focus({preventScroll:true});};
function setChat(expanded){document.body.classList.toggle('chat-closed',!expanded);$('chatToggle').setAttribute('aria-expanded',String(expanded));if(!expanded)$('chatToggle').focus();}
function settingsTab(name){for(const id of ['model','reading','voice']){const selected=id===name;$(id+'Tab').setAttribute('aria-selected',String(selected));$(id+'Tab').tabIndex=selected?0:-1;$(id+'Settings').hidden=!selected;}}
for(const name of ['model','reading','voice']){const tab=$(name+'Tab');tab.onclick=()=>settingsTab(name);tab.onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const names=['model','reading','voice'];const next=e.key==='Home'?'model':e.key==='End'?'voice':names[(names.indexOf(name)+(e.key==='ArrowRight'?1:2))%3];settingsTab(next);$(next+'Tab').focus();}};}
const dock=document.querySelector('.voice-dock');$('voiceZone').append(dock);const returnButton=document.createElement('button');returnButton.id='resumeFollow';returnButton.className='quiet';returnButton.textContent='回到讲解处';returnButton.hidden=true;$('voiceZone').append(returnButton);
$('outlineToggle').onclick=toggleOutline;$('directoryOpen').onclick=toggleOutline;
$('chatClose').onclick=()=> $('chatToggle').click();
$('chatToggle').onclick=()=>setChat(document.body.classList.contains('chat-closed'));
$('reader').addEventListener('click',e=>{const block=e.target.closest('[data-block]');if(block&&!getSelection()?.toString()){selectedText='';interrupt();navigate(block.id,true);}});
$('reader').addEventListener('keydown',e=>{if((e.key==='Enter'||e.key===' ')&&e.target.matches('[data-block]')){e.preventDefault();interrupt();navigate(e.target.id,true);}});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('settingsDialog').open&&document.body.classList.contains('outline-open'))toggleOutline();});
// Figma's shared particle timeline follows real microphone/output activity.
const wave=$('waveform'),waveSamples=new Uint8Array(128);
const particleTops=[18.7,22.77,25.79,27.13,26.56,24.24,20.68,16.62,12.91,10.34,9.51,10.67,13.62,17.74,22.1,25.66,27.54,27.26,24.89,21.01,16.57,12.63];
particleTops.forEach((top,i)=>{const particle=document.createElement('span');particle.className='wave-particle';particle.style.left=(5+i*5)+'px';particle.style.top=top+'px';const img=document.createElement('img');img.src='/static/assets/imgWaveParticle'+(i+1)+'.svg';img.alt='';particle.append(img);wave.append(particle);});
function drawWave(){
  let level=active?inputLevel:0;
  if(outputAnalyser&&nodes.size&&!holding){outputAnalyser.getByteTimeDomainData(waveSamples);level=Math.max(level,Math.sqrt(waveSamples.reduce((s,x)=>s+((x-128)/128)**2,0)/128));}
  wave.classList.toggle('sounding',level>.003);wave.style.opacity=String(.45+Math.min(.55,level*6));
  requestAnimationFrame(drawWave);
}drawWave();

document.querySelectorAll('.file').forEach(label=>label.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();label.querySelector('input').click();}}));
document.querySelectorAll('[data-prompt]').forEach(button=>button.onclick=()=>ask(button.dataset.prompt));
$('question').oninput=renderControls;
$('question').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('questionForm').requestSubmit();}};
$('questionForm').onsubmit=e=>{e.preventDefault();const text=$('question').value.trim();if(text){ask(text);$('question').value='';renderControls();}};
$('continue').onclick=()=>{if(responding){interrupt();status('讲解已暂停，可以继续提问');return;}follow=true;updateFollow();ask('请逐段讲解当前段落，讲完后前进到下一段。');};
function resumeFollow(){follow=true;updateFollow();if(current)navigate(current,true);else view();}
$('resumeFollow').onclick=resumeFollow;
$('follow').onclick=()=>{if(!follow){resumeFollow();return;}follow=false;updateFollow();view();};
$('reader').addEventListener('wheel',()=>{follow=false;updateFollow();view();},{passive:true});
$('reader').addEventListener('touchmove',()=>{follow=false;updateFollow();view();},{passive:true});
document.addEventListener('selectionchange',()=>{const selection=getSelection();if(selection?.anchorNode&&$('reader').contains(selection.anchorNode)&&selection.toString().trim()){selectedText=selection.toString();view();}});
$('contextFile').onchange=async e=>{const file=e.target.files[0];if(!file)return;if(file.size>150000){status('背景文件过大');return;}$('background').value=await file.text();};
setInterval(()=>{if(ws?.readyState===WebSocket.OPEN){if(Date.now()-lastPong>25000){ws.close();return;}send({type:'ping'});}},10000);
window.addEventListener('hashchange',()=>{stopMic();stopPlayback();location.reload();});
window.addEventListener('pagehide',()=>{clearTimeout(reconnectTimer);stopMic();stopPlayback();if(ws){ws.onclose=null;ws.close();}});
async function boot(){
  const params=new URLSearchParams(location.hash.slice(1));
  const access=params.get('access');
  if(access){
    try{
      if(!/^[a-f0-9]{64}$/.test(access))throw new Error('Invalid application link');
      const response=await fetch('/session',{method:'POST',headers:{Authorization:'Bearer '+access},cache:'no-store'});
      if(!response.ok)throw new Error('Open this page again from Lalo Lite');
      params.delete('access');history.replaceState(null,'',location.pathname+(params.size?'#'+params.toString():''));
    }catch(error){status(error.message);return;}
  }
  const token=params.get('launch');
  if(token){
    try{
      if(!/^[a-f0-9]{64}$/.test(token))throw new Error('启动链接无效');
      const response=await fetch('/launch/'+token,{cache:'no-store'});
      if(!response.ok)throw new Error('启动链接已过期，请在原会话重新运行 Skill');
      const launch=await response.json();
      try{localStorage.setItem('lalo-last-launch',JSON.stringify({token,name:launch.document.name,source:launch.source}));}catch{}
      $('mode').value=launch.mode;$('source').value=launch.source;host=launch.host||'';
      projectDir=launch.cwd;$('background').value=launch.background||'';
      loadDocument(launch.document.text,launch.document.name,launch.document.html);
      status('已绑定方案上下文');
    }catch(error){status(error.message);$('connection').textContent='绑定失败';return;}
  }else {try{const last=JSON.parse(localStorage.getItem('lalo-last-launch')||'null');if(last&&/^[a-f0-9]{64}$/.test(last.token)){const link=$('resumeLaunch');link.hidden=false;link.href='/#launch='+last.token;link.textContent='返回已绑定方案：'+last.name;link.onclick=()=>{location.href=link.href;location.reload();};}}catch{}try{const pref=JSON.parse(localStorage.getItem('lalo-model-preference')||'{}');if(['cloud','codex','claude','tiered'].includes(pref.mode))$('mode').value=pref.mode;if(['','gpt-6.1-sol','gpt-6-astra'].includes(pref.model))$('codexModel').value=pref.model;}catch{}const images=await fetch('/static/demo-images.json').then(r=>r.json()).catch(()=>[]);images.forEach((src,i)=>imageAssets['figure-'+(i+1)+'.png']=src);loadDocument(sample+'\n## 插图演示\n\n'+images.map((_,i)=>'![示例图片 '+(i+1)+'](figure-'+(i+1)+'.png)').join('\n\n'));}
  syncSettings();
  connect();
}
boot();
