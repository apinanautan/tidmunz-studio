'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let state = null, currentTab = 'video', limit = 18, drafts = {}, splitDraft = '', splitView = 'storyboard', splitEdits = {video:[],image:[]}, flight = false, galleryKey = '', storyFaceGalleryKey = '', videoKey = '', storyKey = '', splitKey = '', dialogContext = null, albumPrepared = null, albumLoading = '';
let collapsedSlots = new Set();
const saveTimers = {};
try { drafts = JSON.parse(localStorage.getItem('snapgen-mobile-drafts-v2') || '{}'); splitDraft=localStorage.getItem('snapgen-mobile-split-scene')||'';currentTab = localStorage.getItem('snapgen-mobile-tab') || 'video';collapsedSlots=new Set(JSON.parse(localStorage.getItem('snapgen-mobile-collapsed-slots')||'[]')); } catch (_) {}
function keepDrafts(){try{localStorage.setItem('snapgen-mobile-drafts-v2',JSON.stringify(drafts));}catch(_){}}
function keepCollapsedSlots(){try{localStorage.setItem('snapgen-mobile-collapsed-slots',JSON.stringify([...collapsedSlots]));}catch(_){}}
function notify(message,error=false){$('toast').textContent=message;$('toast').classList.toggle('error',error);$('toast').hidden=false;clearTimeout(notify.timer);notify.timer=setTimeout(()=>$('toast').hidden=true,error?10000:6500);}
function tab(name){currentTab=['video','image','storyface','story','prompt'].includes(name)?name:'video';$('video-page').hidden=currentTab!=='video';$('image-page').hidden=currentTab!=='image';$('storyface-page').hidden=currentTab!=='storyface';$('story-page').hidden=currentTab!=='story';$('prompt-page').hidden=currentTab!=='prompt';document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('active',b.dataset.tab===currentTab));try{localStorage.setItem('snapgen-mobile-tab',currentTab);}catch(_){}}
function options(select,values,selected){const rows=values.map(v=>typeof v==='object'?v:{value:String(v),label:String(v)});if(selected && !rows.some(v=>v.value===selected))rows.unshift({value:selected,label:selected});const html=rows.map(v=>`<option value="${esc(v.value)}">${esc(v.label)}</option>`).join('');if(select.innerHTML!==html)select.innerHTML=html;select.value=selected??rows[0]?.value??'';}
function imgData(){return {...state.image,...drafts.image};}
function slotData(i){const base=state.slots[i];return {...base,...drafts['slot'+i],config:{...base.config,...drafts['slot'+i]?.config}};}
function imageFields(){const d=imgData();return {prompt:d.prompt,aspect:d.aspect,lighting:d.lighting,camera:d.camera};}
function videoFields(i){const d=slotData(i);return {prompt:d.prompt,config:d.config,expression:d.expression,no_turn_back:d.no_turn_back};}
function queueSharedSave(kind,slot=null){const tag=kind+(slot??'');clearTimeout(saveTimers[tag]);saveTimers[tag]=setTimeout(async()=>{if(flight){queueSharedSave(kind,slot);return;}if(kind==='image'){if(drafts.image)await command('image_save',imageFields());}else if(drafts['slot'+slot])await command('video_save',videoFields(slot),slot);},650);}
function dirtyImage(key,value){drafts.image={...drafts.image,[key]:value};keepDrafts();queueSharedSave('image');}
function dirtySlot(i,key,value){const tag='slot'+i;drafts[tag]={...drafts[tag]};if(['duration','camera_movement','dialogue'].includes(key))drafts[tag].config={...drafts[tag].config,[key]:value};else drafts[tag][key]=value;keepDrafts();queueSharedSave('video',i);}
async function api(url,data){const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const j=await r.json();if(!r.ok)throw Error(j.error||'คำสั่งไม่สำเร็จ');return j;}
async function command(action,data={},slot=null){
 if(flight) return false;
 if(!state?.ready){notify('โปรแกรมยังไม่พร้อม กรุณารอเชื่อมต่อ',true);return false;}
 flight=true;renderBusy();
 const id=Date.now().toString(36)+'_'+Math.random().toString(36).slice(2);
 try{let j=await api('/api/command',{id,action,data,slot});
   while(j.pending){notify('รอโปรแกรมตอบรับคำสั่ง ยังไม่ต้องกดซ้ำ');await new Promise(r=>setTimeout(r,1000));const r=await fetch('/api/receipt/'+id);j=await r.json();}
   if(!j.ok)throw Error(j.error||'คำสั่งไม่สำเร็จ');
   if(action.startsWith('video_'))delete drafts['slot'+slot];
   if(action.startsWith('image_'))delete drafts.image;
   keepDrafts();notify(j.message);await refresh();return true;
 }catch(e){notify(e.message==='Failed to fetch'?'การเชื่อมต่อขาดหาย ยังไม่ทราบผลคำสั่ง ตรวจสถานะและ Log ก่อนกดสร้างอีกครั้ง':e.message,true);return false;}
 finally{flight=false;renderBusy();}
}
function slotMarkup(i){const closed=collapsedSlots.has(i);return `<article class="slot${closed?' collapsed':''}" id="slot-${i}"><div class="slot-head"><b>Slot ${i+1}</b><span class="slot-summary" id="summary-${i}"></span><span class="status" id="status-${i}">Idle</span><button class="slot-collapse" id="collapse-${i}" data-ui="collapse-slot" data-slot="${i}" aria-expanded="${!closed}" aria-label="${closed?'เปิด':'พับ'} Slot ${i+1}">${closed?'⌄':'⌃'}</button></div>
 <div class="slot-toolbar"><button data-ui="video-library" data-slot="${i}">รูป</button><button class="model" data-ui="model" data-slot="${i}">⚙ โหมด</button><label>วินาที <select data-field="duration" data-slot="${i}" id="duration-${i}" aria-label="วินาที Slot ${i+1}"></select></label><label>กล้อง <select data-field="camera_movement" data-slot="${i}" id="camera-${i}" aria-label="กล้อง Slot ${i+1}"></select></label><label>บทพูด <select data-field="dialogue" data-slot="${i}" id="dialogue-${i}" aria-label="บทพูด Slot ${i+1}"></select></label><button data-ui="paste" data-slot="${i}">Paste</button><button class="purple" data-ui="video-prompts" data-slot="${i}">Prompt</button><button class="red" data-ui="clear-slot" data-slot="${i}">Clear</button><button data-action="video_save" data-slot="${i}">บันทึก</button><button class="green generate" data-action="video_generate" data-slot="${i}">Generate this</button></div>
 <div class="slot-body"><div class="slot-aside"><div class="preview-wrap" id="preview-${i}"></div><div class="filename" id="filename-${i}"></div><button class="purple" data-action="video_gpt" data-slot="${i}">✨ GPT ทำ Prompt วิดีโอ</button><label class="checkline"><input type="checkbox" data-field="no_turn_back" data-slot="${i}" id="no-turn-${i}">ห้ามหันหน้ากลับมา</label></div>
 <div class="slot-editor"><label class="sr-only" for="prompt-${i}">Prompt วิดีโอ Slot ${i+1}</label><textarea class="slot-prompt" id="prompt-${i}" data-field="prompt" data-slot="${i}" placeholder="ใส่ Prompt หรือกด Prompt เลือกจากคลัง"></textarea>
 <div class="expression-row"><label>อารมณ์การพูด <select id="expression-${i}" data-field="expression" data-slot="${i}"></select></label>${['ดีใจ','เศร้า','สงสัย'].map(t=>`<button data-expression="${t}" data-slot="${i}">${t}</button>`).join('')}</div>
 <div class="speaker-row"><label>มีบทพูด <select id="speaker-${i}" aria-label="ตัวละครผู้พูด Slot ${i+1}"></select></label><button data-speaker="ผู้ชาย" data-slot="${i}">ผู้ชาย</button><button data-speaker="ผู้หญิง" data-slot="${i}">ผู้หญิง</button><button data-speaker="selected" data-slot="${i}">ส่งไป</button></div></div></div><pre class="log" id="log-${i}" aria-label="Log Slot ${i+1}"></pre></article>`;}
function render(){
 if(!state?.image || !state.slots?.length)return;
 $('credit').textContent=`🪙 เครดิต ${state.credit||'—'}`;
 if(!$('slot-0'))$('slots').innerHTML=state.slots.map((s,i)=>slotMarkup(i)).join('');
 const flagHTML=state.flags.map(f=>`<label><input type="checkbox" data-flag="${esc(f.label)}" ${f.value?'checked':''}>${esc(f.label)}</label>`).join('');
 if($('flags').children.length!==state.flags.length)$('flags').innerHTML=flagHTML;
 document.querySelectorAll('[data-flag]').forEach(el=>el.checked=state.flags.find(f=>f.label===el.dataset.flag)?.value||false);
 for(const s of state.slots){const i=s.index,d=slotData(i),o=state.model_options[d.config.model];
 $('summary-'+i).textContent=[d.config.model,d.config.resolution,d.config.aspect,d.config.mode,drafts['slot'+i]?'ยังไม่บันทึก':''].filter(Boolean).join(' | ');
 $('status-'+i).textContent=(s.busy?'● ':'○ ')+s.status;$('status-'+i).classList.toggle('busy',s.busy);
 const closed=collapsedSlots.has(i),collapse=$('collapse-'+i);$('slot-'+i).classList.toggle('collapsed',closed);collapse.textContent=closed?'⌄':'⌃';collapse.setAttribute('aria-expanded',String(!closed));collapse.setAttribute('aria-label',(closed?'เปิด':'พับ')+' Slot '+(i+1));
  for(const [id,key] of [['duration','duration'],['camera','camera_movement'],['dialogue','dialogue']])options($(id+'-'+i),o[key],d.config[key]);
  options($('expression-'+i),state.expressions,d.expression);options($('speaker-'+i),['เลือกตัวละคร',...state.characters],$('speaker-'+i).value||'เลือกตัวละคร');
  if(document.activeElement!==$('prompt-'+i))$('prompt-'+i).value=d.prompt;
  $('no-turn-'+i).checked=d.no_turn_back;
  if($('preview-'+i).dataset.id!==(s.image?.id||'empty')){$('preview-'+i).innerHTML=s.image?`<a href="${s.image.url}" target="_blank"><img class="slot-preview" src="${s.image.url}?thumb=1" alt="รูปเริ่มต้น Slot ${i+1}"></a>`:'<div class="preview-empty">กด “รูป” เพื่อเลือกรูปเริ่มต้น</div>';$('preview-'+i).dataset.id=s.image?.id||'empty';}
  $('filename-'+i).textContent=s.image?.name||'ยังไม่มีรูป';setLog('log-'+i,s.log||'พร้อมรับงาน');
 }
 const d=imgData();if(document.activeElement!==$('image-prompt'))$('image-prompt').value=d.prompt||'';
 options($('image-aspect'),state.aspects,d.aspect);options($('image-lighting'),state.lighting,d.lighting);
 options($('ref-select'),[{value:'',label:'เลือกไฟล์แนบ'},...state.image.refs.map(r=>({value:r.label,label:r.label}))],$('ref-select').value);
 document.querySelectorAll('[data-camera]').forEach(b=>b.classList.toggle('active',b.dataset.camera===d.camera));
 $('story-name').textContent=state.image.story||'ยังไม่มีชื่อเรื่อง';$('story-status').textContent=state.image.story_status||'';
 $('ref-count').textContent=state.image.ref_folder_name?`โฟลเดอร์ ${state.image.ref_folder_name} · ไฟล์อ้างอิง ${state.image.refs.length} รูป`:`ยังไม่ได้เลือกโฟลเดอร์อ้างอิง · ไฟล์แนบ ${state.image.refs.length} รูป`;
 $('image-state').textContent=state.image.busy?'● กำลังทำงาน':drafts.image?'กำลังบันทึกไปโปรแกรม…':'ซิงก์กับโปรแกรมแล้ว';
 $('auto-button').textContent=state.image.auto.running?'หยุด Auto-Gen':'Auto-Gen';setLog('image-log',state.image.log||'พร้อมสร้างรูป');
 renderStoryFace();renderStory();renderPromptSplit();renderGallery();renderBusy();
}
function setLog(id,text){const el=$(id);if(el.textContent!==text){el.textContent=text;el.scrollTop=el.scrollHeight;}}
function saveToPhotos(item,button){
 const fallback=()=>{location.href=item.url;};
 const original=/\.(mp4|mov|mkv|webm)$/i.test(item.name)?'📱 บันทึกวิดีโอลงอัลบั้ม':'📸 บันทึกรูปลงอัลบั้ม';
 if(albumPrepared?.id===item.id){
  const files=[albumPrepared.file];
  if(!navigator.share||navigator.canShare&&!navigator.canShare({files})){fallback();return;}
  notify('เมนู iPhone กำลังเปิด · เลือก “บันทึกรูปภาพ” หรือ “บันทึกวิดีโอ”');
  navigator.share({files,title:item.name}).then(()=>{albumPrepared=null;button.textContent=original;}).catch(error=>{
   if(error?.name!=='AbortError'){notify('Safari แชร์ไฟล์ไม่ได้ · เปิดไฟล์เต็มจอให้แล้ว',true);fallback();}
  });
  return;
 }
 if(albumLoading===item.id)return;
 albumPrepared=null;albumLoading=item.id;button.textContent='⏳ กำลังเตรียมไฟล์…';button.disabled=true;
 fetch(item.url,{credentials:'same-origin',cache:'no-store'}).then(response=>{
  if(!response.ok)throw Error('โหลดไฟล์ไม่สำเร็จ');return response.blob();
 }).then(blob=>{
  albumPrepared={id:item.id,file:new File([blob],item.name,{type:blob.type||'application/octet-stream'})};
  const target=document.querySelector(`[data-album="${item.id}"]`)||button;
  target.textContent='✅ พร้อมแล้ว · แตะอีกครั้ง';target.disabled=false;
  notify('ไฟล์พร้อมแล้ว · แตะปุ่มเดิมอีกครั้งเพื่อเปิดเมนู iPhone');
 }).catch(()=>{button.textContent=original;notify('เตรียมไฟล์ไม่สำเร็จ · เปิดไฟล์เต็มจอให้แล้ว',true);fallback();
 }).finally(()=>{albumLoading='';renderBusy();});
}
function storyFaceFields(){return {character:$('storyface-character').value,age:$('storyface-age').value};}
function renderStoryFace(){
 const face=state.story_face||{characters:[],ages:['อัตโนมัติ'],gallery:[]};
 $('storyface-title').textContent=face.title||'ยังไม่มีชื่อเรื่อง';
 $('storyface-status').textContent=face.running?'● กำลังสร้าง':face.status||'พร้อม';
 $('storyface-status').classList.toggle('busy',!!face.running);
 options($('storyface-character'),face.characters?.length?face.characters.map(x=>({value:x.key,label:x.label})):[{value:'',label:'ยังไม่มีรายชื่อตัวละคร'}],face.selected_key||$('storyface-character').value);
 options($('storyface-age'),face.ages||['อัตโนมัติ'],face.age||'อัตโนมัติ');
 setLog('storyface-log',face.log||'เลือกตัวละครแล้วกดสร้างได้เลย');
 const pictures=face.gallery||[],key=pictures.map(x=>x.id).join(',');
 if(key!==storyFaceGalleryKey||!$('storyface-gallery').children.length){storyFaceGalleryKey=key;$('storyface-gallery').innerHTML=pictures.map(x=>`<article class="media-card"><a href="${x.url}" target="_blank"><img src="${x.url}?thumb=1" loading="lazy" alt="${esc(x.name)}"></a><div class="name">${esc(x.name)}</div><div class="media-actions"><button class="album" data-album="${x.id}">📸 บันทึกรูปลงอัลบั้ม</button><a href="${x.url}" target="_blank">เปิดรูป</a></div></article>`).join('')||'<p class="empty">รูปหน้าตรง มุมข้าง และ Body จะปรากฏที่นี่</p>';}
}
function renderGallery(){
 const pics=state.image.gallery.slice(0,limit),key=pics.map(x=>x.id).join(',');
 if(key!==galleryKey||!$('images').children.length){galleryKey=key;$('images').innerHTML=pics.map(x=>`<article class="media-card"><a href="${x.url}" target="_blank"><img src="${x.url}?thumb=1" loading="lazy" alt="${esc(x.name)}"></a><div class="name">${esc(x.name)}</div><div class="edit-row"><input id="edit-${x.id}" placeholder="แก้รูป: พิมพ์สิ่งที่ต้องการเปลี่ยน" aria-label="แก้รูป ${esc(x.name)}"><button class="green" data-edit="${x.id}">แก้ไข</button></div><div class="media-actions"><button class="album" data-album="${x.id}">📸 บันทึกรูปลงอัลบั้ม</button>${state.slots.map(s=>`<button data-send="${x.id}" data-slot="${s.index}">ส่งเข้า Slot ${s.index+1}</button>`).join('')}</div></article>`).join('')||'<p class="empty">รูปที่สร้างจะปรากฏที่นี่ แล้วกดส่งเข้า Slot เพื่อทำวิดีโอต่อได้</p>';}
 $('more-images').hidden=limit>=state.image.gallery.length;$('more-images').textContent=`โหลดรูปเก่าเพิ่ม (${Math.max(0,state.image.gallery.length-limit)})`;
 const vkey=state.videos.map(x=>x.id).join(',');if(vkey!==videoKey||!$('videos').children.length){videoKey=vkey;$('videos').innerHTML=state.videos.map(x=>`<article class="media-card"><video src="${x.url}" controls playsinline preload="none"></video><div class="name">${esc(x.name)}</div><div class="media-actions"><button class="album" data-album="${x.id}">📱 บันทึกวิดีโอลงอัลบั้ม</button><a href="${x.url}" target="_blank">เปิดวิดีโอ</a></div></article>`).join('')||'<p class="empty">วิดีโอที่สร้างเสร็จจะอยู่ที่นี่</p>';}
}
function renderBusy(){if(!state)return;document.querySelectorAll('main button,main input,main select,main textarea,#flags input').forEach(el=>{
 let busy=flight||!state.ready;
 const slot=el.closest('.slot');if(slot)busy ||= state.slots[Number(slot.id.split('-')[1])]?.busy;
 else if(el.closest('#image-page'))busy ||= state.image.busy;
 else if(el.closest('#storyface-page'))busy ||= state.story_face?.running;
 else if(el.id==='prompt-split-button')busy ||= state.prompt_split?.busy;
 if(el.dataset.album&&albumLoading===el.dataset.album)busy=true;
 if(el.id==='auto-button'&&state.image.auto?.running)busy=flight||!state.ready;
 el.disabled=!!busy;
 });}
async function refresh(){try{const r=await fetch('/api/status',{cache:'no-store'});if(!r.ok)throw Error();state=await r.json();$('offline').hidden=!!state.ready;$('connection').textContent=state.ready?'● เชื่อมต่อโปรแกรมแล้ว':'○ รอโปรแกรม';render();}catch(_){if(state)state.ready=false;$('offline').hidden=false;$('connection').textContent='○ ขาดการเชื่อมต่อ';renderBusy();}}
function dialog(title,html,context){dialogContext=context;$('dialog-title').textContent=title;$('dialog-body').innerHTML=html;if(!$('dialog').open)$('dialog').showModal();}
function modelDialog(i){const d=slotData(i);dialog('Slot '+(i+1)+' settings',`<div class="form-grid"><label>Model<select id="model-choice"></select></label><label>Resolution<select id="model-resolution"></select></label><label>Aspect<select id="model-aspect"></select></label><label>Mode<select id="model-mode"></select></label><label>วินาที<select id="model-duration"></select></label></div><p class="hint">การตั้งค่าจะบันทึกลง Slot เดียวกับบนคอม</p><div class="dialog-actions"><button data-ui="close-dialog">ยกเลิก</button><button class="green" data-ui="save-model">OK · บันทึก</button></div>`,{kind:'model',slot:i});options($('model-choice'),state.models,d.config.model);modelFields(d.config);}
function modelFields(previous={}){const o=state.model_options[$('model-choice').value];for(const key of ['resolution','aspect','mode','duration'])options($('model-'+key),o[key],o[key].includes(previous[key])?previous[key]:o[key][0]);}
function promptDialog(mode,slot){dialog('เลือก Prompt · '+(mode==='image'?'สร้างรูป':'Slot '+(slot+1)),`<input id="prompt-search" placeholder="ค้นหา Prompt" style="width:100%"><div id="prompt-list"></div>`,{kind:'prompts',mode,slot});renderPrompts('');}
function renderPrompts(query){const rows=state.prompts[dialogContext.mode];$('prompt-list').innerHTML=rows.map((x,i)=>({x,i})).filter(({x})=>(x.name+' '+x.prompt).toLowerCase().includes(query.toLowerCase())).map(({x,i})=>`<button class="prompt-choice" data-prompt="${i}"><b>${esc(x.name)}</b><p>${esc(x.prompt)}</p></button>`).join('')||'<p class="empty">ไม่มี Prompt ในคลัง</p>';}
function libraryDialog(kind,slot){const all=[...state.image.refs,...state.image.gallery];const unique=[...new Map(all.map(x=>[x.id,x])).values()];dialog(kind==='image'?'เลือกภาพอ้างอิง':'เลือกรูปเข้า Slot '+(slot+1),`<label class="button orange" style="width:100%;margin-bottom:12px">＋ อัปโหลดรูปจากมือถือ<input id="library-upload" type="file" accept="image/png,image/jpeg,image/webp" hidden></label><div class="library">${unique.map(x=>`<button data-library="${x.id}"><img src="${x.url}?thumb=1" loading="lazy" alt=""><span>${esc(x.name)}</span></button>`).join('')||'<p class="empty">ยังไม่มีรูป · อัปโหลดจากมือถือได้เลย</p>'}</div>`,{kind:'library',target:kind,slot});}
async function computerFolderDialog(path=''){const data=await api('/api/computer-folders',{path});const current=data.current||'',selected=current&&data.selected&&current.toLowerCase()===data.selected.toLowerCase();dialog('เลือกโฟลเดอร์อ้างอิงในคอม',`<div class="computer-folder-head"><button data-computer-root="1">ไดรฟ์ / ทางลัด</button>${data.parent?`<button data-computer-folder="${esc(data.parent)}">⬆ ย้อนขึ้น</button>`:''}</div><div class="computer-current">${current?esc(current):'เลือกไดรฟ์หรือโฟลเดอร์'}</div>${current?`<button class="green computer-use-folder" data-computer-use-folder="${esc(current)}">✓ ใช้โฟลเดอร์นี้ · พบรูป ${data.image_count} รูป${selected?' · กำลังใช้อยู่':''}</button>`:''}<div class="computer-folders">${data.folders.map(folder=>`<button data-computer-folder="${esc(folder.path)}"><span>📁</span><b>${esc(folder.name)}</b></button>`).join('')||'<p class="empty">ไม่มีโฟลเดอร์ย่อย</p>'}</div>`,{kind:'computer-folder'});}
function renderStory(){const story=state.story||{},paragraphs=story.paragraphs||[],key=[story.source,story.name,story.version,story.characters].join('|');$('story-file-name').textContent=story.name||'ยังไม่มีบท';$('story-source-label').textContent=story.source==='mobile'?`อัปโหลดจากมือถือ · ${Number(story.characters||0).toLocaleString('th-TH')} ตัวอักษร`:`บทที่เปิดในคอม · ${Number(story.characters||0).toLocaleString('th-TH')} ตัวอักษร`;if(key===storyKey)return;storyKey=key;$('story-document').innerHTML=paragraphs.length?`<div class="story-full-text">${paragraphs.map((p,i)=>`<p class="story-line" data-story-paragraph="${i}">${p.runs.map(r=>`<span${r.color?` style="color:${esc(r.color)}"`:''}>${esc(r.text)}</span>`).join('')||'&nbsp;'}</p>`).join('')}</div>`:'<p class="empty">ยังไม่มีบท · เปิดบทในโปรแกรมหรืออัปโหลดจากมือถือ</p>';}
async function copyStoryText(text){text=String(text||'').trim();if(!text){notify('ลากเลือกข้อความในบทก่อน',true);return;}try{await navigator.clipboard.writeText(text);notify('คัดลอกข้อความลงมือถือแล้ว');}catch(_){notify('แตะค้างที่ข้อความ แล้วเลือก “คัดลอก” ของมือถือ',true);}}
function storyText(){return [...document.querySelectorAll('.story-line')].map(el=>el.textContent).join('\n').trim();}
function findStory(){const query=$('story-search').value.trim().toLocaleLowerCase('th-TH');document.querySelectorAll('.story-line.found').forEach(el=>el.classList.remove('found'));if(!query)return;const found=[...document.querySelectorAll('.story-line')].find(el=>el.textContent.toLocaleLowerCase('th-TH').includes(query));if(!found){notify('ไม่พบข้อความนี้ในบท',true);return;}found.classList.add('found');found.scrollIntoView({behavior:'smooth',block:'center'});}
async function uploadStory(file){if(file.size>12*1024*1024)throw Error('ไฟล์บทใหญ่เกิน 12 MB');if(!/\.(docx|txt)$/i.test(file.name))throw Error('รองรับไฟล์บท DOCX และ TXT เท่านั้น');const data=await new Promise((ok,fail)=>{const reader=new FileReader();reader.onload=()=>ok(String(reader.result).split(',')[1]);reader.onerror=fail;reader.readAsDataURL(file);});return api('/api/story-upload',{name:file.name,content:data});}
function setSplitDraft(text){splitDraft=String(text||'');try{localStorage.setItem('snapgen-mobile-split-scene',splitDraft);}catch(_){}if($('prompt-split-scene')&&document.activeElement!==$('prompt-split-scene'))$('prompt-split-scene').value=splitDraft;}
function sendToSplitter(text){text=String(text||'').trim();if(!text){notify('เลือกข้อความหรือท่อนบทก่อน',true);return;}setSplitDraft(text);tab('prompt');$('prompt-split-scene').focus();window.scrollTo({top:0,behavior:'smooth'});notify('ส่งบทเข้า “แตก Prompt” แล้ว');}
function renderPromptSplit(){const split=state.prompt_split||{busy:false,status:'พร้อมสร้าง',error:''};$('prompt-split-status').textContent=split.status||'พร้อมสร้าง';$('prompt-split-status').classList.toggle('busy',!!split.busy);$('prompt-split-error').hidden=!split.error;$('prompt-split-error').textContent=split.error||'';if(!splitDraft&&split.scene)setSplitDraft(split.scene);if(document.activeElement!==$('prompt-split-scene'))$('prompt-split-scene').value=splitDraft;const source={video:split.video_prompts||[],image:split.image_prompts||[]},key=JSON.stringify(source);if(key!==splitKey){splitKey=key;splitEdits.video=source.video.map(x=>x.prompt||'');splitEdits.image=source.image.map(x=>x.prompt||'');}document.querySelectorAll('[data-split-view]').forEach(b=>b.classList.toggle('active',b.dataset.splitView===splitView));const box=$('prompt-split-results');if(splitView==='storyboard'){box.innerHTML=split.storyboard?`<article class="storyboard-result"><a href="${split.storyboard.url}" target="_blank"><img src="${split.storyboard.url}" alt="Storyboard ล่าสุด"></a><b>${esc(split.storyboard.name)}</b><span>แตะรูปเพื่อเปิดขนาดเต็ม</span></article>`:'<p class="empty">ยังไม่มีภาพ Storyboard · กดสร้าง Storyboard + Prompt ก่อน</p>';}else{const rows=splitEdits[splitView]||[];box.innerHTML=rows.map((prompt,i)=>`<article class="prompt-slot-card"><div class="prompt-result-head"><b>${String(i+1).padStart(2,'0')}</b><span>${splitView==='video'?'Prompt วิดีโอ':'Prompt รูป'}</span></div><textarea data-split-edit="${splitView}" data-split-index="${i}">${esc(prompt)}</textarea><div class="prompt-result-actions"><button data-split-result="${i}" data-target="copy">คัดลอก</button>${splitView==='image'?`<button class="orange" data-split-result="${i}" data-target="image">ส่งไปสร้างรูป</button>`:`<button class="green" data-split-result="${i}" data-target="video-0">ส่ง Slot 1</button><button class="green" data-split-result="${i}" data-target="video-1">ส่ง Slot 2</button>`}</div></article>`).join('')||'<p class="empty">ยังไม่มี Slot ในมุมมองนี้</p>';}const count=(splitEdits.video||[]).length+'/'+(splitEdits.image||[]).length;$('prompt-result-summary').textContent=`วิดีโอ ${splitEdits.video.length} Slot · รูป ${splitEdits.image.length} Slot`;$('prompt-split-button').disabled=!!split.busy||flight||!state.ready;$('prompt-split-button').textContent=split.busy?'กำลังสร้าง Storyboard…':'🎬 สร้าง Storyboard + Prompt';document.querySelector('.prompt-save-row').hidden=splitView==='storyboard';}
async function upload(file){if(file.size>12*1024*1024)throw Error('รูปใหญ่เกิน 12 MB');const data=await new Promise((ok,fail)=>{const r=new FileReader();r.onload=()=>ok(r.result);r.onerror=fail;r.readAsDataURL(file);});return (await api('/api/upload',{name:file.name,content:String(data).split(',')[1]})).media;}
async function chooseMedia(id,kind,slot){$('dialog').close();if(kind==='image'){if(drafts.image && !await command('image_save',imageFields()))return;await command('image_attach',{media:[id]});}else {if(await command('video_load',{media:id},slot)){tab('video');$('slot-'+slot).scrollIntoView({behavior:'smooth',block:'start'});}}}
document.addEventListener('input',e=>{const el=e.target;if(el.dataset.image)dirtyImage(el.dataset.image,el.value);if(el.dataset.field)dirtySlot(Number(el.dataset.slot),el.dataset.field,el.type==='checkbox'?el.checked:el.value);if(el.dataset.splitEdit)splitEdits[el.dataset.splitEdit][Number(el.dataset.splitIndex)]=el.value;if(el.id==='prompt-search')renderPrompts(el.value);if(el.id==='prompt-split-scene')setSplitDraft(el.value);});
document.addEventListener('change',async e=>{const el=e.target;try{
 if(el.dataset.flag)await command('flag',{label:el.dataset.flag,value:el.checked});
 if(el.id==='model-choice')modelFields();
 if(el.id==='image-upload'){notify('กำลังอัปโหลดรูป…');if(drafts.image&&!await command('image_save',imageFields()))return;const ids=[];for(const f of [...el.files].slice(0,10))ids.push((await upload(f)).id);if(ids.length)await command('image_attach',{media:ids});el.value='';}
 if(el.id==='library-upload'&&el.files[0]){notify('กำลังอัปโหลดรูป…');const ctx={...dialogContext},m=await upload(el.files[0]);await chooseMedia(m.id,ctx.target,ctx.slot);}
 if(el.id==='story-upload'&&el.files[0]){notify('กำลังเปิดบทจากมือถือ…');const result=await uploadStory(el.files[0]);state.story=result.story;storyKey='';renderStory();notify('เปิดบทจากมือถือแล้ว สีจากไฟล์ Word ยังอยู่');el.value='';}
 if(el.id==='storyface-character'||el.id==='storyface-age'){await command('story_face_select',storyFaceFields());}
 }catch(error){notify(error.message,true);}});
document.addEventListener('click',async e=>{const b=e.target.closest('button,[data-tab]');if(!b||b.disabled)return;const i=Number(b.dataset.slot);try{
 if(b.dataset.tab){tab(b.dataset.tab);return;}
 if(b.dataset.action){const payload=b.dataset.action.startsWith('video_')?videoFields(i):b.dataset.action.startsWith('story_face_')?storyFaceFields():imageFields();await command(b.dataset.action,payload,Number.isNaN(i)?null:i);return;}
 if(b.dataset.camera){dirtyImage('camera',b.dataset.camera);render();return;}
 if(b.dataset.expression){dirtySlot(i,'expression',b.dataset.expression);render();return;}
 if(b.dataset.speaker){const name=b.dataset.speaker==='selected'?($('speaker-'+i).value==='เลือกตัวละคร'?'ผู้ชาย':$('speaker-'+i).value):b.dataset.speaker;dirtySlot(i,'prompt',slotData(i).prompt.trimEnd()+'\n'+name+': พูดว่า ');dirtySlot(i,'dialogue','มีบทพูด');render();$('prompt-'+i).focus();return;}
 if(b.dataset.send){await chooseMedia(b.dataset.send,'video',i);return;}
 if(b.dataset.album){const item=[...state.image.gallery,...state.videos,...(state.story_face?.gallery||[])].find(x=>x.id===b.dataset.album);if(item)saveToPhotos(item,b);return;}
 if(b.dataset.edit){await command('image_edit',{media:b.dataset.edit,instruction:$('edit-'+b.dataset.edit).value});return;}
 if(b.dataset.splitView){splitView=b.dataset.splitView;renderPromptSplit();return;}
 if(b.dataset.splitResult!==undefined){const prompt=(splitEdits[splitView]||[])[Number(b.dataset.splitResult)]||'';if(b.dataset.target==='copy'){await copyStoryText(prompt);return;}if(b.dataset.target==='image'){dirtyImage('prompt',prompt);tab('image');render();$('image-prompt').focus();return;}const slot=Number(String(b.dataset.target).split('-')[1]);dirtySlot(slot,'prompt',prompt);tab('video');render();$('slot-'+slot).scrollIntoView({behavior:'smooth',block:'start'});$('prompt-'+slot).focus();return;}
 if(b.dataset.computerRoot){await computerFolderDialog('');return;}
 if(b.dataset.computerFolder!==undefined){await computerFolderDialog(b.dataset.computerFolder);return;}
 if(b.dataset.computerUseFolder!==undefined){if(drafts.image&&!await command('image_save',imageFields()))return;if(await command('image_ref_folder',{path:b.dataset.computerUseFolder}))$('dialog').close();return;}
 if(b.dataset.library){await chooseMedia(b.dataset.library,dialogContext.target,dialogContext.slot);return;}
 if(b.dataset.prompt!==undefined){const p=state.prompts[dialogContext.mode][Number(b.dataset.prompt)].prompt;if(dialogContext.mode==='image')dirtyImage('prompt',p);else dirtySlot(dialogContext.slot,'prompt',p);$('dialog').close();render();return;}
 switch(b.dataset.ui){
 case 'collapse-slot':collapsedSlots.has(i)?collapsedSlots.delete(i):collapsedSlots.add(i);keepCollapsedSlots();render();break;
 case 'story-find':findStory();break;
 case 'story-copy-all':await copyStoryText(storyText());break;
 case 'story-copy-selection':await copyStoryText(window.getSelection()?.toString());break;
 case 'story-split-selection':sendToSplitter(window.getSelection()?.toString());break;
 case 'story-desktop':{const result=await api('/api/story-desktop',{});state.story=result.story;storyKey='';renderStory();notify('กลับมาใช้บทที่เปิดอยู่ในคอมแล้ว');break;}
 case 'prompt-paste-short':try{const text=await navigator.clipboard.readText();if(!text.trim())throw Error('คลิปบอร์ดว่าง');setSplitDraft(text);$('prompt-split-scene').focus();notify('วางบทสั้นแล้ว');}catch(_){$('prompt-split-scene').focus();notify('Safari ไม่อนุญาตให้อ่านคลิปบอร์ด · แตะค้างในช่องแล้วเลือก “วาง”',true);}break;
 case 'prompt-split-clear':setSplitDraft('');$('prompt-split-scene').focus();break;
 case 'prompt-split-start':await command('prompt_split_start',{scene:$('prompt-split-scene').value});break;
 case 'prompt-slots-clear':if(splitView!=='storyboard'){splitEdits[splitView]=[];renderPromptSplit();}break;
 case 'prompt-slots-save':if(splitView!=='storyboard')await command('prompt_split_save',{mode:splitView,prompts:splitEdits[splitView]});break;
 case 'close-dialog':$('dialog').close();break;
 case 'model':modelDialog(i);break;
 case 'save-model':{const slot=dialogContext.slot,d=videoFields(slot);d.config={...d.config,model:$('model-choice').value};for(const k of ['resolution','aspect','mode','duration'])d.config[k]=$('model-'+k).value;if(await command('video_save',d,slot))$('dialog').close();break;}
 case 'video-prompts':promptDialog('video',i);break;
 case 'image-prompts':promptDialog('image');break;
 case 'image-library':await computerFolderDialog('');break;
 case 'video-library':libraryDialog('video',i);break;
 case 'clear-image':dirtyImage('prompt','');render();await command('image_save',imageFields());break;
 case 'clear-slot':dialog('ล้าง Slot '+(i+1)+' ?',`<p>ล้างรูปและ Prompt ใน Slot นี้ ไฟล์ผลงานยังคงอยู่</p><div class="dialog-actions"><button data-ui="close-dialog">ยกเลิก</button><button class="red" data-ui="confirm-clear-slot">Clear</button></div>`,{slot:i});break;
 case 'confirm-clear-slot':if(await command('video_clear',{},dialogContext.slot))$('dialog').close();break;
 case 'clear-gallery':dialog('ล้างแกลเลอรี?',`<p>ซ่อนรูปจากแกลเลอรีเหมือนปุ่มในโปรแกรม ไฟล์จริงยังอยู่บนคอม</p><div class="dialog-actions"><button data-ui="close-dialog">ยกเลิก</button><button class="red" data-ui="confirm-clear-gallery">ล้างรูป</button></div>`);break;
 case 'confirm-clear-gallery':if(await command('image_clear_gallery'))$('dialog').close();break;
 case 'paste-ref':{const ref=$('ref-select').value;if(!ref){notify('เลือกชื่อไฟล์แนบก่อน',true);break;}const box=$('image-prompt'),start=box.selectionStart,end=box.selectionEnd;dirtyImage('prompt',box.value.slice(0,start)+' '+ref+' '+box.value.slice(end));render();box.focus();break;}
 case 'paste':try{const text=await navigator.clipboard.readText();dirtySlot(i,'prompt',text);render();}catch(_){$('prompt-'+i).focus();notify('แตะค้างในช่อง Prompt แล้วเลือก “วาง” ของมือถือ');}break;
 case 'more-images':limit+=18;renderGallery();break;
 case 'auto':if(state.image.auto.running){await command('image_stop_auto');break;}dialog('เลือกช่วง Auto-Gen',`<p>สร้าง Storyboard แล้วสร้างซีนตามช่วงที่เลือก ใช้คิวเดียวกับโปรแกรม</p><div class="form-grid"><label>จากซีน<input id="auto-from" type="number" min="1" value="1"></label><label>ถึงซีน<input id="auto-to" type="number" min="1" value="1"></label></div><div class="dialog-actions"><button data-ui="close-dialog">ยกเลิก</button><button class="pink" data-ui="start-auto">เริ่ม Auto-Gen</button></div>`);break;
 case 'start-auto':if(await command('image_auto',{...imageFields(),from:Number($('auto-from').value),to:Number($('auto-to').value)}))$('dialog').close();break;
 }
 }catch(error){notify(error.message,true);}});
tab(currentTab);refresh();setInterval(refresh,800);
