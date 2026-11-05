import React,{useState,useEffect,useRef} from 'react';

export function SavedSettings({request,onLoad,initial,api,onError,onNotice}){
 const [settings,setSettings]=useState(initial||[]),[name,setName]=useState('내 조명 실험'),[saving,setSaving]=useState(false);
 const file=useRef(null);
 async function store(document){setSaving(true);try{const saved=await api('/settings',{method:'POST',body:JSON.stringify(document)});setSettings(await api('/settings'));onNotice('조명과 렌더 설정을 저장했습니다.');return saved}catch(e){onError(e.message);return null}finally{setSaving(false)}}
 async function importFile(event){const selected=event.target.files?.[0];event.target.value='';if(!selected)return;try{if(selected.size>64000)throw new Error('설정 파일은 64KB 이하여야 합니다.');const document=JSON.parse(await selected.text());const saved=await store(document);if(saved){onLoad(saved.request);setName(saved.name);onNotice('파일의 설정을 불러와 저장했습니다.')}}catch(e){onError('설정 파일을 확인하세요. '+e.message)}}
 function exportCurrent(){const blob=new Blob([JSON.stringify({format_version:1,name:name.trim()||'LIGHT LAB',request},null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const anchor=document.createElement('a');anchor.href=url;anchor.download='light-lab-settings.json';anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
 return <section className="saved-settings"><p className="eyebrow">SAVED LIGHTING</p><label>설정 이름<input aria-label="설정 이름" value={name} maxLength={64} onChange={e=>setName(e.target.value)}/></label><div className="tool-actions"><button disabled={saving||!name.trim()} onClick={()=>store({format_version:1,name:name.trim(),request})}>{saving?'저장 중':'현재 설정 저장'}</button><button onClick={exportCurrent}>JSON 내보내기</button><button disabled={saving} onClick={()=>file.current.click()}>JSON 가져오기</button><input ref={file} type="file" accept=".json,application/json" aria-label="설정 JSON 파일" hidden onChange={importFile}/></div><label>저장한 설정<select aria-label="저장한 설정 불러오기" value="" onChange={e=>{const selected=settings.find(s=>s.id===e.target.value);if(selected){onLoad(selected.request);setName(selected.name);onNotice('설정을 불러왔습니다. 렌더를 실행하면 결과에 적용됩니다.')}}}><option value="">선택해서 불러오기 ({settings.length})</option>{settings.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label></section>;
}

const sec=value=>typeof value==='number'?value.toFixed(3)+' s':'—';
export function Benchmark({request,initial,api,canRender,onError,onBusy,onComplete}){
 const [record,setRecord]=useState(initial||null),[starting,setStarting]=useState(false);
 const running=record?.status==='running';
 useEffect(()=>{onBusy(running||starting)},[running,starting]);
 useEffect(()=>{if(!running)return;let stopped=false;const timer=setInterval(async()=>{try{const next=await api('/benchmarks/'+record.id);if(stopped)return;setRecord(next);if(next.status!=='running')await onComplete(next)}catch(e){if(!stopped)onError(e.message)}},1000);return()=>{stopped=true;clearInterval(timer)}},[running,record?.id]);
 async function start(){setStarting(true);try{setRecord(await api('/benchmarks',{method:'POST',body:JSON.stringify(request)}))}catch(e){onError(e.message)}finally{setStarting(false)}}
 async function cancel(){try{const next=await api('/benchmarks/'+record.id,{method:'DELETE'});setRecord(next);await onComplete(next)}catch(e){onError(e.message)}}
 const valid=request.engines.length===2;
 return <section className="benchmark-section"><div className="benchmark-heading"><div><p className="eyebrow">PERFORMANCE STUDY</p><h2>같은 조건으로 반복 측정</h2></div>{running?<button onClick={cancel}>반복 측정 취소</button>:<button disabled={!canRender||!valid||starting} onClick={start}>{starting?'등록 중':'준비 1회 + 본측정 3회'}</button>}</div><p className="benchmark-note">선택한 설정으로 두 엔진을 총 4회 새로 렌더합니다. 준비 1회를 제외한 3회의 중앙값·최솟값·최댓값을 비교합니다.</p>{!valid&&<p className="benchmark-note">실행할 엔진에서 Eevee + Cycles 비교를 선택하세요.</p>}
 {record&&<><div className="benchmark-status" role="status"><strong>{record.status==='running'?`측정 진행 중 · ${record.completed_count}/4 완료`:record.status==='completed'?'반복 측정 완료':record.status==='failed'?'측정 실패 · 남은 실행 중단':'측정 취소'}</strong><span>{record.request.resolution.toUpperCase()} · {record.request.samples} samples</span><a href={`/api/benchmarks/${record.id}/download`}>측정 CSV ↓</a></div>{record.runs.some(run=>run.error)&&<p className="benchmark-note" role="alert">{record.runs.find(run=>run.error).error}</p>}
 <div className="benchmark-table-wrap"><table className="benchmark-table"><caption>엔진별 실제 렌더 시간 · 준비 실행 제외</caption><thead><tr><th>엔진</th><th>중앙값</th><th>최소</th><th>최대</th></tr></thead><tbody>{['eevee','cycles'].map(engine=><tr key={engine}><th>{engine==='eevee'?'Eevee':'Cycles'}</th><td>{sec(record.statistics[engine]?.median)}</td><td>{sec(record.statistics[engine]?.min)}</td><td>{sec(record.statistics[engine]?.max)}</td></tr>)}</tbody></table></div></>}
 <p className="benchmark-footnote">캐시 재사용 없음 · 매회 Blender 새 실행 · Eevee → Cycles 순서 고정 · 실시간 FPS 측정 아님</p></section>;
}
