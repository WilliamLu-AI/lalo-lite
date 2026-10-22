// Meeting controls extend the existing reader instead of replacing its layout.
export function createMeetingControls() {
  const $=id=>document.getElementById(id);
  const make=(tag,id,text)=>{const n=document.createElement(tag);if(id)n.id=id;if(text)n.textContent=text;return n;};
  const toolbar=make('div','meetingToolbar');toolbar.className='meeting-toolbar';
  const members=make('button','meetingMembers');members.type='button';
  const count=make('span','meetingCount','1 人 · '),role=make('span','meetingRole');members.append(count,role);
  const inviteButton=make('button','inviteMeeting','邀请参会');inviteButton.type='button';
  toolbar.append(members,inviteButton);$('focusReading').before(toolbar);
  const dialog=make('dialog','meetingDialog');dialog.className='meeting-dialog';dialog.setAttribute('aria-labelledby','meetingDialogTitle');
  const title=make('h2','meetingDialogTitle','邀请参会');
  const description=make('p','meetingDescription','其他人打开邀请链接，即可加入当前文档讲解。');
  const list=make('ul','meetingParticipants');
  const invite=$('meetingInvite')||make('input','meetingInvite');invite.readOnly=true;invite.setAttribute('aria-label','参会邀请链接');
  const copy=make('button','copyMeetingInvite','复制邀请链接');copy.type='button';
  const feedback=make('p','meetingCopyStatus');feedback.className='subtle';feedback.setAttribute('role','status');
  const note=make('p','meetingExpiry','邀请仅在本次会议中有效。');note.className='subtle';
  const options=make('div','meetingOptions');options.className='meeting-options';
  const close=make('button','meetingDialogClose','关闭');close.type='button';
  dialog.append(title,description,list,invite,copy,feedback,note,options,close);document.body.append(dialog);
  members.onclick=inviteButton.onclick=()=>dialog.showModal();close.onclick=()=>dialog.close();
  copy.onclick=async()=>{try{await navigator.clipboard.writeText(invite.value);feedback.textContent='邀请链接已复制';}catch{invite.focus();invite.select();feedback.textContent='无法自动复制，请复制上方已选中的链接。';}};
  let currentRole='attendee',participants=[],connected=false;
  function render(){
    role.textContent=currentRole==='host'?'主持人':'参会者';count.textContent=participants.length+' 人 · ';
    const host=currentRole==='host';inviteButton.hidden=!host;invite.hidden=copy.hidden=note.hidden=!host;
    title.textContent=host?'邀请参会':'参会状态';description.textContent=host?'其他人打开邀请链接，即可加入当前文档讲解。':'你正在与其他参会人同步阅读和收听。';
    list.replaceChildren();for(const person of participants){const li=make('li');li.append(make('span',null,person.self?'你（'+role.textContent+'）':person.name||'其他参会人'),make('span',null,'已连接'));list.append(li);}
    const ai=make('li');ai.append(make('span',null,'Lalo'),make('span',null,connected?'讲解助手 · 已连接':'讲解助手 · 未连接'));list.append(ai);
  }
  document.body.classList.add('meeting-active');
  return {toolbar,dialog,role,options,invite,
    state(value){currentRole=value.role;render();},
    participants(value){participants=value.people;connected=value.aiConnected;render();},
    ended(){inviteButton.disabled=copy.disabled=true;invite.value='';feedback.textContent='会议已结束，邀请已失效。';},
  };
}
