import {marked} from 'marked';
import DOMPurify from 'dompurify';
import {createVoiceSettings} from './voice-settings.js';
import {createMeetingControls} from './meeting-controls.js';
import {createMeetingClient} from './meeting-client.js';
import {renderMeetingDocument} from './meeting-document.js';

const $=id=>document.getElementById(id);
let ws,epoch=0,session='',configured=false,follow=true,current=null,blocks=[],documentKey='sample';
let imageAssets={};
let meetingClient=null,meetingState=null,meetingIdentity=null,meetingHolding=false,meetingSubmitting=false,meetingSkipClick=false;
let hostListening=false,hostMicInitialized=false,hostRoomReady=false;
let meetingAudioReady=false,meetingMuted=false,outputGain,duckCandidate=null,duckTimer;
let audioPreferences={playbackSpeed:'1',microphoneDevice:'',speakerDevice:''};
let context,stream,capture,source,micGain,active=false,playCursor=0,playEnd=false,blocked=false,reconnectTimer;
let lastPong=Date.now(),micGeneration=0,micStarting=false;
let projectDir='',host='',responding=false,selectedText='',settingsDraft=null,settingsApplied=false;
let appliedConfig=null,pendingConfig=null,configSequence=0;
const nodes=new Set();
const voiceSettings=createVoiceSettings({stopMic,onDiagnostics:showReadiness});
let outputContext,holding=false,answerBubble=null,outputAnalyser,inputLevel=0;
const sentenceCounts=new Map(),sentenceEnds=new Set();
const sample='# 方案讲解助手\n\n边看方案，边与 AI 讨论。在 Codex 或 Claude Code 中运行 present-plan，带入方案和当前会话开始讲解。\n\n## 第一章：目标\n\n帮助读者理解方案的结构、设计理由与取舍。支持提问、插话，以及回到原来的讲解位置。\n\n## 第二章：语音\n\n播放期间麦克风持续工作。检测到用户讲话后停止播报，再识别问题并作答。回声消除由浏览器音频链路承担，需要在实际设备上验收。\n\n## 第三章：阅读器\n\nAI 提供段落编号，阅读器负责滚动、高亮和选区回传。只有播放真正完成后，才执行约定的下一段定位。\n';
function status(text){$('status').textContent=text;$('status').title=text;}
function showReadiness(data){
  if(!data)return;
  let panel=$('readinessPanel');
  if(!panel){panel=document.createElement('details');panel.id='readinessPanel';panel.className='readiness-panel';const summary=document.createElement('summary');summary.id='readinessSummary';const list=document.createElement('ul');list.id='readinessDetails';panel.append(summary,list);$('messages').before(panel);}
  const labels={asr:'ASR 语音识别',tts:'TTS 语音合成',llm:'独立问答模型',codex:'Codex',claude:'Claude Code',frontend:'页面资源',runtime:'语音运行时',tad:'可选话轮检测'};
  const items=Object.entries(data.readiness||{});const missing=items.filter(([key,value])=>!value.ready&&!['tad','codex','claude'].includes(key));
  $('readinessSummary').textContent=missing.length?'配置待完善：'+missing.map(([key])=>labels[key]).join('、'):'配置检查完成 · 连接尚未验证';
  $('readinessDetails').replaceChildren();for(const [key,value]of items){const li=document.createElement('li');li.textContent=(labels[key]||key)+'：'+value.message;$('readinessDetails').append(li);}
  const note=document.createElement('li');note.textContent='尚未验证模型连接；配置完整后，请在设置 → 语音模型中分别测试。';$('readinessDetails').append(note);
  const option=$('mode').querySelector('[value="tiered"]');option.disabled=!data.capabilities?.tiered;
}
const micStatus=document.createElement('span');micStatus.id='micStatus';micStatus.className='subtle';document.querySelector('.voice-status').append(micStatus);
function renderControls(){
  micStatus.hidden=!meetingClient;
  if(meetingClient){
    $('continue').hidden=false;
    if($('sharePosition')){$('sharePosition').hidden=meetingState?.role!=='host';$('sharePosition').disabled=!configured||!current;}
    $('continue').disabled=!configured;
    $('continue').textContent=meetingState?.role==='host'?(responding?'暂停讲解':'继续讲解'):!meetingAudioReady?'开启声音':meetingMuted?'恢复声音':'静音播放';
    $('settingsOpen').hidden=false;
    const mine=meetingState?.floor===meetingIdentity&&!!meetingIdentity;
    const isHost=meetingState?.role==='host';
    const queued=meetingState?.queue?.some(q=>q.kind==='speech'&&q.speaker===meetingIdentity);
    $('start').textContent=micStarting?'正在开启麦克风':meetingHolding?'松开提交':meetingSubmitting?'正在提交':mine?'按住说话':queued?'等待发言':'申请发言';
    if(isHost)$('start').textContent=micStarting?'正在开启麦克风':hostListening?'静音麦克风':'开启麦克风';
    $('start').disabled=!configured||micStarting||(!isHost&&(!!queued||meetingSubmitting));
    const cancellable=[meetingState?.active_question,...(meetingState?.queue||[])].find(q=>q?.kind==='speech'&&q.speaker===meetingIdentity);
    if($('cancelSpeech')){$('cancelSpeech').hidden=isHost||!cancellable;$('cancelSpeech').disabled=!configured;}
    $('voiceHint').textContent=mine?'按住按钮讲话，松开后提交。':'申请后按顺序发言，获得发言权前不会打开麦克风。';
    micStatus.textContent=micStarting?'正在开启麦克风':meetingHolding?'麦克风已开启 · 录音中':'麦克风未开启';
    $('audioInfo').textContent=micStarting?'正在开启麦克风':meetingHolding?'麦克风已开启 · 正在录音':'麦克风未开启';
    if(isHost){$('voiceHint').textContent=hostListening?'持续监听，停顿后自动提问；讲解时可以直接插话。':'麦克风未开启，点击开启后可直接说话。';micStatus.textContent=micStarting?'正在开启麦克风':hostListening?'麦克风已开启 · 持续监听':'麦克风未开启';$('audioInfo').textContent=micStatus.textContent;$('start').setAttribute('aria-pressed',String(hostListening));$('start').dataset.active=String(hostListening);}
    $('sendQuestion').disabled=!configured||!$('question').value.trim();
    document.querySelectorAll('[data-prompt]').forEach(button=>button.disabled=!configured);
    return;
  }
  micStatus.textContent=active?'麦克风已开启':micStarting?'正在开启麦克风':'麦克风未开启';
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

function send(data){if(meetingClient){if(['sentence_played','playback_done'].includes(data.type))meetingClient.command(data).catch(error=>status(error.message));return;}if(ws?.readyState===WebSocket.OPEN)ws.send(JSON.stringify(data));}
function message(role,text,speakerLabel){$('chatEmpty')?.remove();const div=document.createElement('div');div.className='message '+role;const label=document.createElement('small');label.textContent=speakerLabel||(role==='user'?'你':'讲解助手');div.append(label,document.createTextNode(text));$('messages').append(div);while($('messages').children.length>100)$('messages').firstChild.remove();$('messages').scrollTop=$('messages').scrollHeight;return div;}
async function audioContext(){if(!context){context=new AudioContext();}await context.resume();return context;}
function stopPlayback(){restoreDucking();holding=false;outputContext?.resume().catch(()=>{});sentenceCounts.clear();sentenceEnds.clear();answerBubble=null;for(const node of nodes){node.onended=null;try{node.stop();}catch{}node.disconnect();}nodes.clear();playCursor=0;playEnd=false;}
function interrupt(){if(meetingClient)return;responding=false;renderControls();blocked=true;stopPlayback();send({type:'interrupt'});}
function playbackDone(){if(playEnd&&nodes.size===0&&!blocked){playEnd=false;responding=false;renderControls();send({type:'playback_done',epoch});}}
function sentenceDone(index){if(sentenceEnds.has(index)&&!sentenceCounts.get(index)){sentenceEnds.delete(index);sentenceCounts.delete(index);send({type:'sentence_played',epoch,sentence:index});}}
function updateAnswer(text){if(!answerBubble)answerBubble=message('assistant',text);else {answerBubble.lastChild.textContent=text;$('messages').scrollTop=$('messages').scrollHeight;}}
async function play(data){
  const ticket=epoch,sid=session;if(blocked)return;
  const ctx=outputContext||(outputContext=new AudioContext());if(holding)await ctx.suspend();else await ctx.resume();if(ticket!==epoch||sid!==session||blocked)return;
  const bytes=Uint8Array.from(atob(data.pcm),c=>c.charCodeAt(0));
  const pcm=new Int16Array(bytes.buffer,0,Math.floor(bytes.length/2));if(!pcm.length)return;
  const buffer=ctx.createBuffer(1,pcm.length,data.rate);const floats=buffer.getChannelData(0);for(let i=0;i<pcm.length;i++)floats[i]=pcm[i]/32768;
  if(ctx.setSinkId&&ctx.sinkId!==audioPreferences.speakerDevice)await ctx.setSinkId(audioPreferences.speakerDevice);if(ticket!==epoch||sid!==session||blocked)return;const node=ctx.createBufferSource();node.buffer=buffer;node.playbackRate.value=Number(audioPreferences.playbackSpeed);if(!outputAnalyser){outputAnalyser=ctx.createAnalyser();outputAnalyser.fftSize=256;outputGain=ctx.createGain();outputGain.gain.value=meetingMuted?0:duckCandidate ? .25 : 1;outputAnalyser.connect(outputGain);outputGain.connect(ctx.destination);}node.connect(outputAnalyser);nodes.add(node);
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
async function receiveModelEvent(data){
    lastPong=Date.now();
    if(data.type==='ready'){showReadiness(data);const tieredOption=$('mode').querySelector('[value="tiered"]');tieredOption.disabled=!data.capabilities?.tiered;if($('mode').value==='tiered'&&tieredOption.disabled){$('mode').value=host==='claude'?'claude':'codex';appliedConfig=null;}await voiceSettings.load();session=data.session;epoch=data.epoch;blocked=true;stopPlayback();$('connection').textContent='已连接';configure();return;}
    if(data.session!==session||data.epoch<epoch)return;
    if(data.epoch>epoch){epoch=data.epoch;stopPlayback();}
    if(data.type==='interrupted'){stopPlayback();blocked=true;responding=false;}
    if(data.type==='turn_started'){blocked=false;playEnd=false;responding=true;}
    if(data.type==='turn_empty'){responding=false;}
    if(data.type==='configured'){if(data.configId!==undefined&&data.configId!==pendingConfig?.configId)return;configured=true;blocked=false;$('configState').textContent=`已加载 ${data.blocks} 个段落`;$('locatorInfo').textContent=data.locator==='flash'?'段落定位：Flash':data.locator==='jev'?'章节定位：Jev':'段落定位：讲解模型';showBinding(data);try{localStorage.setItem('lalo-model-preference',JSON.stringify({mode:data.mode||appliedConfig.mode,model:data.codexModel??appliedConfig.codexModel}));}catch{}status(active?'正在聆听':'准备就绪');if($('settingsDialog').open&&settingsApplied)$('settingsDialog').close();view();}
    if(data.type==='context_ready')showBinding(data);
    if(data.type==='microphone_revoked'){stopMic();stopPlayback();responding=false;status(data.reason==='configuration'?'语音配置不完整，请打开设置 → 语音模型':'另一页面已开始对话，本页麦克风已停止');}
    if(data.type==='status')status(data.message);
    if(data.type==='location'){
      const descriptions={selected:'已匹配章节',uncertain:'判断不确定，保持当前位置',timeout:'请求超时，保持当前位置',http_451:'服务限制访问，保持当前位置',not_configured:'未配置密钥，保持当前位置',document_too_large:'文档超出定位范围，保持当前位置',empty_document:'文档为空',invalid_response:'响应无效，保持当前位置',network_error:'网络错误，保持当前位置'};
      $('locatorInfo').textContent='Jev：'+(descriptions[data.status]||'定位不可用，保持当前位置')+(data.elapsedMs!==undefined?`（${data.elapsedMs} ms）`:'');
    }
    if(data.type==='playback_duck'){duckCandidate=data.candidate;clearTimeout(duckTimer);applyOutputGain();duckTimer=setTimeout(restoreDucking,1500);}
    if(data.type==='playback_restore'&&duckCandidate===data.candidate)restoreDucking();
    if(data.type==='playback_hold'){holding=true;if(outputContext)await outputContext.suspend();}
    if(data.type==='playback_resume'){holding=false;if(outputContext)await outputContext.resume();status('继续讲解');}
    if(data.type==='transcript_partial')status('你：'+data.text);
    if(data.type==='backchannel'&&data.text)message('user',data.text);
    if(data.type==='transcript')message('user',data.text,meetingClient&&data.speaker?(data.speaker===meetingIdentity?'你':data.speaker_role==='host'?'主持人':'参会者'):undefined);
    if(data.type==='answer_partial'&&!blocked)updateAnswer(data.text);
    if(data.type==='navigate_current'&&!blocked)navigate(data.blockId,data.force===true);
    if(data.type==='sentence_end'&&!blocked){sentenceEnds.add(data.sentence);sentenceDone(data.sentence);}
    if(data.type==='answer'&&!blocked){navigate(data.blockId,data.force===true);updateAnswer(data.text);}
    if(data.type==='audio')await play(data);
    if(data.type==='audio_end'&&!blocked){playEnd=true;playbackDone();}
    if(data.type==='navigate'&&!blocked){const moved=navigate(data.blockId);if(moved&&follow&&$('auto').checked)await ask('请逐段讲解当前段落，讲完后前进到下一段。');}
    if(data.type==='error'){responding=false;stopPlayback();const stage={asr:'语音识别',model:'模型回答',tts:'语音合成'}[data.stage]||'请求';const detail=stage+'失败'+(data.code?' ['+data.code+']':'')+'：'+data.message;status(detail);message('assistant',detail);}
    renderControls();
}
function connect(){
  ws=new WebSocket((location.protocol==='https:'?'wss://':'ws://')+location.host+'/ws');let received=Promise.resolve();
  ws.onmessage=e=>{received=received.then(async()=>{
    await receiveModelEvent(JSON.parse(e.data));
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
async function ask(text){if(meetingClient){if(!configured)return;try{await audioContext();outputContext=outputContext||new AudioContext();await outputContext.resume();await enableMeetingAudio();await meetingClient.command({type:'ask',text});}catch(error){status(error.message);}return;}if(!configured){status('尚未连接或配置完成');return;}try{await audioContext();interrupt();responding=true;renderControls();status('正在准备讲解…');view();send({type:'text',text});}catch(err){status('音频启动失败：'+err.message);responding=false;renderControls();}}
$('start').onclick=()=>{if(meetingClient){if(meetingState?.role==='host'){toggleHostRecording();return;}if(meetingSkipClick){meetingSkipClick=false;return;}if(meetingState?.floor!==meetingIdentity)meetingClient.command({type:'request_speech'}).catch(error=>status(error.message));return;}if(active||micStarting){stopMic();interrupt();status('对话已结束');}else if(responding){interrupt();status('已停止讲解');}else startMic();};
async function toggleHostRecording(){
  if(micStarting||!configured)return;
  micStarting=true;renderControls();
  try{
    if(hostListening){await meetingClient.stopMicrophone(false);hostListening=false;status('麦克风已静音');}
    else{await meetingClient.startMicrophone({deviceId:audioPreferences.microphoneDevice});if(!configured)return;hostListening=true;status('正在聆听，停顿后自动提问');}
  }catch(error){hostListening=false;status('麦克风开启失败：'+error.message);}
  finally{micStarting=false;renderControls();}
  if(hostListening){outputContext=outputContext||new AudioContext();outputContext.resume().catch(()=>{});enableMeetingAudio().catch(()=>status('麦克风已开启；请点击继续讲解以开启播放声音'));}
}
function startHostListening(){if(hostRoomReady&&meetingIdentity&&meetingState?.role==='host'&&!hostMicInitialized&&configured){hostMicInitialized=true;toggleHostRecording();}}
async function beginMeetingSpeech(){
  if(!meetingClient||meetingState?.floor!==meetingIdentity||meetingHolding||meetingSubmitting)return;
  const speechId=meetingState.active_question?.id;
  meetingHolding=true;micStarting=true;renderControls();
  try{outputContext=outputContext||new AudioContext();await outputContext.resume();if(!meetingHolding)return;await enableMeetingAudio();if(!meetingHolding)return;await meetingClient.startMicrophone({deviceId:audioPreferences.microphoneDevice});if(meetingHolding)status('正在录音');}
  catch(error){
    meetingHolding=false;
    const detail=error.name==='NotFoundError'?'未检测到可用麦克风。请连接麦克风后重试，也可以文字提问。':error.name==='NotAllowedError'?'麦克风权限未获允许。请在浏览器中允许使用麦克风后重试。':error.name==='NotReadableError'?'麦克风无法读取，可能被其他应用占用。请检查设备后重试。':'麦克风开启失败：'+error.message;
    status(detail);message('assistant',detail);
    try{await meetingClient.stopMicrophone(false);if(speechId)await meetingClient.command({type:'cancel_speech',speech_id:speechId});}
    catch{status(detail+' 发言申请释放失败，请点击取消发言。');}
  }
  finally{micStarting=false;renderControls();}
}
async function endMeetingSpeech(){
  if(!meetingClient||!meetingHolding)return;
  meetingHolding=false;meetingSubmitting=true;renderControls();
  try{await meetingClient.stopMicrophone();}catch(error){status(error.message);}
  finally{meetingSubmitting=false;renderControls();}
}
$('start').onpointerdown=event=>{if(meetingClient&&meetingState?.role!=='host'&&meetingState?.floor===meetingIdentity){event.preventDefault();meetingSkipClick=true;$('start').setPointerCapture(event.pointerId);beginMeetingSpeech();}};
$('start').onpointerup=()=>{if(meetingState?.role!=='host')endMeetingSpeech();};
$('start').onpointercancel=endMeetingSpeech;
$('start').onkeydown=event=>{if(meetingClient&&meetingState?.role!=='host'&&meetingState?.floor===meetingIdentity&&[' ','Enter'].includes(event.key)){event.preventDefault();if(!event.repeat)beginMeetingSpeech();}};
$('start').onkeyup=event=>{if(meetingClient&&meetingState?.role!=='host'&&[' ','Enter'].includes(event.key)){event.preventDefault();endMeetingSpeech();}};
window.addEventListener('blur',()=>{if(meetingClient&&meetingState?.role!=='host')endMeetingSpeech();});
$('configure').onclick=async()=>{if(meetingClient){settingsApplied=true;for(const key of Object.keys(audioPreferences))audioPreferences[key]=$(key).value;$('settingsDialog').close();status('本机阅读与音频设置已保存');return;}try{await voiceSettings.save();}catch(e){$('voiceConfigStatus').textContent=e.message;settingsTab('voice');return;}settingsApplied=true;for(const key of Object.keys(audioPreferences))audioPreferences[key]=$(key).value;stopMic();configure(true);};
$('settingsOpen').onclick=()=>{if(!meetingClient)voiceSettings.load();settingsApplied=false;settingsDraft={mode:$('mode').value,source:$('source').value,background:$('background').value,codexModel:$('codexModel').value,...audioPreferences};for(const [key,value] of Object.entries(audioPreferences))$(key).value=value;syncSettings();refreshDevices();if(meetingClient){const host=meetingState?.role==='host';$('modelTab').hidden=$('voiceTab').hidden=!host;document.querySelectorAll('#modelSettings input,#modelSettings select,#modelSettings textarea,#modelSettings button,#voiceSettings input,#voiceSettings select,#voiceSettings button').forEach(n=>n.disabled=true);let note=$('meetingSettingsNote');if(!note){note=document.createElement('p');note.id='meetingSettingsNote';note.className='subtle';$('readingSettings').prepend(note);}note.textContent='本机设置不会改变其他参会人。会议使用开始时的模型配置，模型与会话在会议结束后调整。';settingsTab('reading');}$('settingsDialog').showModal();};
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
for(const name of ['model','reading','voice']){const tab=$(name+'Tab');tab.onclick=()=>settingsTab(name);tab.onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const names=['model','reading','voice'].filter(n=>!$(n+'Tab').hidden);const next=e.key==='Home'?'model':e.key==='End'?'voice':names[(names.indexOf(name)+(e.key==='ArrowRight'?1:names.length-1))%names.length];settingsTab(next);$(next+'Tab').focus();}};}
const dock=document.querySelector('.voice-dock');$('voiceZone').append(dock);const returnButton=document.createElement('button');returnButton.id='resumeFollow';returnButton.className='quiet';returnButton.textContent='回到讲解处';returnButton.hidden=true;$('voiceZone').append(returnButton);
$('outlineToggle').onclick=toggleOutline;$('directoryOpen').onclick=toggleOutline;
$('chatClose').onclick=()=> $('chatToggle').click();
$('chatToggle').onclick=()=>setChat(document.body.classList.contains('chat-closed'));
$('reader').addEventListener('click',e=>{const block=e.target.closest('[data-block]');if(block&&!getSelection()?.toString()){selectedText='';if(meetingClient){follow=false;updateFollow();}interrupt();navigate(block.id,true);}});
$('reader').addEventListener('keydown',e=>{if((e.key==='Enter'||e.key===' ')&&e.target.matches('[data-block]')){e.preventDefault();if(meetingClient){follow=false;updateFollow();}interrupt();navigate(e.target.id,true);}});
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
async function enableMeetingAudio(){await meetingClient.enableAudio();meetingAudioReady=true;renderControls();}
function applyOutputGain(){if(outputGain)outputGain.gain.value=meetingMuted?0:duckCandidate ? .25 : 1;}
function restoreDucking(){duckCandidate=null;clearTimeout(duckTimer);applyOutputGain();}
function muteMeetingAudio(value){meetingMuted=value;applyOutputGain();document.querySelectorAll('audio[data-meeting-speaker]').forEach(a=>a.muted=value);renderControls();}
$('continue').onclick=async()=>{if(meetingClient){try{if(meetingState?.role!=='host'){if(!meetingAudioReady){outputContext=outputContext||new AudioContext();await outputContext.resume();await enableMeetingAudio();}else muteMeetingAudio(!meetingMuted);return;}outputContext=outputContext||new AudioContext();await outputContext.resume();await enableMeetingAudio();await meetingClient.command({type:responding?'stop':'resume'});}catch(error){status(error.message);}return;}if(responding){interrupt();status('讲解已暂停，可以继续提问');return;}follow=true;updateFollow();ask('请逐段讲解当前段落，讲完后前进到下一段。');};
function resumeFollow(){follow=true;updateFollow();if(meetingClient){navigate(meetingState?.current,true);return;}if(current)navigate(current,true);else view();}
$('resumeFollow').onclick=resumeFollow;
$('follow').onclick=()=>{if(!follow){resumeFollow();return;}follow=false;updateFollow();view();};
$('reader').addEventListener('wheel',()=>{follow=false;updateFollow();view();},{passive:true});
$('reader').addEventListener('touchmove',()=>{follow=false;updateFollow();view();},{passive:true});
document.addEventListener('selectionchange',()=>{const selection=getSelection();if(selection?.anchorNode&&$('reader').contains(selection.anchorNode)&&selection.toString().trim()){selectedText=selection.toString();view();}});
$('contextFile').onchange=async e=>{const file=e.target.files[0];if(!file)return;if(file.size>150000){status('背景文件过大');return;}$('background').value=await file.text();};
setInterval(()=>{if(ws?.readyState===WebSocket.OPEN){if(Date.now()-lastPong>25000){ws.close();return;}send({type:'ping'});}},10000);
window.addEventListener('hashchange',()=>{stopMic();stopPlayback();location.reload();});
window.addEventListener('pagehide',()=>{meetingClient?.close().catch(()=>{});clearTimeout(reconnectTimer);stopMic();stopPlayback();if(ws){ws.onclose=null;ws.close();}});
const createMeetingButton=document.createElement('button');
createMeetingButton.id='createMeeting';createMeetingButton.textContent='开始会议';
$('focusReading').before(createMeetingButton);
createMeetingButton.onclick=async()=>{
  createMeetingButton.disabled=true;
  try{
    const response=await fetch('/api/meetings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({blocks,document:{name:$('docTitle').textContent,html:$('reader').innerHTML},configuration:{...formConfig(),cwd:projectDir}}),cache:'no-store'});
    if(!response.ok)throw new Error('创建会议失败，请检查 Room 配置');
    const created=await response.json();
    stopMic();stopPlayback();clearTimeout(reconnectTimer);
    if(ws){ws.onmessage=null;ws.onclose=null;ws.close();ws=null;}
    configured=false;session='';epoch=0;blocked=true;
    const invite=document.createElement('input');invite.id='meetingInvite';invite.readOnly=true;invite.setAttribute('aria-label','参会邀请链接');
    invite.value=created.invitation_url||(location.origin+'/#meeting='+encodeURIComponent(created.invitation));
    document.querySelector('.top-actions').append(invite);
    createMeetingButton.hidden=true;
    await bootMeeting(created.invitation,created);
  }catch(error){status(error.message);createMeetingButton.disabled=false;}
};
async function bootMeeting(invitation,created=null){
  const cacheKey='lalo-meeting:'+invitation;
  if(!created){try{created=JSON.parse(sessionStorage.getItem(cacheKey)||'null');}catch{}}
  createMeetingButton.hidden=true;
  const controls=createMeetingControls();
  const enableAudio=document.createElement('button');enableAudio.id='meetingAudio';enableAudio.textContent='开启会议声音';controls.options.append(enableAudio);
  enableAudio.onclick=async()=>{try{outputContext=outputContext||new AudioContext();await outputContext.resume();await enableMeetingAudio();enableAudio.textContent='会议声音已开启';enableAudio.disabled=true;}catch(error){status(error.message);}};
  const cancelSpeech=document.createElement('button');cancelSpeech.id='cancelSpeech';cancelSpeech.textContent='取消发言';cancelSpeech.hidden=true;$('start').after(cancelSpeech);
  cancelSpeech.onclick=async()=>{
    const request=[meetingState?.active_question,...(meetingState?.queue||[])].find(q=>q?.kind==='speech'&&q.speaker===meetingIdentity);
    if(!request)return;
    hostListening=false;meetingHolding=false;cancelSpeech.disabled=true;
    try{await meetingClient.stopMicrophone(false);await meetingClient.command({type:'cancel_speech',speech_id:request.id});}
    catch(error){status(error.message);}finally{renderControls();}
  };
  const sharePosition=document.createElement('button');sharePosition.id='sharePosition';sharePosition.textContent='同步当前位置';sharePosition.hidden=true;controls.options.append(sharePosition);
  sharePosition.onclick=async()=>{try{await meetingClient.command({type:'navigate',block_id:current});}catch(error){status(error.message);}};
  const role=controls.role;
  let loaded=false,received=Promise.resolve(),ended=false,cleanupPending=false;
  const retryButton=document.createElement('button');retryButton.id='meetingRetry';retryButton.textContent='重新同步';retryButton.hidden=true;controls.toolbar.append(retryButton);
  retryButton.onclick=async()=>{retryButton.disabled=true;try{await meetingClient.command({type:'sync'});}catch(error){status(error.message);}finally{retryButton.disabled=false;}};
  const endButton=document.createElement('button');endButton.id='endMeeting';endButton.textContent='结束会议';endButton.hidden=true;controls.toolbar.append(endButton);
  endButton.onclick=async()=>{endButton.disabled=true;try{await meetingClient.end();cleanupPending=false;endButton.hidden=true;}catch(error){cleanupPending=true;status(ended?'会议已停止，房间清理失败，请重试结束。':error.message);endButton.hidden=false;endButton.textContent='重试结束';endButton.disabled=false;}};
  const leaveButton=document.createElement('button');leaveButton.id='leaveMeeting';leaveButton.textContent='离开会议';leaveButton.hidden=true;controls.toolbar.append(leaveButton);
  leaveButton.onclick=async()=>{leaveButton.disabled=true;ended=true;configured=false;blocked=true;responding=false;hostListening=false;meetingHolding=false;stopPlayback();renderControls();try{await meetingClient.close();sessionStorage.removeItem(cacheKey);$('connection').textContent='已离开会议';status('已离开会议');leaveButton.hidden=true;controls.ended();}catch(error){status(error.message);leaveButton.disabled=false;}};
  meetingClient=createMeetingClient({onParticipants:value=>controls.participants(value),onAudioTrack:(track,speaker,subscribed)=>{
    if(subscribed){const element=track.attach();element.dataset.meetingSpeaker=speaker;element.hidden=true;element.muted=meetingMuted;document.body.append(element);}
    else for(const element of track.detach())element.remove();
  },onEvent:event=>{
    if(['meeting_ended','meeting_disconnected','meeting_reconnecting'].includes(event.type)){
      retryButton.hidden=true;ended=ended||event.type==='meeting_ended';if(ended){controls.ended();try{sessionStorage.removeItem(cacheKey);}catch{}}configured=false;blocked=true;responding=false;hostListening=false;meetingHolding=false;stopPlayback();
      $('connection').textContent=ended?'会议已结束':event.type==='meeting_reconnecting'?'会议正在重连':'会议连接已断开';status(ended?'主持人已结束会议':'连接已断开，语音已停止');endButton.hidden=(ended&&!cleanupPending)||meetingState?.role!=='host';renderControls();return;
    }
    if(ended)return;
    if(event.type==='state'){
      retryButton.hidden=true;configured=true;hostRoomReady=true;applyState(event.snapshot);$('connection').textContent='会议已连接';startHostListening();
    }else{received=received.then(async()=>{if(ended)return;if(event.type==='error'&&!event.session){renderControls();if(event.stage==='transport'){retryButton.hidden=false;status('会议服务暂时不可用，请点击重新同步。');}else status(event.message);return;}if(!session&&event.session){session=event.session;epoch=event.epoch;}await receiveModelEvent(event);}).catch(error=>status(error.message));}
  }});
  function applyState(state){
    meetingState=state;if(state.floor!==meetingIdentity)meetingHolding=false;responding=['presenting','answering'].includes(state.phase);
    controls.state(state);endButton.hidden=state.role!=='host';leaveButton.hidden=state.role==='host';
    if(!loaded){
      blocks=state.blocks;documentKey='meeting:'+invitation;
      renderMeetingDocument($('reader'),blocks,state.document);$('outline').replaceChildren();
      for(const block of blocks){
        if(block.heading){const button=document.createElement('button');button.textContent=block.text;button.dataset.target=block.id;button.onclick=()=>{follow=false;updateFollow();navigate(block.id,true);};$('outline').append(button);}
      }
      $('docTitle').textContent=state.document?.name||blocks.find(b=>b.heading)?.text||'共享方案';
      $('outlineDocTitle').textContent=$('docTitle').textContent;
      loaded=true;
    }
    $('resumeFollow').title='回到讲解处：'+(blocks.find(b=>b.id===state.current)?.text||'').slice(0,80);
    if(follow)navigate(state.current,true);
    $('configState').textContent='共享方案 · '+blocks.length+' 个内容块';
    renderControls();
  }
  renderControls();
  try{
    let joined=created;
    if(!joined){
    const response=await fetch('/api/meetings/join',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({invitation}),cache:'no-store'});
    if(!response.ok)throw new Error('邀请无效或会议已结束');
    joined=await response.json();    }
    applyState(joined.snapshot);
    meetingIdentity=await meetingClient.connect(joined.credential);
    startHostListening();
    try{sessionStorage.setItem(cacheKey,JSON.stringify(joined));}catch{}
    history.replaceState(null,'',location.pathname+'#meeting='+encodeURIComponent(invitation));
    if(joined.snapshot.role==='host'&&!$('meetingInvite')){
      const invite=document.createElement('input');invite.id='meetingInvite';invite.readOnly=true;invite.setAttribute('aria-label','参会邀请链接');
      invite.value=joined.invitation_url||(location.origin+'/#meeting='+encodeURIComponent(invitation));controls.dialog.insertBefore(invite,$('copyMeetingInvite'));
    }
    controls.invite.value=joined.invitation_url||(location.origin+'/#meeting='+encodeURIComponent(invitation));
    renderControls();
  }catch(error){$('connection').textContent='加入失败';status(error.message);}
}
async function boot(){
  const params=new URLSearchParams(location.hash.slice(1));
  if(params.has('meeting')){await bootMeeting(params.get('meeting'));return;}
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
