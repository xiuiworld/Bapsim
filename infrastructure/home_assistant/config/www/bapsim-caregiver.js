/* Bapsim caregiver card. Native HA element; no framework or remote dependencies. */
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const stamp = value => Number.isFinite(Date.parse(value)) ? new Intl.DateTimeFormat('ko-KR', {timeZone:'Asia/Seoul',month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}).format(new Date(value)) : '기록 없음';
const time = value => Number.isFinite(Date.parse(value)) ? new Intl.DateTimeFormat('ko-KR', {timeZone:'Asia/Seoul',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value)) : '미확인';
export function derive(states, now=Date.now(), source='matter') {
  const state = id => states['sensor.bapsim_'+id] || {state:'unknown',attributes:{}};
  const out=state('output_state').attributes, obs=state('current_observation').attributes;
  const live=out.source===source, fields=obs.source===source ? obs.fields||{} : {};
  const fresh=live&&Date.parse(out.valid_until)>now;
  const offline=['disconnected','alert_requested'].includes(state('observation_issue_status').state)||fields.device_connection?.value==='offline';
  const usable=fresh&&!offline&&['available','partial'].includes(out.observation);
  const observed=obs.source===source&&Date.parse(obs.valid_until)>now&&!offline;
  const assessments=live&&Array.isArray(out.assessments)?out.assessments.filter(a=>a.source===source):[];
  const active=assessments.filter(a=>a.status!=='resolved');
  let kind='collecting',title='부모님의 일상을<br>조금씩 알아가요',subtitle='밥솥 사용 기록을 모으고 있어요.',label='기록 수집 중';
  if(!usable){kind='unavailable';label='현재 상태 확인 필요';title='밥솥 소식이<br>잠시 닿지 않고 있어요';subtitle=offline?'연결이 끊겨 사용 변화를 판단할 수 없어요.':!fresh?'최근 기록이 없어 현재 상태를 알 수 없어요.':'입력 상태를 확인할 때까지 판단을 보류해요.';}
  else if(active.some(a=>a.caregiver_status==='checking')){kind='checking';label='안부 확인 중';title='부모님의 안부를<br>확인하고 있어요';subtitle='확인을 마치면 완료를 남겨주세요.';}
  else if(active.length){kind='attention';label='안부 확인 필요';title='부모님께 안부를<br>물어봐 주세요';subtitle=active.some(a=>a.type==='declining_usage')?'최근 사용 기록이 평소보다 줄었어요.':'평소 사용 시간이 지났지만 개방 기록이 없어요.';}
  else if(state('detection_status').state==='check_required'||state('interval_detection').state==='watch'){kind='watch';label='사용 공백 관찰 중';title='오늘은 평소보다<br>밥솥 소식이 늦네요';subtitle='이후 기록을 조금 더 살피고 있어요.';}
  else if(out.learning==='personalized'){kind='normal';label='일상 살피는 중';title='오늘도 부모님의<br>일상을 살피고 있어요';subtitle='지금 안내할 사용 변화는 없어요.';}
  const b=state('baseline').attributes;
  const windows=live&&out.learning==='personalized'?(b.windows||[]).filter(w=>w.covered_days>=(b.min_covered_days||7)&&w.positive_days>=5&&!w.schedule_excluded&&Number.isFinite(Date.parse(w.expected_time))):[];
  return {out,obs,fields,fresh,usable,observed,assessments,active,kind,title,subtitle,label,windows,last:live?out.last_usage_at:null,delivery:state('notification_delivery')};
}
const resolutions={caregiver_completed:'보호자가 안부 확인 완료',usage_resumed:'개방 기록으로 사용 재개',change_cleared:'사용 변화 해소',away_declared:'외출 설정으로 종료',request_cancelled:'알림 요청 종료',acknowledgement_expired:'재알림 보류 기한 종료'};
const deliveries={simulation:'합성 알림 요청 재생 · 실제 발송 없음',not_sent:'발송 이력 없음',pending:'알림 요청 처리 중',ha_accepted:'HA 알림 호출 수락',failed:'전송 실패',uncertain:'전송 결과 불명확',paused:'알림 보류 적용',quiet_hours:'야간 발송 대기',cancelled:'요청 취소'};
export class BapsimCaregiver extends HTMLElement {
  constructor(){super();this.attachShadow({mode:'open'});this.page='home';this.notice='';this.busy=false;this.draft={};}
  setConfig(config){this.config=config;this.render();}
  set hass(hass){this._hass=hass;this.render();}
  connectedCallback(){this.tick=setInterval(()=>this.render(),15000);}
  disconnectedCallback(){clearInterval(this.tick);clearInterval(this.replayTimer);}
  getCardSize(){return 8;}
  async startReplay(){
    if(this.busy)return;
    try{
      const response=await fetch(new URL('./bapsim-caregiver-replay.json',import.meta.url),{cache:'no-store'});
      if(!response.ok)throw Error('replay unavailable');
      const data=await response.json();
      if(data.source!=='simulator'||data.mode!=='recorded_replay'||data.real_push_sent!==false||!Array.isArray(data.frames)||!data.frames.length||data.frames.some(f=>f.states?.['sensor.bapsim_output_state']?.attributes.source!=='simulator'))throw Error('invalid replay');
      if(data.schema_version!==2||!Number.isInteger(data.start_index)||!data.frames[data.start_index])throw Error('replay version');
      this.replay=data;this.replayIndex=data.start_index;this.replayPaused=true;this.replaySpeed=1;this.replayElapsed=0;this.page='home';this.notice='';this.draft={};this.routineEditor=null;this.replaySettings={};
      clearInterval(this.replayTimer);
      this.replayTimer=setInterval(()=>this.advanceReplay(),250);
      this.render();
    }catch{this.replayError='재생 자료를 불러오지 못했어요. 설치 파일을 확인해 주세요.';this.render();}
  }
  advanceReplay(){
    if(!this.replay||this.replayPaused)return;
    const f=this.replay.frames[this.replayIndex];
    if(f.wait_for_response||this.replayIndex>=this.replay.frames.length-1){this.replayPaused=true;this.render();return;}
    this.replayElapsed+=250*this.replaySpeed;
    if(this.replayElapsed<1000)return;
    this.replayElapsed=0;this.replayIndex++;
    if(this.replay.frames[this.replayIndex].wait_for_response)this.replayPaused=true;
    this.render();
  }
  replayResponse(id,response){
    const frame=this.replay?.frames[this.replayIndex];
    if(!frame)return;
    const m=derive(frame.states,Date.parse(frame.at),'simulator');
    const target=frame.responses?.[response];
    const next=this.replay.frames[target];
    if(!m.usable||!m.active.some(a=>a.assessment_id===id)||!Number.isInteger(target)||!next||!next.states['sensor.bapsim_output_state'].attributes.assessments.some(a=>a.assessment_id===id))return;
    this.replayIndex=target;this.replayPaused=true;this.replayElapsed=0;this.notice='';this.render();
  }
  replayBanner(){
    if(!this.replay)return '';
    const f=this.replay.frames[this.replayIndex];
    return `<aside style="flex-shrink:0;position:sticky;bottom:16px;z-index:2;margin:16px 12px;padding:10px 14px;background:#f7f7f1f5;border:1px solid #d8dfd2;border-radius:20px;box-shadow:0 6px 24px #233c2926;backdrop-filter:blur(12px)" aria-label="시연 재생"><div style="display:flex;justify-content:space-between;font-size:12px"><span title="합성 기록 · 실제 발송 없음">시연</span><time>${escapeHTML(stamp(f.at))}</time></div><div style="display:flex;justify-content:space-between"><button data-replay="pause" ${f.wait_for_response||this.replayIndex===this.replay.frames.length-1?'disabled':''}>${f.wait_for_response?'응답 대기':this.replayPaused?'재생':'정지'}</button><button data-replay="speed" aria-label="재생 속도">${this.replaySpeed}×</button><button data-replay="restart">처음으로</button><button data-replay="exit">시연 종료</button></div></aside>`;
  }
  bindReplay(){
    this.shadowRoot.querySelectorAll('[data-replay]').forEach(b=>b.onclick=()=>{
      const a=b.dataset.replay;
      if(a==='start')return this.startReplay();
      if(a==='exit'){clearInterval(this.replayTimer);this.replay=null;this.page='home';}
      if(a==='pause'&&!this.replay.frames[this.replayIndex].wait_for_response){this.replayPaused=!this.replayPaused;this.replayElapsed=0;}
      if(a==='speed')this.replaySpeed=this.replaySpeed===4?1:this.replaySpeed*2;
      if(a==='restart'){this.replayIndex=this.replay.start_index;this.replayPaused=true;this.replayElapsed=0;this.page='home';this.notice='';}
      this.render();
    });
    if(!this.replay)return;
    this.shadowRoot.querySelectorAll('[data-path]').forEach(b=>b.disabled=!['routine','travel','details','notice'].includes(b.dataset.path));
  }

  saveReplaySettings(form){
    if(!this.replay||!form.reportValidity())return;
    const f=Object.fromEntries(new FormData(form));
    const now=this.replay.frames[this.replayIndex].at;
    this.replaySettings??={};
    if(form.dataset.kind==='routine'){
      const windows=[];
      for(const i of [1,2,3,4]){
        const day=f['days'+i]||'사용 안 함',t=f['time'+i]||'';
        if(day==='사용 안 함')continue;
        const days=day==='매일'?[0,1,2,3,4,5,6]:day==='평일'?[0,1,2,3,4]:day==='주말'?[5,6]:day==='요일 직접 선택'?[0,1,2,3,4,5,6].filter(d=>f['day'+i+'_'+d]):[];
        if(!/^([01]\d|2[0-3]):[0-5]\d$/.test(t)||!days.length){this.notice='사용할 시간과 요일을 확인해 주세요.';this.render();return;}
        windows.push({time:t,weekdays:days});
      }
      if(!windows.length){this.notice='사용 시간대를 한 개 이상 선택해 주세요.';this.render();return;}
      for(const i of [1,2,3,4]){
        this.replaySettings['input_select.bapsim_routine_days_'+i]={state:f['days'+i]||'사용 안 함',attributes:{options:['사용 안 함','매일','평일','주말','요일 직접 선택']}};
        this.replaySettings['input_datetime.bapsim_routine_time_'+i]={state:f['time'+i]||''};
        for(const d of [0,1,2,3,4,5,6])this.replaySettings['input_boolean.bapsim_routine_day_'+i+'_'+d]={state:f['day'+i+'_'+d]?'on':'off'};
      }
      this.replaySettings['sensor.bapsim_initial_routine']={attributes:{routines:[{source:'simulator',device_id:'demo_cooker',windows}]}};
    }else{
      const end=Date.parse(f.end+'+09:00');
      if(!Number.isFinite(end)||end<=Date.parse(now)){this.notice='종료 시각은 시연 시각보다 뒤로 정해 주세요.';this.render();return;}
      const controls=this.replaySettings['sensor.bapsim_notification_controls']??={attributes:{pauses:{},acknowledgements:{}}};
      if(form.dataset.kind==='travel'){
        const start=Date.parse(f.start+'+09:00');
        if(!Number.isFinite(start)||start>=end){this.notice='시작과 종료 시각을 확인해 주세요.';this.render();return;}
        controls.attributes.pauses['replay-'+(Object.keys(controls.attributes.pauses).length+1)]={starts_at:new Date(start).toISOString(),ends_at:new Date(end).toISOString()};
      }else if(form.dataset.kind==='notice'){
        const m=derive(this.replay.frames[this.replayIndex].states,Date.parse(now),'simulator');
        if(!m.active.some(a=>a.assessment_id===f.incident)){this.notice='현재 확인 요청을 선택해 주세요.';this.render();return;}
        controls.attributes.acknowledgements[f.incident]={ends_at:new Date(end).toISOString()};
      }else return;
    }
    this.notice='';this.draft={};this.render();
  }
  navigate(path){history.pushState(null,'',path);window.dispatchEvent(new Event('location-changed'));}
  async respond(id,response){
    if(this.replay){this.replayResponse(id,response);return;}
    const m=derive(this._hass.states),a=m.active.find(a=>a.assessment_id===id);
    if(!m.usable||!a?.device_id||this.busy){this.notice='최근 상태가 바뀌었어요. 안내를 다시 확인해 주세요.';this.render();return;}
    this.busy=true;this.notice='';this.render();
    try{await this._hass.callService('bapsim','respond',{source:'matter',device_id:a.device_id,incident_id:a.assessment_id,response});this.notice='';}
    catch(e){this.notice='응답을 저장하지 못했어요. 연결과 안내 상태를 다시 확인해 주세요.';}
    finally{this.busy=false;this.render();}
  }

  routineContext(){
    const states=this.replay?this.replaySettings||{}:this._hass.states;
    const routines=states['sensor.bapsim_initial_routine']?.attributes.routines||[];
    const current=this._hass.states['sensor.bapsim_current_observation']?.attributes;
    const device=this.replay?'demo_cooker':current?.source==='matter'?current.device_id:states['sensor.bapsim_matter_input']?.attributes.device_id||(routines.length===1?routines[0].device_id:null);
    return {device,windows:routines.find(r=>r.device_id===device)?.windows||[]};
  }
  routineView(e){
    const {device,windows}=this.routineContext(),edit=this.routineEditor;
    const dayLabel=days=>days.length===7?'매일':days.join(',')==='0,1,2,3,4'?'평일':days.join(',')==='5,6'?'주말':days.map(d=>['월','화','수','목','금','토','일'][d]).join(' · ');
    return `<p class="form-note">사용하는 시간과 요일을 등록해 주세요.</p><div class="routine-list">${windows.map((w,i)=>`<section class="routine-item"><div><strong>${e(w.time)}</strong><p>${e(dayLabel(w.weekdays))}</p></div><div><button data-routine-edit="${i}" ${this.busy?'disabled':''} aria-label="${e(w.time)} 수정">수정</button><button data-routine-delete="${i}" ${this.busy?'disabled':''} aria-label="${e(w.time)} 삭제">삭제</button></div></section>`).join('')||'<p class="empty-note">등록한 시간이 없어요.<br>필요할 때 하나씩 추가하세요.</p>'}</div>${edit?`<form data-kind="routine-entry" class="routine-editor"><fieldset ${this.busy?'disabled':''}><label class="field">사용 시간<input type="time" data-routine-time value="${e(edit.time)}" required></label><label class="field day-title">사용 요일</label><div class="day-pills" role="group" aria-label="사용 요일">${['월','화','수','목','금','토','일'].map((d,i)=>`<button type="button" data-routine-day="${i}" aria-label="${d}요일" aria-pressed="${edit.weekdays.includes(i)}">${d}</button>`).join('')}</div><div class="entry-actions"><button type="button" data-routine-close>취소</button><button class="primary" type="submit">${edit.index===null?'시간 등록':'변경 저장'}</button></div></fieldset></form>`:`<button class="add-time" data-routine-add ${this.busy||!device||windows.length>=4?'disabled':''}>＋ 시간 추가</button>${windows.length>=4?'<p class="form-note">현재 분석 모델은 최대 4개 시간을 지원해요.</p>':''}${!device?'<p>연결된 밥솥을 확인한 뒤 등록할 수 있어요.</p>':''}`}`;
  }
  async saveRoutineWindows(windows){
    if(this.busy||windows.length>4)return;
    const {device}=this.routineContext();
    if(!device)return;
    if(this.replay){
      this.replaySettings??={};
      this.replaySettings['sensor.bapsim_initial_routine']={attributes:{routines:[{source:'simulator',device_id:device,windows:structuredClone(windows)}]}};
      this.routineEditor=null;this.notice='';this.render();return;
    }
    this.busy=true;
    try{await this._hass.callService('bapsim','set_initial_routine',{device_id:device,windows});this.routineEditor=null;this.notice='';}
    catch{this.notice='저장하지 못했어요. 연결 상태를 확인해 주세요.';}
    finally{this.busy=false;this.render();}
  }
  async submitRoutineEntry(form){
    if(this.busy||!form.reportValidity()||!this.routineEditor)return;
    const edit=this.routineEditor,{windows}=this.routineContext();
    if(edit.basis!==JSON.stringify(windows)){this.notice='등록 목록이 바뀌었어요. 다시 열어 주세요.';this.routineEditor=null;this.render();return;}
    if(!/^([01]\d|2[0-3]):[0-5]\d$/.test(edit.time)||!edit.weekdays.length){this.notice='시간과 사용 요일을 선택해 주세요.';this.render();return;}
    const next=structuredClone(windows),item={time:edit.time,weekdays:[...edit.weekdays].sort()};
    if(edit.index===null)next.push(item);else next[edit.index]=item;
    if(next.some((w,i)=>next.some((v,j)=>i!==j&&w.time===v.time&&w.weekdays.some(d=>v.weekdays.includes(d))))){this.notice='같은 시간과 요일이 이미 등록돼 있어요.';this.render();return;}
    await this.saveRoutineWindows(next);
  }
  bindRoutine(){
    const open=index=>{const {windows}=this.routineContext();if(index===null&&windows.length>=4)return;this.routineEditor={index,time:index===null?'':windows[index].time,weekdays:index===null?[0,1,2,3,4,5,6]:[...windows[index].weekdays],basis:JSON.stringify(windows)};this.notice='';this.render();};
    this.shadowRoot.querySelectorAll('[data-routine-add]').forEach(b=>b.onclick=()=>open(null));
    this.shadowRoot.querySelectorAll('[data-routine-edit]').forEach(b=>b.onclick=()=>open(Number(b.dataset.routineEdit)));
    this.shadowRoot.querySelectorAll('[data-routine-delete]').forEach(b=>b.onclick=()=>this.saveRoutineWindows(this.routineContext().windows.filter((_,i)=>i!==Number(b.dataset.routineDelete))));
    this.shadowRoot.querySelectorAll('[data-routine-time]').forEach(b=>b.oninput=()=>this.routineEditor.time=b.value);
    this.shadowRoot.querySelectorAll('[data-routine-day]').forEach(b=>b.onclick=()=>{const d=Number(b.dataset.routineDay),days=this.routineEditor.weekdays;this.routineEditor.weekdays=days.includes(d)?days.filter(x=>x!==d):[...days,d].sort();this.render();});
    this.shadowRoot.querySelectorAll('[data-routine-preset]').forEach(b=>b.onclick=()=>{this.routineEditor.weekdays=b.dataset.routinePreset==='all'?[0,1,2,3,4,5,6]:b.dataset.routinePreset==='week'?[0,1,2,3,4]:[5,6];this.render();});
    this.shadowRoot.querySelectorAll('[data-routine-close]').forEach(b=>b.onclick=()=>{this.routineEditor=null;this.render();});
  }
  settingsView(m,e){
    if(!['routine','travel','details','notice'].includes(this.page))return '';
    const states=this.replay?{...this.replay.frames[this.replayIndex].states,...this.replaySettings}:this._hass.states;
    const val=(key,fallback='')=>e(this.draft[key]??fallback);
    const settingsNow=this.replay?Date.parse(this.replay.frames[this.replayIndex].at):Date.now();
    const pauses=Object.entries(states['sensor.bapsim_notification_controls']?.attributes.pauses||{}).filter(([,p])=>!p.cancelled_at&&Date.parse(p.ends_at)>settingsNow);
    const title={routine:'평소 사용 시간',travel:'외출 기간 알림 쉬기',details:'관측·사용 기록',notice:'재알림 보류'}[this.page];
    let body='';
    if(this.page==='routine'){
      body=this.routineView(e);
    }else if(this.page==='travel'){
      body=`<p class="form-note">외출하는 동안 사용 변화 알림을 쉬어요.</p><section class="paper"><h3>저장된 기간</h3>${pauses.map(([id,p])=>`<div class="saved"><dl class="period"><dt>시작</dt><dd>${e(stamp(p.starts_at))}</dd><dt>종료</dt><dd>${e(stamp(p.ends_at))}</dd></dl><button data-cancel="${e(id)}" ${this.busy?'disabled':''}>이 기간 취소</button></div>`).join('')||'<p class="empty-note">등록된 외출 기간이 없어요.</p>'}</section><form data-kind="travel"><fieldset ${this.busy?'disabled':''}><div class="form-row"><label>시작 (한국 시간)<input name="start" data-field="start" type="datetime-local" value="${val('start')}" required></label><label>종료 (한국 시간)<input name="end" data-field="end" type="datetime-local" value="${val('end')}" required></label></div><button class="primary wide" type="submit">이 기간 알림 쉬기</button></fieldset></form>`;
    }else if(this.page==='notice'){
      body=`<p class="form-note">선택한 요청의 재알림을 잠시 쉬어요.</p><form data-kind="notice"><fieldset ${this.busy||!m.usable||!m.active.length?'disabled':''}><label class="field">확인 요청<select name="incident" data-field="incident" required><option value="">선택해 주세요</option>${m.active.map(a=>`<option value="${e(a.assessment_id)}" ${this.draft.incident===a.assessment_id?'selected':''}>${a.type==='declining_usage'?'최근 사용 감소':'평소 시간대 사용 공백'} · ${e(stamp(a.created_at||a.requested_at))}</option>`).join('')}</select></label><label class="field">다시 알릴 시각<input name="end" data-field="end" type="datetime-local" value="${val('end')}" required></label><button class="primary wide" type="submit">재알림 잠시 보류</button></fieldset></form>${!m.usable?'<p>최신 관측을 확인한 뒤 처리할 수 있어요.</p>':!m.active.length?'<p>현재 보류할 확인 요청이 없어요.</p>':''}`;
    }else{
      const v=(k,f)=>m.observed&&m.fields[k]?.quality==='good'?f(m.fields[k].value):'확인 불가';
      body=`<section class="paper"><h3>밥솥 관측</h3><dl><dt>뚜껑</dt><dd>${v('lid_open',v=>typeof v==='boolean'?(v?'열림':'닫힘'):'확인 불가')}</dd><dt>취사·보온 추정</dt><dd>${v('cooker_phase',v=>({cooking:'취사 추정',warming:'보온 추정',inactive:'비활성 추정'}[v]||'판단 불가'))}</dd><dt>부착부 표면 온도</dt><dd>${v('surface_temperature_c',v=>typeof v==='number'?e(v)+' °C':'확인 불가')}</dd><dt>최근 수신</dt><dd>${e(stamp(m.obs.received_at))}</dd></dl>${!m.observed?'<p>최신 관측을 확인할 수 없어요.</p>':''}</section><section class="paper"><h3>사용 기록</h3><p>최근 개방 · ${e(stamp(m.last))}</p><p>평소 사용 · ${m.windows.length?m.windows.map(w=>e(time(w.expected_time))+' 전후').join(' · '):'개인 기준 수집 중'}</p><p>${e(m.subtitle)}</p></section>`;
    }
    const r=states['sensor.bapsim_notification_control_result'];
    const result=this.page===this.commandPage&&this.lastCommand&&r?.attributes.command_id===this.lastCommand?({rejected:'저장되지 않았어요. 기간을 확인해 주세요.',failed:'저장하지 못했어요. 다시 시도해 주세요.'}[r.state]||''):'';
    return `<main class="settings-detail"><div class="subhead"><button class="back" data-page="settings" aria-label="설정으로 돌아가기"><svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true"><path d="m14 6-6 6 6 6" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></button><h1>${title}</h1></div>${this.replay?'<p class="form-note">시연용 설정 · 실제 설정에는 영향 없음</p>':''}<div class="settings-result" role="status">${this.page==='details'?'':e([this.notice,result].filter(Boolean).join(" "))}</div>${body}</main>`;
  }
  async control(data){
    if(this.replay){
      if(data.action==='cancel_pause'){
        const p=this.replaySettings?.['sensor.bapsim_notification_controls']?.attributes.pauses?.[data.pause_id];
        if(p&&!p.cancelled_at){p.cancelled_at=this.replay.frames[this.replayIndex].at;this.notice='';this.render();}
      }
      return;
    }
    if(this.busy)return;
    if(data.action==='cancel_pause'){
      const p=this._hass.states['sensor.bapsim_notification_controls']?.attributes.pauses?.[data.pause_id];
      if(!p||p.cancelled_at||Date.parse(p.ends_at)<=Date.now()){this.notice='기간이 변경됐어요. 다시 확인해 주세요.';this.render();return;}
    }
    this.busy=true;this.notice='';this.commandPage=this.page;
    this.lastCommand=globalThis.crypto?.randomUUID?.()||`care-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    this.render();
    try{await this._hass.callService('bapsim','set_notification_control',{...data,command_id:this.lastCommand});this.notice='';this.draft={};}
    catch{this.notice='요청하지 못했어요. 기간과 연결 상태를 확인해 주세요.';}
    finally{this.busy=false;this.render();}
  }
  async saveSettings(form){
    if(form?.dataset.kind==='routine-entry'){await this.submitRoutineEntry(form);return;}
    if(this.replay){if(form)this.saveReplaySettings(form);return;}
    if(this.busy||!form.reportValidity())return;
    const fields=Object.fromEntries(new FormData(form));
    if(form.dataset.kind==='routine'){
      if(![1,2,3,4].some(i=>fields['days'+i]!=='사용 안 함')){this.notice='사용 시간대를 한 개 이상 선택해 주세요.';this.render();return;}
      if([1,2,3,4].some(i=>fields['days'+i]!=='사용 안 함'&&!/^\d{2}:\d{2}$/.test(fields['time'+i]))){this.notice='사용할 시간대의 시각을 입력해 주세요.';this.render();return;}
      if([1,2,3,4].some(i=>!this._hass.states['input_select.bapsim_routine_days_'+i]||!this._hass.states['input_datetime.bapsim_routine_time_'+i])){this.notice='사용 시간 설정을 불러오지 못했어요.';this.render();return;}
      if([1,2,3,4].some(i=>fields['days'+i]==='요일 직접 선택'&&![0,1,2,3,4,5,6].some(d=>fields['day'+i+'_'+d]))){this.notice='직접 선택한 사용 요일을 하나 이상 골라 주세요.';this.render();return;}
      this.busy=true;this.notice='';this.render();
      try{
        for(const i of [1,2,3,4]){
          if(fields['days'+i]!=='사용 안 함')await this._hass.callService('input_datetime','set_datetime',{entity_id:'input_datetime.bapsim_routine_time_'+i,time:fields['time'+i]+':00'});
          if(fields['days'+i]==='요일 직접 선택')for(const d of [0,1,2,3,4,5,6])await this._hass.callService('input_boolean',fields['day'+i+'_'+d]?'turn_on':'turn_off',{entity_id:'input_boolean.bapsim_routine_day_'+i+'_'+d});
          await this._hass.callService('input_select','select_option',{entity_id:'input_select.bapsim_routine_days_'+i,option:fields['days'+i]});
        }
        await this._hass.callService('bapsim','dashboard_initial_routine',{});
        this.notice='';
      }catch{this.notice='적용하지 못했어요. 연결된 밥솥과 설정을 확인한 뒤 다시 저장해 주세요.';}
      finally{this.busy=false;this.render();}
      return;
    }
    const end=Date.parse(fields.end+'+09:00');
    if(!Number.isFinite(end)||end<=Date.now()){this.notice='종료 시각은 현재보다 뒤로 정해 주세요.';this.render();return;}
    if(form.dataset.kind==='travel'){
      const start=Date.parse(fields.start+'+09:00');
      if(!Number.isFinite(start)||start>=end){this.notice='시작과 종료 시각을 확인해 주세요.';this.render();return;}
      await this.control({action:'pause_lifestyle',pause_id:`pause-${Date.now()}-${Math.random().toString(36).slice(2)}`,starts_at:new Date(start).toISOString(),ends_at:new Date(end).toISOString()});
    }else{
      const m=derive(this._hass.states);
      if(!m.usable||!m.active.some(a=>a.assessment_id===fields.incident)){this.notice='확인 요청이 변경됐어요. 다시 선택해 주세요.';this.render();return;}
      await this.control({action:'acknowledge',assessment_id:fields.incident,ends_at:new Date(end).toISOString()});
    }
  }
  render(){if(!this._hass||!this.config)return;if(['INPUT','SELECT'].includes(this.shadowRoot.activeElement?.tagName))return;const frame=this.replay?.frames[this.replayIndex],m=derive(frame?.states||this._hass.states,frame?Date.parse(frame.at):Date.now(),frame?'simulator':'matter'),e=escapeHTML;
    const previous=[...this.shadowRoot.querySelectorAll('details[open]')].map(x=>x.id);
    const value=(key,format)=>m.observed&&m.fields[key]?.quality==='good'&&m.fields[key].value!=null?format(m.fields[key].value):'확인 불가';
    const report=key=>({error:'센서 오류',unsupported:'지원되지 않는 값',unavailable:'연결 끊김',unknown:'품질 미확인'}[m.fields[key]?.quality]||'관측 없음');
    const routine=(frame?{...frame.states,...this.replaySettings}:this._hass.states)['sensor.bapsim_initial_routine']?.attributes.routines||[];
    const notices=m.assessments.map(a=>`<article><div class="care-art" aria-hidden="true"><svg viewBox="0 0 320 150"><ellipse cx="160" cy="133" rx="111" ry="9" fill="#dce3d5"/><path d="M53 129V52l49-35 49 35v77" fill="#e7ebdc"/><path d="m43 57 59-43 58 43" fill="none" stroke="#8ca185" stroke-width="6" stroke-linecap="round"/><rect x="77" y="58" width="49" height="42" rx="5" fill="#fffaf0"/><path d="M101 58v42M77 79h49" stroke="#c9d5bd" stroke-width="3"/><rect x="180" y="27" width="65" height="108" rx="13" fill="#5c7864" transform="rotate(8 212 81)"/><rect x="187" y="36" width="51" height="87" rx="7" fill="#fffaf0" transform="rotate(8 212 81)"/><path d="M205 64c-8-10-19 1-10 10l14 13 13-17c7-11-6-18-12-7l-3 4Z" fill="#d7a081"/><path d="M143 65q19-19 35-10" fill="none" stroke="#b2bea6" stroke-width="3" stroke-dasharray="3 6"/><path d="M269 129V92m0 18q-19-4-17-18 17 0 17 18m0-8q17-3 18-17-17-2-18 17" fill="#b4c3a6" stroke="#879f7d" stroke-width="2"/></svg></div><h3>${a.status==='resolved'&&a.resolution_reason==='caregiver_completed'?'안부를 나눈 기록':a.type==='declining_usage'?'요즘 밥솥 사용이 줄었어요':'평소와 달라 안부가 궁금해요'}</h3>${a.status==='resolved'?`<p>${e(a.resolution_reason==='caregiver_completed'?'직접 확인하고 남긴 안부예요.':resolutions[a.resolution_reason]||'종료 사유 미확인')}</p>`:''}${m.delivery.attributes.incident_id===a.assessment_id?`<p>${e(deliveries[m.delivery.state]||'알림 처리 상태 미확인')}</p><small>HA 호출 수락은 휴대폰 도착 확인이 아니에요.</small>`:''}${a.status!=='resolved'?`<div class="actions"><button class="primary" data-response="resolved" data-id="${e(a.assessment_id)}" ${!m.usable||this.busy?'disabled':''}>안부 확인 완료</button></div>`:''}</article>`).join('');
    this.shadowRoot.innerHTML=`<style> :host{display:block;color:#294737;font-family:var(--paper-font-body1_-_font-family, sans-serif)}*{box-sizing:border-box}ha-card{display:flex;flex-direction:column;min-height:calc(100vh - var(--header-height,56px) - 16px);min-height:calc(100dvh - var(--header-height,56px) - 16px);max-width:460px;margin:0 auto;background:#faf8f2;color:#294737;border:0;border-radius:0;box-shadow:none}button{font:inherit;color:inherit;cursor:pointer;border:0;background:none;min-height:44px;font-size:12px;padding:10px 12px}button:focus-visible,summary:focus-visible{outline:3px solid #bc7849;outline-offset:2px}button:disabled{opacity:.45;cursor:default}header{flex-shrink:0;display:flex;align-items:center;justify-content:space-between;padding:17px 20px}.brand{font-size:30px;letter-spacing:-1px}nav{display:flex}nav button{font-size:15px;padding:8px 10px;border-radius:22px}nav button[aria-pressed=true]{background:#e8eddf}main{flex:1 0 auto;padding:8px 22px 20px}h1{font-size:23px;line-height:1.5;font-weight:500;margin:12px 0 25px;letter-spacing:-.7px}h2{font-size:26px;line-height:1.4;letter-spacing:-1px;font-weight:600;margin:17px 0 12px}h3{font-size:16px;font-weight:600;margin:0 0 12px}h4{font-size:12px}p{font-size:12px;line-height:1.8;color:#69785f;margin:8px 0}small{font-size:10px;color:#74816a;line-height:1.8}.hero{padding:24px 24px 0;background:#eef0e3;border-radius:24px}.hero.attention{background:#f5e9dc}.pill{font-size:10px;padding:7px 11px;background:#ffffffb0;border-radius:20px}.illustration svg{width:100%;height:165px;display:block}.hero>p{font-size:11px}.primary{background:#304f3f;color:white;border-radius:12px}.wide{width:100%;margin:8px 0}.paper{padding:18px;background:#fff;border:1px solid #e3e7dd;border-radius:22px;margin-top:16px}.metrics{display:grid;grid-template-columns:1fr 1fr;gap:15px;margin:16px 0}.metrics strong{display:block;font-size:15px;font-weight:500;line-height:1.6;margin-top:9px;word-break:keep-all}details{border-top:1px solid #dce2d3;margin:12px 0 0}summary{min-height:44px;cursor:pointer;font-size:11px;color:#63775a;display:flex;align-items:center;justify-content:space-between;list-style:none}summary:after{content:'⌄'}details[open]>summary:after{content:'⌃'}summary::-webkit-details-marker{display:none}dl{display:grid;grid-template-columns:1fr 1fr;font-size:11px;gap:12px;margin:12px 0}dd{margin:0;text-align:right}.row{display:flex;width:100%;justify-content:space-between;border-bottom:1px solid #e1e5d9;padding:18px 4px;text-align:left}.actions{display:flex;gap:8px;margin:14px 0 0}.actions button{flex:1;border:1px solid #ccd5c5;border-radius:12px}article+article{border-top:1px solid #e1e5d9;padding-top:16px;margin-top:16px}footer{font-size:10px;color:#87917d;padding:12px 22px 28px}.warning{background:#f6ebdf;padding:12px;border-radius:12px}[role=status]{font-size:12px;line-height:1.7}details.paper{border-top:1px solid #e3e7dd;padding:6px 18px}details.paper[open]{padding-bottom:18px}details.paper>summary{font-size:13px}details.paper[open]>summary{margin-bottom:8px}@media(max-width:360px){main{padding:8px 16px 20px}.hero{padding:22px 19px 0}h2{font-size:24px}}.usage{padding-bottom:4px}.usage details[open]{padding-bottom:14px}.row{font-size:15px;align-items:center}.row>span{font-size:18px;font-weight:400;line-height:1;margin-left:16px}.subhead{display:flex;align-items:center;gap:12px;margin:10px 0 20px}.subhead h1{margin:0;font-size:22px}.back{font-size:28px;padding:0;min-width:44px}.form-row{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:16px 0}.weekdays{display:flex;gap:8px;justify-content:space-between}.weekdays label{font-size:14px;text-align:center}.weekdays input{width:22px;height:22px;min-height:22px;margin:4px auto}form[data-kind="travel"] .form-row{grid-template-columns:1fr}.form-row label,label.field{display:block;font-size:13px;line-height:1.6}input,select{display:block;width:100%;min-width:0;min-height:46px;margin:7px 0 0;padding:10px;border:1px solid #cbd4c3;border-radius:10px;background:#fff;color:#294737;font:inherit;font-size:16px}input:focus-visible,select:focus-visible{outline:2px solid #597c5d}.form-note{font-size:13px}.saved{padding:12px 0;border-bottom:1px solid #e1e5d9}.saved p{margin:4px 0}.form-actions{margin-top:20px}.form-actions button{font-size:15px}fieldset{border:0;padding:0;margin:0;min-width:0}h4{font-size:13px}.settings-result{margin:12px 0;font-size:13px} @media(max-width:360px){header{padding:14px 12px}.brand{font-size:28px}nav button{padding:8px;font-size:14px}} .settings-detail .subhead{display:grid;grid-template-columns:40px minmax(0,1fr);gap:0;align-items:center;margin:8px 0 20px -12px}.settings-detail .back{display:grid;place-items:center;width:40px;min-width:40px;height:44px;padding:0}.settings-detail .subhead h1{display:flex;align-items:center;min-height:44px;font-size:21px;line-height:1.4;letter-spacing:-.6px;word-break:keep-all}.settings-detail p,.settings-detail dl{font-size:15px;line-height:1.65}.settings-detail .form-note{font-size:13px;line-height:1.6}.settings-detail .paper{padding:18px;margin-top:14px;border-radius:18px}.settings-detail h3{font-size:17px;margin:0 0 12px}.settings-detail .empty-note{margin:0;padding:0;font-size:14px;color:#69785f}.settings-detail .saved{padding:0 0 12px;margin:12px 0 0}.settings-detail .saved:last-child{border:0;padding-bottom:0}.settings-detail .period{grid-template-columns:auto minmax(0,1fr);gap:12px;margin:0}.settings-detail .period dd{text-align:right;font-weight:500}.settings-detail .saved button{font-size:14px;margin:8px 0 0;padding:6px 0;color:#586c50}.settings-detail .settings-result:empty{display:none}.routine-item{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:16px 0;border-bottom:1px solid #dde3d7}.routine-item strong{font-size:24px;font-weight:600}.routine-item p{margin:4px 0 0;font-size:14px}.routine-item>div:last-child{display:flex;gap:8px;flex-shrink:0}.routine-item button{font-size:14px;padding:8px 12px;min-height:44px;border:1px solid #ccd5c5;border-radius:10px;background:#edf1e7;font-weight:500}.routine-item button[data-routine-delete]{background:#fff;color:#805649;border-color:#dccfc7}.settings-detail .back svg{display:block;transform:translateY(1px)}.settings-detail .routine-list>.empty-note{padding:8px 0}.add-time{width:100%;border:1px dashed #a8b79f;border-radius:14px;font-size:16px;margin:18px 0 8px;padding:14px}.routine-editor{padding:18px;background:white;border:1px solid #dce2d3;border-radius:18px;margin:18px 0}.day-title{margin-top:20px}.day-pills{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:4px;margin-top:10px}.day-pills button{min-width:0;min-height:42px;padding:0;border:1px solid #dce2d3;border-radius:12px;font-size:15px}.day-pills button[aria-pressed=true]{background:#304f3f;color:white;border-color:#304f3f}.day-presets{display:flex;gap:4px;margin-top:6px}.day-presets button{font-size:14px}.entry-actions{display:flex;justify-content:flex-end;gap:10px;margin-top:18px}.entry-actions button{font-size:15px;min-width:80px}.settings-detail dl:not(.period){grid-template-columns:1fr 1fr;gap:12px}.settings-detail dd{word-break:keep-all}.settings-detail .settings-result{font-size:14px}
/* Card edges own their spacing; content margins must not add a second inset. */
.paper> :first-child,.paper>article> :first-child{margin-top:0}
.paper> :last-child,.paper>article> :last-child{margin-bottom:0}
.paper>small,details>small{display:block}
.paper>details> :last-child,.hero>details> :last-child{margin-bottom:0}
.hero>details[open]{padding-bottom:16px}
[role=status]:empty{display:none}
.routine-editor .field:first-child{margin-top:0}
fieldset>.field+.field{margin-top:16px}
fieldset>.wide:last-child{margin-bottom:0}

/* Shared dimensions, with emphasis reserved for primary actions. */
.primary,.actions button,.routine-item button,.routine-item button[data-routine-delete],
.add-time,.entry-actions button,.saved button,.form-actions button,.day-presets button,
[data-replay],.settings-detail .saved button{
  background:#edf1e7;color:#304f3f;border:1px solid #d4ddcc;border-radius:12px;
  min-height:46px;padding:11px 14px;font-size:16px;font-weight:500;line-height:1.4;
}
.primary,.actions button.primary,.entry-actions button.primary,.add-time{background:#55785d;color:#fff;border-color:#55785d}.routine-item button{min-width:60px}
.settings-detail .saved button{display:block;width:fit-content;margin:14px 0 0 auto}
.day-presets{gap:8px;flex-wrap:wrap}
[data-replay="start"]{margin-top:8px;text-align:left}
[aria-label="시연 재생"]>div:last-child{gap:6px;margin-top:8px;flex-wrap:wrap}
[aria-label="시연 재생"] button{flex:1;white-space:nowrap;padding:10px;font-size:15px}
nav button,.row{font-size:16px}
summary,details.paper>summary{font-size:14px;gap:12px}
summary:after,details[open]>summary:after{
  content:'';display:block;flex:0 0 7px;width:7px;height:7px;
  border-right:2px solid currentColor;border-bottom:2px solid currentColor;
  margin:0 5px 1px 0;transform:rotate(45deg);align-self:center;
}
details[open]>summary:after{transform:rotate(225deg);margin-top:7px;margin-bottom:0}
.hero>h2{margin-top:0}.care-mark{display:flex;align-items:center;justify-content:center;width:48px;height:48px;border-radius:50%;background:#edf1e7;color:#55785d;font-size:30px;margin-bottom:14px}.care-art{max-width:300px;margin:0 auto 18px}.care-art svg{display:block;width:100%;height:145px}  </style><ha-card><header><strong class="brand">밥심</strong><nav aria-label="밥심 메뉴"><button data-page="home" aria-pressed="${this.page==='home'}">일상</button><button data-page="history" aria-pressed="${this.page==='history'}">확인 기록${m.usable&&m.active.length?' · '+m.active.length:''}</button><button data-page="settings" aria-pressed="${(['settings','routine','travel','details','notice'].includes(this.page))}">설정</button></nav></header>
    ${this.page==='home'?`<main><section class="hero ${m.kind}"><h2>${m.title}</h2><p>${m.subtitle}</p>${m.usable&&m.active.length?'<button class="primary wide" data-page="history">안부 확인하기 ↗</button>':''}<div class="illustration"><svg viewBox="0 0 440 340" role="img" aria-label="밥솥 일러스트. 상태를 확정하는 표시가 아닙니다"><defs><linearGradient id="body" x2="0.8" y2="1"><stop stop-color="#fffefa"/><stop offset="1" stop-color="#dddccf"/></linearGradient><linearGradient id="lid" x2="0.8" y2="1"><stop stop-color="#657565"/><stop offset="1" stop-color="#344f43"/></linearGradient></defs><circle cx="238" cy="160" r="137" fill="#dfe6d8"/><path d="M30 275 Q210 236 409 277" stroke="#c4cbbd" fill="none"/><ellipse cx="230" cy="280" rx="137" ry="20" fill="#344f4316"/><path d="M104 137 Q97 127 90 141 L90 193 Q96 202 106 193 M348 137 Q358 130 362 143 L362 190 Q358 201 347 193" fill="#314a3d"/><path d="M103 133 Q100 109 139 100 L310 100 Q349 112 348 139 L339 242 Q336 275 298 277 L153 277 Q112 276 109 240Z" fill="url(#body)"/><path id="cooker-lid" d="M102 139 Q99 93 144 83 Q225 66 306 84 Q352 96 350 139 Q222 162 102 139Z" fill="url(#lid)"/><path d="M182 88 L185 69 Q223 55 261 68 L265 86" fill="none" stroke="#344f43" stroke-width="13" stroke-linecap="round"/><path d="M125 113 Q218 89 327 115" stroke="#ffffff25" stroke-width="3" fill="none"/><rect x="180" y="182" width="90" height="51" rx="20" fill="#ecece4" stroke="#d1d6ca"/><circle cx="201" cy="207" r="5" fill="#a2b19c"/><path d="M220 203h29m-29 9h17" stroke="#9ba797" stroke-width="3" stroke-linecap="round"/><text x="222" y="252" text-anchor="middle" font-size="10" fill="#8a9387" letter-spacing="5">BAPSIM</text><g stroke="#8ba17d" stroke-width="4" fill="none" stroke-linecap="round"><path d="M369 269q-17-31 0-60m-1 27 15-12m-19 27-14-10"/></g><ellipse cx="368" cy="271" rx="23" ry="7" fill="#b8c4ac"/></svg></div><details id="observation"><summary>밥솥 관측 자세히</summary><dl><dt>뚜껑</dt><dd>${e(value('lid_open',v=>typeof v==='boolean'?(v?'열림':'닫힘'):'확인 불가'))}</dd><dt>취사·보온 추정</dt><dd>${e(value('cooker_phase',v=>({cooking:'취사 추정',warming:'보온 추정',inactive:'비활성 추정'}[v]||'판단 불가')))}</dd><dt>부착부 표면 온도</dt><dd>${e(value('surface_temperature_c',v=>typeof v==='number'?v+' °C':'확인 불가'))}</dd></dl><p>${m.observed?'':'최신 관측을 확인할 수 없어요. ' }최근 수신 · ${e(stamp(m.obs.received_at))}</p>${m.observed?['lid_open','cooker_phase','surface_temperature_c'].filter(k=>m.fields[k]?.quality!=='good').map(k=>`<small>${{lid_open:'뚜껑',cooker_phase:'상태 추정',surface_temperature_c:'표면 온도'}[k]} · ${report(k)}</small>`).join('<br>'):''}</details></section>
    <section class="paper usage"><h3>최근 사용과 평소 시간</h3><div class="metrics"><div><small>최근 뚜껑 열림</small><strong>${e(stamp(m.last))}</strong></div><div><small>평소 사용 시각</small><strong>${m.windows.length?m.windows.map(w=>e(time(w.expected_time))).join(' · ')+' 전후':'기록을 모으는 중'}</strong></div></div><details id="basis"><summary>기준과 기록 자세히</summary><p>${m.windows.length?'관측 기록으로 학습한 개인 기준이에요.':'충분한 관측일과 사용 기록이 모이면 비교할 수 있어요.'}</p>${m.windows.map(w=>`<p>${e(time(w.expected_time))} 전후 · 관측 ${e(w.covered_days)}일</p>`).join('')}${m.out.initial_basis?'<p>현재 비교에는 초기 설정 시간이 활용되고 있어요.</p>':''}${!m.fresh?'<p>최근 개방은 과거 이력이에요. 현재 공백은 비교할 수 없어요.</p>':''}</details></section><button class="row" data-page="history">내가 남긴 안부 <span>→</span></button><button class="row" data-path="travel">부모님이 외출하시나요? <span>→</span></button></main>`:''}
    ${this.page==='history'?`<main><h1>확인 기록</h1><p>부모님께 건넨 안부를 모아두었어요.</p>${!m.usable?'<p class="warning">현재 관측을 확인할 수 없어 새 응답은 잠시 보류해요.</p>':''}<div role="status">${e(this.notice)}</div><section class="paper">${notices||'<h3>아직 확인 요청이 없어요</h3><p>확인할 변화가 생기면 여기에 모아드릴게요.</p>'}</section><details id="delivery" class="paper"><summary>최근 알림 처리</summary><p>${e(deliveries[m.delivery.state]||'처리 결과 미확인')}</p><small>별도 최근 이력일 수 있어요. 휴대폰 도착 여부는 여기서 확인할 수 없어요.</small></details><button class="row" data-path="notice">재알림 보류 <span>→</span></button></main>`:''}
    ${this.settingsView(m,e)}${this.page==='settings'?`<main><h1>우리 가족에게 맞게</h1><button class="row" data-path="routine">평소 사용 시간 <span>→</span></button><button class="row" data-path="travel">외출 기간 알림 쉬기 <span>→</span></button><button class="row" data-path="notice">재알림 보류 <span>→</span></button><button class="row" data-path="details">관측·사용 기록 상세 <span>→</span></button><details id="tools" class="paper"><summary>연결 및 시험 도구</summary><button class="row" data-replay="start">합성 기록으로 사용 흐름 보기 →</button><p role="status">${e(this.replayError||'')}</p><button class="row" data-path="diagnostics">연결 상세·개발자 진단 →</button><button class="row" data-path="a05-test">실물 연결 시험 →</button><button class="row" data-path="simulator">가상 입력 시험 →</button><small>가상 입력은 부모님 댁 기록과 분리돼요.</small></details></main>`:''}${this.replayBanner()}</ha-card>`;
    this.bindReplay();
    this.bindRoutine();
    this.shadowRoot.querySelectorAll('[data-field]').forEach(el=>el.oninput=()=>{this.draft[el.dataset.field]=el.type==='checkbox'?el.checked:el.value;if(el.tagName==='SELECT'){el.blur();this.render();}});
    this.shadowRoot.querySelectorAll('form').forEach(form=>form.onsubmit=ev=>{ev.preventDefault();this.saveSettings(form);});
    this.shadowRoot.querySelectorAll('[data-cancel]').forEach(b=>b.onclick=()=>this.control({action:'cancel_pause',pause_id:b.dataset.cancel}));
    previous.forEach(id=>{const el=this.shadowRoot.getElementById(id);if(el)el.open=true;});
    this.shadowRoot.querySelectorAll('[data-page]').forEach(b=>b.onclick=()=>{this.routineEditor=null;this.page=b.dataset.page;if(this.replay&&this.page==='settings')this.replayPaused=true;this.notice='';this.draft={};this.render();});
    this.shadowRoot.querySelectorAll('[data-path]').forEach(b=>b.onclick=()=>{if(['routine','travel','details','notice'].includes(b.dataset.path)){this.routineEditor=null;this.page=b.dataset.path;if(this.replay)this.replayPaused=true;this.notice='';this.draft={};this.render();}else this.navigate('/bapsim-lab/'+(this.config.paths?.[b.dataset.path]||b.dataset.path));});
    this.shadowRoot.querySelectorAll('[data-response]').forEach(b=>b.onclick=()=>this.respond(b.dataset.id,b.dataset.response));
  }
}
if(!customElements.get('bapsim-caregiver'))customElements.define('bapsim-caregiver',BapsimCaregiver);
