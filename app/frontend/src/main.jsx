import React, {useEffect, useMemo, useState, useRef} from 'react';
import {createRoot} from 'react-dom/client';
import './style.css';
import {SavedSettings,Benchmark} from './ExperimentTools.jsx';
import ImageViewer from './ImageViewer.jsx';

const GROUPS=[['panel','중앙 패널','발광 강도',30,.1],['cove_left','왼쪽 상단','W',4000,50],['cove_right','오른쪽 상단','W',4000,50],['corner_left','왼쪽 모서리','W',200,1],['corner_right','오른쪽 모서리','W',200,1]];
const ACTIVE=['queued','running'];
const RES={preview:[480,360],hd:[960,540],fhd:[1920,1080]};
const STATUS={queued:'대기 중',running:'렌더 중',completed:'완료',failed:'실패',cancelled:'취소'};
const clone=x=>JSON.parse(JSON.stringify(x));
const signature=x=>x?JSON.stringify({lights:GROUPS.map(([key])=>({key,color:x.lights[key].color_srgb.toUpperCase(),intensity:x.lights[key].enabled?x.lights[key].intensity:0})),resolution:x.resolution,samples:x.samples,ambient_gain:x.ambient_gain}):'';
const seconds=x=>typeof x==='number'?x.toFixed(2)+' s':'—';
const at=x=>x?new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(x)): '기준 이미지';
async function api(path,options={}){
 const response=await fetch('/api'+path,{...options,headers:{'Content-Type':'application/json',...options.headers}});
 const data=await response.json().catch(()=>({detail:'서버 연결을 확인해 주세요.'}));
 if(!response.ok)throw new Error(Array.isArray(data.detail)?data.detail.map(x=>x.msg).join(' · '):(data.detail||'요청을 처리하지 못했습니다.'));
 return data;
}
function Icon({name,size=18}){
 const paths={expand:<><path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/></>,sun:<><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/></>,play:<path d="m8 5 11 7-11 7Z"/>,reset:<><path d="M3 10a9 9 0 1 1 2 8M3 4v6h6"/></>,arrow:<path d="m8 5 7 7-7 7"/>,grid:<><rect x="3" y="4" width="7" height="16" rx="1"/><rect x="14" y="4" width="7" height="16" rx="1"/></>,wipe:<><rect x="3" y="4" width="18" height="16" rx="1"/><path d="M12 2v20m-5-10h10"/></>,download:<><path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/></>,close:<path d="m6 6 12 12M18 6 6 18"/>,clock:<><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,info:<><circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/></>};
 return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}
function ColorInput({value,onChange,label}){
 const [draft,setDraft]=useState(value);
 useEffect(()=>setDraft(value),[value]);
 return <div className="color-input"><label className="color-picker" style={{background:value}} title={`${label} 색상`}><input type="color" aria-label={`${label} 색상`} value={value} onChange={e=>onChange(e.target.value.toUpperCase())}/></label><input className="hex-input" aria-label={`${label} 색상 코드`} value={draft} maxLength={7} spellCheck={false} onChange={e=>setDraft(e.target.value)} onBlur={()=>{if(/^#[\da-f]{6}$/i.test(draft))onChange(draft.toUpperCase());else setDraft(value)}} onKeyDown={e=>{if(e.key==='Enter')e.currentTarget.blur()}}/></div>;
}
function LightControl({group,light,onChange}){
 const [key,label,unit,max,step]=group;
 return <section className={'light-control '+(!light.enabled?'off':'')}>
  <div className="light-heading"><span className="light-dot" style={{background:light.color_srgb}}/><h3>{label}</h3><button type="button" role="switch" aria-checked={light.enabled} aria-label={`${label} 켜기/끄기`} className="switch" onClick={()=>onChange({enabled:!light.enabled})}><span/></button></div>
  <div className="control-row"><ColorInput value={light.color_srgb} onChange={color_srgb=>onChange({color_srgb})} label={label}/><div className="intensity-input"><input aria-label={`${label} 밝기`} type="number" min="0" max={max} step={step} value={light.intensity} disabled={!light.enabled} onChange={e=>{const v=Number(e.target.value);if(Number.isFinite(v))onChange({intensity:Math.max(0,Math.min(max,v))})}}/><span>{unit}</span></div></div>
  <input className="intensity-range" aria-label={`${label} 밝기 슬라이더`} type="range" min="0" max={max} step={step} value={light.intensity} disabled={!light.enabled} onChange={e=>onChange({intensity:Number(e.target.value)})}/>
 </section>;
}
function EngineFrame({engine,job,dirty,onExpand}){
 const label=engine==='eevee'?'Eevee':'Cycles';
 const has=job?.images?.includes(engine);const working=job&&ACTIVE.includes(job.status)&&job.request.engines.includes(engine);
 const dims=job?.resolution_pixels||[480,360];
 return <article className="engine-frame">
  <div className="engine-heading"><div><span className={'engine-bullet '+engine}/><h2>{label}</h2><span className="engine-kind">{engine==='eevee'?'래스터화 · 간접광 근사':'패스트레이싱'}</span></div><span className="time-value">{seconds(job?.measurements?.[engine])}</span></div>
  <div className={'image-stage '+(dirty?'stale':'')} style={{aspectRatio:dims[0]+'/'+dims[1]}}>
   {has?<><button className="image-open" aria-label={`${label} 이미지 확대 보기`} onClick={()=>onExpand(engine)}><img src={`/api/jobs/${job.id}/images/${engine}.png`} alt={`${label} 갤러리 렌더 결과`}/><span className="image-open-hint"><Icon name="expand" size={13}/> 확대 보기</span></button>{dirty&&<span className="image-tag">이전 설정의 결과</span>}<a className="original-link" href={`/api/jobs/${job.id}/images/${engine}.png`} target="_blank" rel="noreferrer" title={`${label} 원본 이미지 열기`}><Icon name="download" size={15}/> 원본</a></>:<div className="empty-state">{working?<span className="large-spinner"/>:<span className="empty-outline"><Icon name="sun" size={30}/></span>}<strong>{working?(job.status==='queued'?'순서를 기다리고 있습니다':`${label} 결과를 준비하고 있습니다`):'같은 빛, 다른 계산'}</strong><p>{working?'완료된 엔진부터 결과가 표시됩니다.':'비교 렌더를 실행하면 이곳에 결과가 나타납니다.'}</p></div>}
  </div>
  <div className="engine-footer"><span>{has?`${dims[0]} × ${dims[1]} · ${job.request.samples} samples`:'아직 생성된 이미지 없음'}</span><span>{has?'생성 시 측정':'—'}</span></div>
 </article>;
}
function WipeView({job,onExpand}){
 const [position,setPosition]=useState(50);const dims=job.resolution_pixels;
 return <section className="wipe-view"><div className="wipe-images" style={{aspectRatio:dims[0]+'/'+dims[1]}}>
  <img src={`/api/jobs/${job.id}/images/cycles.png`} alt="Cycles 비교 이미지"/>
  <img src={`/api/jobs/${job.id}/images/eevee.png`} alt="Eevee 비교 이미지" style={{clipPath:`inset(0 ${100-position}% 0 0)`}}/>
  <div className="wipe-line" style={{left:position+'%'}}><span>↔</span></div><span className="wipe-label left">Eevee</span><span className="wipe-label right">Cycles</span>
  <input type="range" min="0" max="100" value={position} onChange={e=>setPosition(Number(e.target.value))} aria-label="두 엔진 이미지 비교 경계"/>
 </div><div className="wipe-caption"><span>경계를 움직여 반사와 빛의 번짐을 비교하세요.</span><button onClick={()=>onExpand('eevee')}>Eevee 확대</button><button onClick={()=>onExpand('cycles')}>Cycles 확대</button></div></section>;
}
function App(){
 const [bootstrap,setBootstrap]=useState(null),[request,setRequest]=useState(null),[display,setDisplay]=useState(null),[jobs,setJobs]=useState([]);
 const [error,setError]=useState(''),[notice,setNotice]=useState(''),[submitting,setSubmitting]=useState(false),[mode,setMode]=useState('side'),[info,setInfo]=useState(false),[tick,setTick]=useState(Date.now());
 async function initialize(){try{setError('');const data=await api('/bootstrap');setBootstrap(data);const initial=data.jobs.find(job=>job.kind==='render')||data.example||null;setRequest(initial&&initial.kind==='render'?clone(initial.request):data.defaults);setJobs(data.jobs);setDisplay(initial)}catch(e){setError(e.message)}}
 useEffect(()=>{initialize()},[]);
 const [benchmarkBusy,setBenchmarkBusy]=useState(false),[fresh,setFresh]=useState(false),[viewer,setViewer]=useState(null);
 const modalRef=useRef(null);
 useEffect(()=>{if(!info)return;const previous=document.activeElement;const modal=modalRef.current;const focusable=()=>[...modal.querySelectorAll('button,a[href],input,select,[tabindex="0"]')];focusable()[0]?.focus();function trap(event){if(event.key!=='Tab')return;const list=focusable();const first=list[0],last=list[list.length-1];if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus()}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()}}modal.addEventListener('keydown',trap);return()=>{modal.removeEventListener('keydown',trap);previous?.focus()}},[info]);
 const busy=display&&ACTIVE.includes(display.status);
 useEffect(()=>{if(!busy)return;let done=false;const id=setInterval(async()=>{try{const job=await api('/jobs/'+display.id);if(!done)setDisplay(job);if(!ACTIVE.includes(job.status)){const rows=await api('/jobs');if(!done)setJobs(rows)}}catch(e){if(!done)setError(e.message)}},800);return()=>{done=true;clearInterval(id)}},[display?.id,busy]);
 useEffect(()=>{if(!busy)return;const id=setInterval(()=>setTick(Date.now()),500);return()=>clearInterval(id)},[busy]);
 useEffect(()=>{if(!notice)return;const id=setTimeout(()=>setNotice(''),4500);return()=>clearTimeout(id)},[notice]);
 useEffect(()=>{function key(e){if(e.key==='Escape')setInfo(false)}window.addEventListener('keydown',key);return()=>window.removeEventListener('keydown',key)},[]);
 const dirty=useMemo(()=>display&&request&&signature(display.request)!==signature(request),[display,request]);
 function updateLight(key,patch){setRequest(r=>({...r,lights:{...r.lights,[key]:{...r.lights[key],...patch}}}))}
 function allWhite(){setRequest(r=>({...r,lights:Object.fromEntries(Object.entries(r.lights).map(([key,value])=>[key,{...value,color_srgb:'#FFFFFF'}]))}))}
 async function submit(){setSubmitting(true);setError('');try{const job=await api('/jobs?fresh='+fresh,{method:'POST',body:JSON.stringify(request)});setDisplay(job);setMode('side');setJobs(await api('/jobs'));if(job.cache_hit)setNotice('같은 설정의 저장된 결과를 불러왔습니다.')}catch(e){setError(e.message)}finally{setSubmitting(false)}}
 async function cancel(){try{setDisplay(await api('/jobs/'+display.id,{method:'DELETE'}));setJobs(await api('/jobs'))}catch(e){setError(e.message)}}
 async function select(job){try{const item=await api('/jobs/'+job.id);setDisplay(item);setRequest(item.kind==='example'?{...clone(item.request),engines:['eevee','cycles']}:clone(item.request));setMode('side')}catch(e){setError(e.message)}}
 if(!bootstrap||!request)return <div className="loading-page"><div className="brand">LIGHT<span>LAB</span></div>{error?<><p>{error}</p><button onClick={initialize}>다시 연결</button></>:<p>빛을 비교할 공간을 준비하고 있습니다.</p>}</div>;
 const canWipe=display?.images?.includes('eevee')&&display?.images?.includes('cycles');
 async function benchmarkComplete(record){setJobs(await api('/jobs'));const last=[...record.runs].reverse().find(run=>run.status==='completed');if(last){setDisplay(await api('/jobs/'+last.id));setMode('side')}}
 const runtime=bootstrap.runtime;const dims=RES[request.resolution];
 const elapsed=display?.started_at?Math.max(0,(tick-new Date(display.started_at).getTime())/1000).toFixed(1):'0.0';
 return <><div className="app-shell" inert={viewer?true:undefined}>
  <header className="topbar"><a href="/" className="brand" aria-label="LIGHT LAB 홈"><span className="brand-symbol"><i/><i/><i/></span>LIGHT<span>LAB</span></a><span className="brand-subtitle">빛을 비교하는 공간</span><div className="topbar-right"><span className="local-status"><i className={runtime.can_render?'ready':''}/>{runtime.can_render?'로컬 렌더 준비됨':'미리보기 연결됨'}</span><button className="icon-button" aria-label="비교 방식 설명" onClick={()=>setInfo(true)}><Icon name="info"/></button></div></header>
  <div className="workspace">
   <aside className="sidebar"><div className="sidebar-top"><div><p className="eyebrow">LIGHTING CONTROLS</p><h1>빛을 조절하세요.</h1></div><button className="icon-button" aria-label="기본 조명으로 초기화" onClick={()=>setRequest(clone(bootstrap.defaults))}><Icon name="reset"/></button></div>
    <div className="sidebar-scroll"><div className="preset-line"><span>각 광원을 독립적으로 조절합니다.</span><button onClick={allWhite}>모두 흰색</button></div>
     {GROUPS.map(group=><LightControl key={group[0]} group={group} light={request.lights[group[0]]} onChange={patch=>updateLight(group[0],patch)}/>)}
     <section className="settings"><p className="eyebrow">RENDER SETTINGS</p><div className="settings-grid"><label>해상도<select aria-label="해상도" value={request.resolution} onChange={e=>setRequest({...request,resolution:e.target.value})}><option value="preview">미리보기 · 480 × 360</option><option value="hd">HD · 960 × 540</option><option value="fhd">FHD · 1920 × 1080</option></select></label><label>샘플 수<select aria-label="샘플 수" value={request.samples} onChange={e=>setRequest({...request,samples:Number(e.target.value)})}>{[32,64,128,256,512].map(x=><option key={x} value={x}>{x} samples</option>)}</select></label></div><p className="settings-hint">두 엔진에 같은 샘플 수를 적용합니다.</p><label className="engine-select-label">실행할 엔진<select aria-label="실행할 엔진" value={request.engines.join(',')} onChange={e=>setRequest({...request,engines:e.target.value.split(',')})}><option value="eevee,cycles">Eevee + Cycles 비교</option><option value="eevee">Eevee만 빠르게 확인</option><option value="cycles">Cycles만 확인</option></select></label></section>
     <SavedSettings request={request} onLoad={value=>setRequest(clone(value))} initial={bootstrap.settings} api={api} onError={setError} onNotice={setNotice}/>
    </div><div className="sidebar-action"><label className="fresh-option"><input type="checkbox" checked={fresh} onChange={e=>setFresh(e.target.checked)}/>저장 결과 대신 새로 측정</label><button className="render-button" onClick={submit} disabled={submitting||benchmarkBusy||!runtime.can_render}>{submitting?<span className="small-spinner"/>:<Icon name="play" size={17}/>}<span>{submitting?'작업 등록 중':busy?'다음 렌더 대기열에 추가':'렌더링 비교하기'}</span><span className="button-arrow">↗</span></button><p>{runtime.can_render?'라이트맵 없이 계산 · 설정은 실행할 때 적용됩니다.':'일반 터미널에서 앱을 실행하면 렌더할 수 있습니다.'}</p></div>
   </aside>
   <main className="main-content"><div className="main-heading"><div><p className="eyebrow">ROOM STUDY / 01</p><h2>같은 공간, 두 가지 빛의 해석.</h2></div><span className="scene-tag">POLISHED STONE GALLERY</span></div>
    {!runtime.can_render&&<div className="runtime-banner"><Icon name="info" size={16}/><span>현재는 결과 미리보기 모드입니다. 새 렌더는 일반 터미널에서 <code>start_app.sh</code>로 앱을 실행해 주세요.</span></div>}
    {error&&<div className="error-banner" role="alert"><span>{error}</span><button aria-label="오류 메시지 닫기" onClick={()=>setError('')}><Icon name="close" size={16}/></button></div>}
    <section className="comparison-section"><div className="comparison-toolbar"><div className="result-state"><span className={'status-dot '+(busy?'pulse':'')}/><span>{busy?display.stage:dirty?'설정이 변경됐습니다':display?.kind==='example'?'확인한 기준 미리보기':display?.status==='completed'?'저장된 렌더 결과':'렌더 결과'}</span>{dirty&&<small>변경한 빛으로 다시 렌더해 주세요.</small>}</div><div className="result-actions">{display?.kind==='render'&&display?.status==='completed'&&<a className="result-download" href={`/api/jobs/${display.id}/download`}><Icon name="download" size={15}/> 결과 ZIP</a>}<div className="view-toggle" aria-label="이미지 비교 방식"><button className={mode==='side'?'active':''} aria-label="이미지 나란히 비교" onClick={()=>setMode('side')}><Icon name="grid" size={16}/></button><button className={mode==='wipe'?'active':''} aria-label="이미지 겹쳐 비교" disabled={!canWipe} onClick={()=>setMode('wipe')}><Icon name="wipe" size={16}/></button></div></div></div>
     {mode==='wipe'&&canWipe?<WipeView job={display} onExpand={engine=>setViewer({job:display,engine})}/>:<div className="comparison-grid"><EngineFrame engine="eevee" job={display} dirty={dirty} onExpand={engine=>setViewer({job:display,engine})}/><EngineFrame engine="cycles" job={display} dirty={dirty} onExpand={engine=>setViewer({job:display,engine})}/></div>}
     {busy&&<div className="job-progress"><div className="indeterminate-track"><span/></div><div><span><Icon name="clock" size={14}/>{display.stage} · {elapsed}초 경과</span><button onClick={cancel}>작업 취소</button></div></div>}
     {display?.status==='failed'&&<div className="job-error" role="alert"><strong>렌더를 완료하지 못했습니다.</strong><p>{display.error}</p><a href={`/api/jobs/${display.id}/log`} target="_blank" rel="noreferrer">실행 로그 보기 ↗</a></div>}
     {display?.status==='cancelled'&&<p className="cancel-note">작업을 취소했습니다. 완료된 부분 결과는 유지됩니다.</p>}
     <div className="comparison-note"><span><Icon name="info" size={14}/>표시된 시간은 결과 생성 당시 측정값입니다.</span><span>{display?.total_seconds?`전체 처리 ${display.total_seconds.toFixed(2)} s`:'라이트맵 베이크 없음 · Eevee 레이트레이싱 OFF'}</span></div>
    </section>
    <Benchmark request={request} initial={bootstrap.benchmarks?.[0]} api={api} canRender={runtime.can_render&&!busy} onError={setError} onBusy={setBenchmarkBusy} onComplete={benchmarkComplete}/>
    <section className="history"><div className="history-heading"><h2>최근 실험 <span>{jobs.length}</span></h2><span>설정과 결과가 이 컴퓨터에 저장됩니다.</span></div><div className="history-list">{jobs.slice(0,6).map(job=><button key={job.id} className={'history-item '+(display?.id===job.id?'selected':'')} onClick={()=>select(job)}><div className="history-swatches">{GROUPS.map(([key])=><i key={key} style={{background:job.request.lights[key].enabled?job.request.lights[key].color_srgb:'#d0d0ca'}}/>)}</div><div className="history-title"><strong>{job.label}</strong><span>{job.request.samples} samples · {at(job.created_at)}</span></div><span className={'history-status '+job.status}>{STATUS[job.status]||job.status}</span><Icon name="arrow" size={14}/></button>)}</div></section>
    <footer className="main-footer"><span>EEVEE <b>래스터화 + 간접광 근사</b></span><i/><span>CYCLES <b>패스트레이싱</b></span><button onClick={()=>setInfo(true)}>비교 기준 알아보기 ↗</button></footer>
   </main>
  </div>{notice&&<div className="toast" role="status">{notice}</div>}
  {info&&<div className="modal-backdrop" onClick={()=>setInfo(false)}><section ref={modalRef} className="info-modal" role="dialog" aria-modal="true" aria-labelledby="info-title" onClick={e=>e.stopPropagation()}><button className="modal-close icon-button" aria-label="설명 닫기" onClick={()=>setInfo(false)}><Icon name="close"/></button><p className="eyebrow">ABOUT THE EXPERIMENT</p><h2 id="info-title">화질과 계산 비용을 함께 관찰하세요.</h2><p>Eevee는 직접광과 공간별 색상 확산, 반사 프로브를 사용합니다. 라이트맵을 굽거나 렌더 중 레이트레이싱을 사용하지 않습니다.</p><p>Cycles는 같은 다섯 광원의 색상과 세기를 사용해 빛의 반사 경로를 계산합니다. Eevee용 간접광 근사와 패널 보조 광원은 Cycles 결과에 추가하지 않습니다.</p><p>같은 샘플 수가 같은 화질이나 계산량을 뜻하지는 않습니다. 엔진별 시간은 최종 렌더 호출 기준이며, 전체 처리 시간에는 앱의 작업 준비가 포함됩니다.</p><div className="info-fact">장면은 고정됩니다. 조명의 색과 세기만 바꾸며 차이를 관찰해 보세요.</div></section></div>}
 </div>{viewer&&<ImageViewer job={viewer.job} initialEngine={viewer.engine} onClose={()=>setViewer(null)}/>}</>;
}
createRoot(document.getElementById('root')).render(<App/>);
