
const $=id=>document.getElementById(id);
async function closeDialog(dialog){
  if(!dialog.open||dialog.classList.contains('closing'))return;
  // Keep the native dialog open until its exit transition finishes (Safari included).
  dialog.classList.add('closing');
  await Promise.allSettled(dialog.getAnimations({subtree:true}).map(animation=>animation.finished));
  dialog.close();dialog.classList.remove('closing');
}
let busy=false,volT=null,spkT={},lastTouch=0,currentStatus=null,tileSignature='',speakerSignature='';
let selectedTarget='';
let buttonsStatus=null,buttonsChecking=false,buttonsGeneration=0;
const warningSvg='<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><circle cx="12" cy="17" r="1" fill="currentColor"/><path d="M12 10L12 14" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M3.44722 18.1056L10.2111 4.57771C10.9482 3.10361 13.0518 3.10362 13.7889 4.57771L20.5528 18.1056C21.2177 19.4354 20.2507 21 18.7639 21H5.23607C3.7493 21 2.78231 19.4354 3.44722 18.1056Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
function warningIcon(speaker){
  const warning=document.createElement('span');warning.className='connection-warning';warning.innerHTML=warningSvg;
  warning.title=connectionLabel(speaker)+' — Réglages → Enceintes';
  warning.setAttribute('role','img');warning.setAttribute('aria-label',warning.title);
  return warning;
}
function connectionLabel(speaker){
  const actual=currentStatus?.speakers.find(s=>s.name===speaker.name&&s.ip===speaker.ip);
  if(actual?.source==='unreachable')return 'Enceinte injoignable';
  if(buttonsStatus?.available===false)return 'Service des boutons indisponible';
  const connection=buttonsStatus?.speakers?.[speaker.name];
  if(!connection||connection.ip!==speaker.ip)return 'État des boutons inconnu';
  return connection.state==='connected'?'Boutons connectés':
    connection.state==='connecting'?'Connexion des boutons en cours':'Boutons déconnectés';
}
function connectionWarning(speaker){
  return connectionLabel(speaker)==='Enceinte injoignable'||
    (buttonsStatus!==null&&connectionLabel(speaker)!=='Boutons connectés');
}
function renderSpeakerConnectionNotice(){
  const warnings=speakerDrafts.filter(connectionWarning);
  const unreachable=warnings.filter(s=>connectionLabel(s)==='Enceinte injoignable');
  const buttons=warnings.filter(s=>connectionLabel(s)!=='Enceinte injoignable');
  const names=speakers=>{
    const quoted=speakers.map(s=>'« '+s.name+' »');
    return quoted.length>1?quoted.slice(0,-1).join(', ')+' et '+quoted.at(-1):quoted[0];
  };
  const messages=[];
  if(unreachable.length===1)messages.push('L’enceinte '+names(unreachable)+
    ' est injoignable. Rallumez-la ou vérifiez son réseau, puis cliquez sur le bouton Connecter pour relancer la connexion.');
  else if(unreachable.length>1)messages.push('Les enceintes '+names(unreachable)+
    ' sont injoignables. Rallumez-les ou vérifiez leur réseau, puis cliquez sur les boutons Connecter correspondants pour relancer les connexions.');
  if(buttons.length===1)messages.push('La connexion aux boutons physiques de l’enceinte '+names(buttons)+
    ' n’est pas confirmée. La lecture depuis l’interface reste disponible. Cliquez sur le bouton Connecter pour relancer la connexion.');
  else if(buttons.length>1)messages.push('Les connexions aux boutons physiques des enceintes '+names(buttons)+
    ' ne sont pas confirmées. La lecture depuis l’interface reste disponible. Cliquez sur les boutons Connecter correspondants pour relancer les connexions.');
  $('speakerConnectionNotice').textContent=messages.join('\n\n');
  $('speakerConnectionNotice').hidden=messages.length===0;
}
async function readButtons(){
  if(buttonsChecking)return;
  const generation=buttonsGeneration;
  try{
    const result=await api('/api/buttons');
    if(generation===buttonsGeneration&&!buttonsChecking)buttonsStatus=result;
  }catch{if(generation===buttonsGeneration)buttonsStatus={available:false,speakers:{}}}
}
async function checkButtons(name){
  if(buttonsChecking)return;
  buttonsChecking=true;buttonsGeneration++;
  $('buttonsLoading').showModal();
  try{
    buttonsStatus=await api('/api/buttons/check',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(name===undefined?{}:{name})});
  }catch(e){buttonsStatus={available:false,speakers:{}};toast('Erreur : '+e.message)}
  finally{
    buttonsChecking=false;
    $('buttonsLoading').close();
    if(currentStatus)render(currentStatus);
    renderSpeakerList();
  }
}
const targetLabel=()=>selectedTarget==='all'?'Toutes les enceintes':
  currentStatus?.speakers.find(s=>s.ip===selectedTarget)?.name||'une enceinte';
const targetQuery=()=>'?speaker='+encodeURIComponent(selectedTarget);
function disableTargets(disabled){
  Array.from($('target').children).forEach(b=>b.disabled=disabled);$('targetAll').disabled=disabled;
}
function selectTarget(value){if(busy)return;selectedTarget=value;clearTimeout(volT);lastTouch=0;
  if(currentStatus)render(currentStatus)}
const touch=()=>lastTouch=Date.now();          // suppress poll updates while user interacts
const idle=()=>Date.now()-lastTouch>1200;
const pretty=s=>s.replace(/-/g,' ').replace(/\b\w/g,c=>c.toUpperCase());
const hue=s=>{let h=0;for(const c of s)h=(h*31+c.charCodeAt(0))%360;return h};
async function api(p,options){const r=await fetch(p,options);const data=await r.json();
  if(!r.ok||data.ok===false)throw new Error(data.error||'Request failed');return data}
function toast(m){const t=$('toast');t.textContent=m;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),1800)}

function buildTiles(stations){
  $('grid').replaceChildren();
  stations.forEach(s=>{
    const b=document.createElement('button');b.className='tile';b.id='preset-'+s.number;
    b.stationId=s.id;b.disabled=!s.id;
    const h=hue(s.id||'preset-'+s.number);
    b.style.background=`linear-gradient(135deg,hsl(${h} 45% 30%),hsl(${h} 55% 16%))`;
    b.innerHTML='<span class="badge">▶ PLAYING</span><span class="preset"></span><span class="nm"></span>';
    b.querySelector('.preset').textContent='Preset '+s.number;
    b.querySelector('.nm').textContent=s.id?(s.note||pretty(s.id)):'Sans affectation';
    b.onclick=async()=>{if(busy||b.disabled||!s.id)return;busy=true;touch();
      b.querySelector('.nm').textContent='Starting…';
      disableTargets(true);
      try{await api('/api/play/'+encodeURIComponent(s.id)+targetQuery()+'&volume='+$('vol').value);
        toast((s.note||pretty(s.id))+' → '+targetLabel())}
      catch(e){toast('Erreur : '+e.message)}
      finally{b.querySelector('.nm').textContent=s.note||pretty(s.id);busy=false;disableTargets(false);refresh()}};
    $('grid').appendChild(b)});
}
function updateVolumeSlider(slider,value){
  slider.value=value;
  slider.style.setProperty('--level',(Math.max(0,Math.min(70,Number(slider.value)))/70*100)+'%');
}
function buildSpeakers(speakers){
  speakers.forEach(s=>{
    const r=document.createElement('div');r.className='srow';r.id='sp-'+s.ip;
    r.innerHTML='<span class="st"></span><span class="nm"></span><input type="range" min="0" max="70">';
    r.querySelector('.nm').textContent=s.name.replace('ST - ','').replace('ST ','');
    const sl=r.querySelector('input');
    sl.setAttribute('aria-label','Volume : '+s.name);
    updateVolumeSlider(sl,s.volume??30);
    sl.oninput=()=>{updateVolumeSlider(sl,sl.value);touch();clearTimeout(spkT[s.ip]);
      spkT[s.ip]=setTimeout(()=>api('/api/volume/'+sl.value+'?ip='+s.ip).catch(e=>toast(e.message)),350)};
    $('spkList').appendChild(r)});
}
function render(st){
  const signatureSpeakers=JSON.stringify(st.speakers.map(s=>[s.name,s.ip]));
  const changed=signatureSpeakers!==speakerSignature;speakerSignature=signatureSpeakers;
  currentStatus=st;
  renderSpeakerConnectionNotice();
  if(changed){
    clearTimeout(volT);Object.values(spkT).forEach(clearTimeout);spkT={};
    $('target').replaceChildren();$('spkList').replaceChildren();
    if(selectedTarget!=='all'&&!st.speakers.some(s=>s.ip===selectedTarget))selectedTarget='';
  }
  if(!$('target').childElementCount){
    st.speakers.forEach(s=>{
      const b=document.createElement('button');b.className='target-btn';b.textContent=s.name;
      b.value=s.ip;b.onclick=()=>selectTarget(s.ip);$('target').appendChild(b);
    });
    const preferred=st.speakers.find(s=>s.name===st.default_speaker||s.ip===st.default_speaker);
    if(!selectedTarget)selectedTarget=preferred?.ip||st.speakers[0]?.ip||'all';
  }
  const target=selectedTarget;
  Array.from($('target').children).forEach(b=>{
    b.setAttribute('aria-pressed',String(b.value===target));
    const speaker=st.speakers.find(s=>s.ip===b.value);
    b.textContent=speaker.name;
    if(connectionWarning(speaker))b.appendChild(warningIcon(speaker));
  });
  $('targetAll').setAttribute('aria-pressed',String(target==='all'));
  $('spkList').hidden=target!=='all';
  const selected=st.speakers.find(s=>s.ip===target);
  const mapping=(selected?st.presets?.[selected.name]:st.presets?.default)||{};
  const stations=Array.from({length:6},(_,index)=>{
    const number=index+1;
    const station=st.stations.find(s=>s.id===mapping[number]);
    return {number,id:station?.id||null,note:station?.note||''};
  });
  const signature=JSON.stringify(stations);
  if(signature!==tileSignature){buildTiles(stations);tileSignature=signature}
  const unreachable=selected?selected.source==='unreachable':
    st.speakers.some(s=>s.source==='unreachable');
  Array.from($('grid').children).forEach(b=>{
    b.disabled=!b.stationId||unreachable;
    b.title=unreachable?(selected?'Enceinte injoignable':
      'Une enceinte est injoignable. Sélectionnez une enceinte disponible.'):'';
  });
  $('conn').classList.remove('off');$('connTxt').textContent=
    st.speakers.filter(s=>s.playStatus=='PLAY_STATE').length+'/'+st.speakers.length+' playing';
  if($('spkList').childElementCount===0)buildSpeakers(st.speakers);
  const active=st.speakers.filter(s=>(!selected||s.ip===selected.ip)&&s.playStatus==='PLAY_STATE');
  const live=active.length>0;
  const playingStation=active.length&&active.every(s=>s.station===active[0].station)?active[0].station:null;
  $('hero').classList.toggle('live',live);
  $('stop').hidden=!live;
  $('hTitle').textContent=live?(playingStation?(st.stations.find(s=>s.id===playingStation)?.note||pretty(playingStation)):active.length>1?'Plusieurs sources':active[0].title||'En lecture'):'Off';
  $('hSub').textContent=live?'En lecture : '+active.map(s=>s.name).join(', '):'Choisir une enceinte puis une station';
  Array.from($('grid').children).forEach(b=>b.classList.toggle('playing',live&&!!b.stationId&&b.stationId===playingStation));
  st.speakers.forEach(s=>{const r=$('sp-'+s.ip);if(!r)return;
    r.querySelector('.st').classList.toggle('on',s.playStatus=='PLAY_STATE');
    if(idle()&&s.volume!=null)updateVolumeSlider(r.querySelector('input'),s.volume)});
  if(idle()){const v=selected||st.speakers.find(s=>s.volume!=null);
    if(v&&v.volume!=null){updateVolumeSlider($('vol'),v.volume);$('vv').textContent=v.volume}}
}
async function refresh(){try{const [status]=await Promise.all([api('/api/status'),readButtons()]);render(status)}
  catch(e){$('conn').classList.add('off');$('connTxt').textContent='offline'}}
function selectedVolumeQuery(){return selectedTarget==='all'?'':'?ip='+encodeURIComponent(selectedTarget)}
$('targetAll').onclick=()=>selectTarget('all');
function setGroupVol(v){v=Math.max(0,Math.min(70,v));updateVolumeSlider($('vol'),v);$('vv').textContent=v;touch();
  clearTimeout(volT);const query=selectedVolumeQuery();volT=setTimeout(()=>api('/api/volume/'+v+query).catch(e=>toast(e.message)),350)}
$('vol').oninput=()=>setGroupVol(+$('vol').value);
$('vdn').onclick=()=>setGroupVol(+$('vol').value-5);
$('vup').onclick=()=>setGroupVol(+$('vol').value+5);
$('stop').onclick=async()=>{if(busy)return;busy=true;touch();disableTargets(true);
  try{await api('/api/stop'+targetQuery());toast('Arrêt : '+targetLabel())}
  catch(e){toast('Erreur : '+e.message)}finally{busy=false;disableTargets(false);refresh()}};
let settingsSaving=false,speakerDrafts=[],speakerEditing=null,speakerDeleting=null,speakerRevision=null;
let speakerRequest=0,speakerSearching=false;
const speakerInputs={name:'speakerName',ip:'speakerIp',deviceID:'speakerDeviceID',model:'speakerModel'};
function endSpeakerSearch(){
  speakerRequest++;speakerSearching=false;
  $('speakerScan').disabled=false;$('speakerLookup').disabled=false;$('speakerSave').disabled=false;
}
function fillSpeaker(speaker){
  for(const [key,id] of Object.entries(speakerInputs))$(id).value=speaker[key]||'';
  $('speakerName').focus();
}
function editSpeaker(speaker=null){
  if(settingsSaving)return;
  endSpeakerSearch();$('speakerDiscovery').hidden=!!speaker;
  $('speakerScanStatus').textContent='';$('speakerScanResults').replaceChildren();
  speakerEditing=speaker;
  for(const [key,id] of Object.entries(speakerInputs))$(id).value=speaker?.[key]||'';
  $('speakerEditTitle').textContent=speaker?'Modifier une enceinte':'Ajouter une enceinte';
  $('speakerEditError').textContent='';$('speakerEdit').showModal();$('speakerName').focus();
}
async function findSpeakers(fromIp=false){
  if(settingsSaving||speakerSearching)return;
  const request=++speakerRequest,ip=$('speakerIp').value.trim();
  speakerSearching=true;$('speakerScan').disabled=true;$('speakerLookup').disabled=true;$('speakerSave').disabled=true;
  $('speakerEditError').textContent='';$('speakerScanResults').replaceChildren();
  $('speakerScanStatus').textContent=fromIp?'Lecture des informations…':'Recherche en cours…';
  try{
    const result=await api(fromIp?'/api/speakers/identify':'/api/speakers/discover',{
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(fromIp?{ip}:{})});
    if(request!==speakerRequest)return;
    if(fromIp&&$('speakerIp').value.trim()!==ip){
      $('speakerScanStatus').textContent='Adresse modifiée pendant la recherche. Relancez la récupération des informations.';return;
    }
    const speakers=fromIp?[result.speaker]:result.speakers;
    $('speakerScanStatus').textContent=speakers.length?'Choisissez une enceinte.':
      'Aucune enceinte trouvée. Vérifiez le réseau ou renseignez une IP pour récupérer ses informations.';
    for(const speaker of speakers){
      const known=speakerDrafts.find(s=>(s.deviceID||'').toUpperCase()===speaker.deviceID.toUpperCase()&&s!==speakerEditing);
      const button=document.createElement('button');button.type='button';button.className='action discovery-choice';
      button.textContent=speaker.name+' — '+(speaker.model||'SoundTouch')+' — '+speaker.ip+(known?' — Déjà configurée ('+known.name+')':'');
      button.disabled=!!known;
      button.onclick=()=>{fillSpeaker(speaker);$('speakerScanResults').replaceChildren();
        $('speakerScanStatus').textContent='Informations récupérées. Vérifiez le nom puis enregistrez.'};
      $('speakerScanResults').appendChild(button);
      if(fromIp&&!known){
        // Preserve a local name entered before the lookup, especially while editing.
        const name=$('speakerName').value;
        fillSpeaker({...speaker,name:name||speaker.name});$('speakerScanResults').replaceChildren();
        $('speakerScanStatus').textContent='Informations récupérées. Vérifiez le nom puis enregistrez.';
      }
    }
  }catch(e){if(request===speakerRequest){$('speakerScanStatus').textContent='';$('speakerEditError').textContent=e.message}}
  finally{if(request===speakerRequest)endSpeakerSearch()}
}
$('speakerScan').onclick=()=>findSpeakers();
$('speakerLookup').onclick=()=>findSpeakers(true);
function renderSpeakerList(){
  renderSpeakerConnectionNotice();
  $('speakerEditors').replaceChildren();
  if(!speakerDrafts.length){
    const empty=document.createElement('p');empty.className='hint';
    empty.textContent='Aucune enceinte. Ajoutez votre première enceinte.';$('speakerEditors').appendChild(empty);
  }
  for(const speaker of [...speakerDrafts]
    .sort((a,b)=>a.name.localeCompare(b.name,'fr',{sensitivity:'base',numeric:true}))
    .filter(s=>matchesStation(s.name,$('speakerSearch').value))){
    const row=document.createElement('fieldset');row.className='speaker-editor station-row';row.disabled=settingsSaving;
    const name=document.createElement('strong');name.textContent=speaker.name;row.appendChild(name);
    if(connectionWarning(speaker))name.appendChild(warningIcon(speaker));
    const retry=document.createElement('button');retry.type='button';retry.className='action reconnect';
    retry.disabled=settingsSaving;
    retry.title='Connecter';
    retry.setAttribute('aria-label','Connecter '+speaker.name);
    retry.innerHTML='<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><path d="M14 10C14 9.44771 13.5523 9 13 9H12.5C9.46243 9 7 11.4624 7 14.5C7 17.5376 9.46243 20 12.5 20H17.5C20.5376 20 23 17.5376 23 14.5C23 12.0091 21.3441 9.90488 19.073 9.22823C18.5098 9.06042 18 9.52887 18 10.1166V10.1683C18 10.6659 18.3745 11.0735 18.8345 11.2634C20.1055 11.788 21 13.0395 21 14.5C21 16.433 19.433 18 17.5 18H12.5C10.567 18 9 16.433 9 14.5C9 12.567 10.567 11 12.5 11H13C13.5523 11 14 10.5523 14 10Z" fill="currentColor"/><path d="M11.5 4C14.5376 4 17 6.46243 17 9.5C17 12.5376 14.5376 15 11.5 15H11C10.4477 15 10 14.5523 10 14C10 13.4477 10.4477 13 11 13H11.5C13.433 13 15 11.433 15 9.5C15 7.567 13.433 6 11.5 6H6.5C4.567 6 3 7.567 3 9.5C3 10.9605 3.89451 12.212 5.16553 12.7366C5.62548 12.9264 6 13.3341 6 13.8317V13.8834C6 14.4711 5.49024 14.9396 4.92699 14.7718C2.65592 14.0951 1 11.9909 1 9.5C1 6.46243 3.46243 4 6.5 4H11.5Z" fill="currentColor"/></svg>';
    retry.onclick=async()=>{await checkButtons(speaker.name);await refresh();renderSpeakerList()};
    if(connectionWarning(speaker))row.appendChild(retry);
    const edit=document.createElement('button');edit.type='button';edit.className='action';
    edit.setAttribute('aria-label','Modifier '+speaker.name);edit.title='Modifier';
    edit.innerHTML='<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><path fill-rule="evenodd" clip-rule="evenodd" d="M21.1213 2.70705C19.9497 1.53548 18.0503 1.53547 16.8787 2.70705L15.1989 4.38685L7.29289 12.2928C7.16473 12.421 7.07382 12.5816 7.02986 12.7574L6.02986 16.7574C5.94466 17.0982 6.04451 17.4587 6.29289 17.707C6.54127 17.9554 6.90176 18.0553 7.24254 17.9701L11.2425 16.9701C11.4184 16.9261 11.5789 16.8352 11.7071 16.707L19.5556 8.85857L21.2929 7.12126C22.4645 5.94969 22.4645 4.05019 21.2929 2.87862L21.1213 2.70705ZM18.2929 4.12126C18.6834 3.73074 19.3166 3.73074 19.7071 4.12126L19.8787 4.29283C20.2692 4.68336 20.2692 5.31653 19.8787 5.70705L18.8622 6.72357L17.3068 5.10738L18.2929 4.12126ZM15.8923 6.52185L17.4477 8.13804L10.4888 15.097L8.37437 15.6256L8.90296 13.5112L15.8923 6.52185ZM4 7.99994C4 7.44766 4.44772 6.99994 5 6.99994H10C10.5523 6.99994 11 6.55223 11 5.99994C11 5.44766 10.5523 4.99994 10 4.99994H5C3.34315 4.99994 2 6.34309 2 7.99994V18.9999C2 20.6568 3.34315 21.9999 5 21.9999H16C17.6569 21.9999 19 20.6568 19 18.9999V13.9999C19 13.4477 18.5523 12.9999 18 12.9999C17.4477 12.9999 17 13.4477 17 13.9999V18.9999C17 19.5522 16.5523 19.9999 16 19.9999H5C4.44772 19.9999 4 19.5522 4 18.9999V7.99994Z" fill="currentColor"/></svg>';
    edit.onclick=()=>editSpeaker(speaker);row.appendChild(edit);
    const remove=document.createElement('button');remove.type='button';remove.className='action remove-speaker';
    remove.setAttribute('aria-label','Retirer '+speaker.name);remove.title='Retirer';
    remove.innerHTML='<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><path d="M4 6H20M16 6L15.7294 5.18807C15.4671 4.40125 15.3359 4.00784 15.0927 3.71698C14.8779 3.46013 14.6021 3.26132 14.2905 3.13878C13.9376 3 13.523 3 12.6936 3H11.3064C10.477 3 10.0624 3 9.70951 3.13878C9.39792 3.26132 9.12208 3.46013 8.90729 3.71698C8.66405 4.00784 8.53292 4.40125 8.27064 5.18807L8 6M18 6V16.2C18 17.8802 18 18.7202 17.673 19.362C17.3854 19.9265 16.9265 20.3854 16.362 20.673C15.7202 21 14.8802 21 13.2 21H10.8C9.11984 21 8.27976 21 7.63803 20.673C7.07354 20.3854 6.6146 19.9265 6.32698 19.362C6 18.7202 6 17.8802 6 16.2V6M14 10V17M10 10V17" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    remove.onclick=()=>{
      if(settingsSaving)return;speakerDeleting=speaker;
      $('speakerDeleteText').textContent='Retirer « '+speaker.name+' » de la configuration ? Sa lecture continue et ses presets sont conservés.';
      $('speakerDeleteError').textContent='';$('speakerDelete').showModal();
    };
    row.appendChild(remove);
    $('speakerEditors').appendChild(row);
  }
}
function closeSettings(){if(!settingsSaving)return closeDialog($('settings'))}
$('settingsClose').onclick=closeSettings;
$('settings').oncancel=e=>{e.preventDefault();return closeSettings()};
$('speakerAdd').onclick=()=>editSpeaker();
$('speakerSearch').oninput=renderSpeakerList;
$('speakerCancel').onclick=()=>{if(!settingsSaving){endSpeakerSearch();return closeDialog($('speakerEdit'))}};
$('speakerEdit').oncancel=e=>{e.preventDefault();return $('speakerCancel').onclick()};
$('speakerDeleteCancel').onclick=()=>{if(!settingsSaving)return closeDialog($('speakerDelete'))};
$('speakerDelete').oncancel=e=>{e.preventDefault();if(!settingsSaving)return closeDialog($('speakerDelete'))};
$('speakerDeleteConfirm').onclick=()=>writeSpeakers(speakerDrafts.filter(s=>s!==speakerDeleting),true);
$('speakerForm').onsubmit=async e=>{
  e.preventDefault();if(settingsSaving||speakerSearching)return;
  const values=Object.fromEntries(Object.entries(speakerInputs).map(([key,id])=>[key,$(id).value.trim()]));
  if(speakerDrafts.some(s=>s!==speakerEditing&&s.name===values.name)){
    $('speakerEditError').textContent='Une enceinte porte déjà ce nom.';return;
  }
  const speakers=speakerEditing?speakerDrafts.map(s=>s===speakerEditing?{...s,...values}:s):
    [...speakerDrafts,{original_name:'',...values}];
  return writeSpeakers(speakers);
};
function setSpeakerConfig(speakers){
  speakerDrafts=Object.entries(speakers).map(([name,speaker])=>({original_name:name,name,
    ip:speaker.ip,deviceID:speaker.deviceID,model:speaker.model||''}));
};
$('speakersOpen').onclick=async()=>{
  $('settingsMenu').hidePopover();
  if(busy)return;
  $('speakersOpen').disabled=true;
  try{
    const config=await api('/api/speakers');
    speakerRevision=config.revision;
    setSpeakerConfig(config.speakers);
    $('speakerSearch').value='';renderSpeakerList();
    $('settingsError').textContent='';$('settings').showModal();
  }catch(e){toast('Erreur : '+e.message)}finally{$('speakersOpen').disabled=false}
};
async function writeSpeakers(speakers,deleting=false){
  if(settingsSaving)return;
  const error=$(deleting?'speakerDeleteError':'speakerEditError');
  settingsSaving=true;error.textContent='';$('speakerFields').disabled=true;
  $('speakerAdd').disabled=true;$('speakerDeleteConfirm').disabled=true;renderSpeakerList();
  try{
    const result=await api('/api/speakers',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({speakers,revision:speakerRevision})});
    speakerRevision=result.revision;
    setSpeakerConfig(result.speakers);
    await closeDialog($(deleting?'speakerDelete':'speakerEdit'));
    toast(deleting?'Enceinte retirée':'Enceinte enregistrée');await refresh();
  }catch(e){error.textContent=e.message}
  finally{settingsSaving=false;$('speakerFields').disabled=false;$('speakerAdd').disabled=false;
    $('speakerDeleteConfirm').disabled=false;renderSpeakerList()}
}
let stationConfig=null,stationEditing=null,stationDeleting=null,stationSaving=false;
function stationLabel(id,value){return value&&typeof value==='object'?(value.note||pretty(id)):pretty(id)}
function editStation(id=null){
  stationEditing=id;
  const value=id===null?null:stationConfig.stations[id];
  $('stationName').value=id===null?'':stationLabel(id,value);
  $('stationUrl').value=typeof value==='string'?value:value?.url||'';
  $('stationEditTitle').textContent=id===null?'Ajouter une station':'Modifier une station';
  $('stationPresetsHint').hidden=id===null;
  $('stationEditError').textContent='';$('stationEdit').showModal();$('stationName').focus();
}
function renderStationList(){
  $('stationList').replaceChildren();
  if(!Object.keys(stationConfig.stations).length){
    const empty=document.createElement('p');empty.className='hint';empty.textContent='Aucune station. Ajoutez votre première station.';$('stationList').appendChild(empty);
  }
  for(const [id,value] of Object.entries(stationConfig.stations)
    .sort((a,b)=>stationLabel(...a).localeCompare(stationLabel(...b),'fr',{sensitivity:'base',numeric:true}))
    .filter(([id,value])=>matchesStation(stationLabel(id,value),$('stationSearch').value))){
    const row=document.createElement('fieldset');row.className='speaker-editor station-row';
    const name=document.createElement('strong');name.textContent=stationLabel(id,value);row.appendChild(name);
    const edit=document.createElement('button');edit.type='button';edit.className='action';edit.disabled=stationSaving;
    edit.setAttribute('aria-label','Modifier '+stationLabel(id,value));edit.title='Modifier';
    edit.innerHTML='<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><path fill-rule="evenodd" clip-rule="evenodd" d="M21.1213 2.70705C19.9497 1.53548 18.0503 1.53547 16.8787 2.70705L15.1989 4.38685L7.29289 12.2928C7.16473 12.421 7.07382 12.5816 7.02986 12.7574L6.02986 16.7574C5.94466 17.0982 6.04451 17.4587 6.29289 17.707C6.54127 17.9554 6.90176 18.0553 7.24254 17.9701L11.2425 16.9701C11.4184 16.9261 11.5789 16.8352 11.7071 16.707L19.5556 8.85857L21.2929 7.12126C22.4645 5.94969 22.4645 4.05019 21.2929 2.87862L21.1213 2.70705ZM18.2929 4.12126C18.6834 3.73074 19.3166 3.73074 19.7071 4.12126L19.8787 4.29283C20.2692 4.68336 20.2692 5.31653 19.8787 5.70705L18.8622 6.72357L17.3068 5.10738L18.2929 4.12126ZM15.8923 6.52185L17.4477 8.13804L10.4888 15.097L8.37437 15.6256L8.90296 13.5112L15.8923 6.52185ZM4 7.99994C4 7.44766 4.44772 6.99994 5 6.99994H10C10.5523 6.99994 11 6.55223 11 5.99994C11 5.44766 10.5523 4.99994 10 4.99994H5C3.34315 4.99994 2 6.34309 2 7.99994V18.9999C2 20.6568 3.34315 21.9999 5 21.9999H16C17.6569 21.9999 19 20.6568 19 18.9999V13.9999C19 13.4477 18.5523 12.9999 18 12.9999C17.4477 12.9999 17 13.4477 17 13.9999V18.9999C17 19.5522 16.5523 19.9999 16 19.9999H5C4.44772 19.9999 4 19.5522 4 18.9999V7.99994Z" fill="currentColor"/></svg>';
    edit.onclick=()=>editStation(id);row.appendChild(edit);
    const remove=document.createElement('button');remove.type='button';remove.className='action remove-speaker';remove.disabled=stationSaving;
    remove.setAttribute('aria-label','Supprimer '+stationLabel(id,value));remove.title='Supprimer';
    remove.innerHTML='<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><path d="M4 6H20M16 6L15.7294 5.18807C15.4671 4.40125 15.3359 4.00784 15.0927 3.71698C14.8779 3.46013 14.6021 3.26132 14.2905 3.13878C13.9376 3 13.523 3 12.6936 3H11.3064C10.477 3 10.0624 3 9.70951 3.13878C9.39792 3.26132 9.12208 3.46013 8.90729 3.71698C8.66405 4.00784 8.53292 4.40125 8.27064 5.18807L8 6M18 6V16.2C18 17.8802 18 18.7202 17.673 19.362C17.3854 19.9265 16.9265 20.3854 16.362 20.673C15.7202 21 14.8802 21 13.2 21H10.8C9.11984 21 8.27976 21 7.63803 20.673C7.07354 20.3854 6.6146 19.9265 6.32698 19.362C6 18.7202 6 17.8802 6 16.2V6M14 10V17M10 10V17" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    remove.onclick=async()=>{
      try{
        stationConfig=await api('/api/stations');renderStationList();
        if(!Object.hasOwn(stationConfig.stations,id))throw new Error('Station déjà supprimée.');
        stationDeleting=id;
        const usage=stationConfig.usages[id];
        const lines=['Supprimer « '+stationLabel(id,stationConfig.stations[id])+' » du catalogue ?'];
        for(const item of usage.direct)lines.push((item.scope==='default'?'Liste globale':item.scope)+' : preset(s) '+item.numbers.join(', '));
        if(usage.effective.length)lines.push('Enceintes concernées : '+usage.effective.map(item=>item.scope+' (preset(s) '+item.numbers.join(', ')+')').join(', '));
        lines.push(usage.direct.length?'Ces affectations seront supprimées. Les presets individuels concernés resteront sans affectation.':'Cette station n’est affectée à aucun preset.');
        $('stationDeleteText').textContent=lines.join('\n');$('stationDelete').showModal();
      }catch(e){$('stationsError').textContent=e.message}
    };row.appendChild(remove);$('stationList').appendChild(row);
  }
}
async function loadStations(){stationConfig=await api('/api/stations');renderStationList()}
$('stationsOpen').onclick=async()=>{
  if(busy)return;$('settingsMenu').hidePopover();
  try{$('stationSearch').value='';await loadStations();$('stationsError').textContent='';$('stationsSettings').showModal()}
  catch(e){toast('Erreur : '+e.message)}
};
$('stationsClose').onclick=()=>{if(!stationSaving)return closeDialog($('stationsSettings'))};
$('stationsSettings').oncancel=e=>{e.preventDefault();if(!stationSaving)return closeDialog($('stationsSettings'))};
$('stationAdd').onclick=()=>editStation();
$('stationSearch').oninput=renderStationList;
$('stationCancel').onclick=()=>{if(!stationSaving)return closeDialog($('stationEdit'))};
$('stationEdit').oncancel=e=>{e.preventDefault();if(!stationSaving)return closeDialog($('stationEdit'))};
$('stationDeleteCancel').onclick=()=>{if(!stationSaving)return closeDialog($('stationDelete'))};
$('stationDelete').oncancel=e=>{e.preventDefault();if(!stationSaving)return closeDialog($('stationDelete'))};
async function writeStation(payload){
  if(stationSaving)return;
  stationSaving=true;$('stationsError').textContent='';$('stationEditError').textContent='';$('stationFields').disabled=true;
  $('stationAdd').disabled=true;$('stationDeleteConfirm').disabled=true;renderStationList();
  try{
    await api('/api/stations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...payload,revision:stationConfig.revision})});
    await closeDialog($('stationEdit'));toast(payload.action==='delete'?'Station supprimée':'Station enregistrée');
    await loadStations();await refresh();
  }catch(e){$(payload.action==='delete'?'stationsError':'stationEditError').textContent=e.message;try{await loadStations()}catch{}}
  finally{stationSaving=false;$('stationFields').disabled=false;$('stationAdd').disabled=false;
    $('stationDeleteConfirm').disabled=false;await closeDialog($('stationDelete'));renderStationList()}
}
$('stationForm').onsubmit=e=>{e.preventDefault();return writeStation({action:'save',id:stationEditing,name:$('stationName').value,url:$('stationUrl').value})};
$('stationDeleteConfirm').onclick=()=>writeStation({action:'delete',id:stationDeleting,confirmed:true});
// Global and speaker lists are independent; missing slots stay unassigned.
const searchKey=value=>value.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLocaleLowerCase('fr');
const matchesStation=(label,query)=>searchKey(label).includes(searchKey(query.trim()));
let presetsSaving=false,presetDrafts={},presetStations=[],presetNumber=null;
function effectivePreset(scope,n){
  return presetDrafts[scope]?.[n];
}
function presetLabel(value){return value?(presetStations.find(s=>s.id===value)?.note||pretty(value)):'Sans affectation'}
function buildPresetEditors(){
  const scope=$('presetsScope').value;
  $('presetEditors').replaceChildren();
  $('presetsError').textContent=scope?'':'Ajoutez une enceinte dans les réglages pour configurer ses presets.';
  if(!scope)return;
  for(let n=1;n<=6;n++){
    const button=document.createElement('button');button.type='button';button.className='action preset-button';
    const number=document.createElement('strong');number.textContent='Preset '+n;button.appendChild(number);
    const name=document.createElement('span');name.textContent=presetLabel(effectivePreset(scope,n));button.appendChild(name);
    button.onclick=()=>{
      presetNumber=n;$('presetPickerTitle').textContent=(scope==='default'?'Liste globale':scope)+' — Preset '+n;
      $('presetSearch').value='';$('presetPickerError').textContent='';buildPresetChoices();$('presetPicker').showModal();
    };
    $('presetEditors').appendChild(button);
  }
}
function buildPresetChoices(){
  $('presetChoices').replaceChildren();
  const current=effectivePreset($('presetsScope').value,presetNumber);
  const choices=[{id:null,note:'Sans affectation'},...presetStations.filter(s=>matchesStation(s.note||pretty(s.id),$('presetSearch').value))];
  for(const station of choices){
    const button=document.createElement('button');button.type='button';button.className='action';button.textContent=station.note||pretty(station.id);
    button.disabled=presetsSaving;button.setAttribute('aria-pressed',String((current??null)===station.id));
    button.onclick=()=>savePreset(station.id);$('presetChoices').appendChild(button);
  }
  if(choices.length===1&&$('presetSearch').value.trim()){
    const empty=document.createElement('p');empty.className='hint';empty.textContent='Aucune station trouvée.';$('presetChoices').appendChild(empty);
  }
}
async function savePreset(station){
  if(presetsSaving)return;
  const scope=$('presetsScope').value,mapping={...presetDrafts[scope],[presetNumber]:station};
  if(scope==='default'&&station===null)delete mapping[presetNumber];
  presetsSaving=true;$('presetSearch').disabled=true;$('presetPickerError').textContent='';buildPresetChoices();
  try{
    await api('/api/presets',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({presets:{[scope]:mapping}})});
    presetDrafts[scope]=mapping;buildPresetEditors();await closePresetPicker();toast('Preset enregistré');await refresh();
  }catch(e){$('presetPickerError').textContent=e.message}
  finally{presetsSaving=false;$('presetSearch').disabled=false;buildPresetChoices()}
}
async function closePresetPicker(){
  await closeDialog($('presetPicker'));
  if($('presetsSettings').open)$('presetEditors').children[presetNumber-1]?.focus({preventScroll:true});
}
function closePresets(){if(!presetsSaving)return closeDialog($('presetsSettings'))}
$('presetsClose').onclick=closePresets;
$('presetsSettings').oncancel=e=>{e.preventDefault();return closePresets()};
$('presetsScope').onchange=buildPresetEditors;
$('presetSearch').oninput=buildPresetChoices;
$('presetPickerClose').onclick=()=>{if(!presetsSaving)return closePresetPicker()};
$('presetPicker').oncancel=e=>{e.preventDefault();if(!presetsSaving)return closePresetPicker()};
$('presetsOpen').onclick=async()=>{
  if(busy)return;$('presetsOpen').disabled=true;
  try{
    const config=await api('/api/presets');
    presetDrafts=Object.assign(Object.create(null),config.presets);
    presetStations=config.stations.sort((a,b)=>(a.note||pretty(a.id)).localeCompare(b.note||pretty(b.id),'fr',{sensitivity:'base',numeric:true}));
    $('presetsScope').replaceChildren();
    $('presetsScope').add(new Option('Liste globale — toutes les enceintes','default'));
    presetDrafts.default??={};
    for(const name of Object.keys(config.speakers)){
      $('presetsScope').add(new Option(name,name));presetDrafts[name]??={};
    }
    const selected=currentStatus?.speakers.find(s=>s.ip===selectedTarget)?.name;
    $('presetsScope').value=selected&&Object.hasOwn(config.speakers,selected)?selected:'default';
    buildPresetEditors();$('presetsSettings').showModal();
  }catch(e){toast('Erreur : '+e.message)}finally{$('presetsOpen').disabled=false}
};
$('buttonsLoading').oncancel=event=>event.preventDefault();
function startInterface(){refresh();checkButtons();setInterval(refresh,5000)}
startInterface();
