import React,{useCallback,useEffect,useLayoutEffect,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import './image-viewer.css';

const MIN_SCALE=.1,MAX_SCALE=4;
const limit=(value,min,max)=>Math.max(min,Math.min(max,value));
const name=engine=>engine==='eevee'?'Eevee':'Cycles';

function Symbol({type}){
 const paths={close:<path d="m6 6 12 12M18 6 6 18"/>,plus:<path d="M12 5v14M5 12h14"/>,minus:<path d="M5 12h14"/>,full:<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/>,exit:<path d="M3 8h5V3m8 0v5h5M8 21v-5H3m18 0h-5v5"/>};
 return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[type]}</svg>;
}

export default function ImageViewer({job,initialEngine,onClose}){
 const root=useRef(null),stage=useRef(null),closeButton=useRef(null),drag=useRef(null);
 const [engine,setEngine]=useState(initialEngine),[size,setSize]=useState({width:0,height:0});
 const [natural,setNatural]=useState({width:job.resolution_pixels?.[0]||480,height:job.resolution_pixels?.[1]||360});
 const [view,setView]=useState({mode:'fit',scale:1,x:0,y:0}),[moving,setMoving]=useState(false);
 const [fullscreen,setFullscreen]=useState(false),[message,setMessage]=useState(''),[imageError,setImageError]=useState(false);
 const fitScale=Math.min(MAX_SCALE,Math.max(MIN_SCALE,Math.min((size.width-32)/natural.width,(size.height-32)/natural.height)));
 const scale=view.mode==='fit'?fitScale:view.scale;
 const src=`/api/jobs/${job.id}/images/${engine}.png`;
 const bounds=useCallback((zoom,x,y)=>{
  const maxX=Math.max(0,(natural.width*zoom-size.width)/2),maxY=Math.max(0,(natural.height*zoom-size.height)/2);
  return {x:limit(x,-maxX,maxX),y:limit(y,-maxY,maxY)};
 },[natural.width,natural.height,size.width,size.height]);
 const pan=bounds(scale,view.x,view.y);
 const canPan=natural.width*scale>size.width||natural.height*scale>size.height;
 const fit=useCallback(()=>setView({mode:'fit',scale:1,x:0,y:0}),[]);
 const zoom=useCallback((value,anchor={x:0,y:0},relative=false)=>{
  setView(previous=>{
   const oldScale=previous.mode==='fit'?fitScale:previous.scale;
   const next=limit(relative?oldScale*value:value,MIN_SCALE,MAX_SCALE);
   const current=bounds(oldScale,previous.x,previous.y),ratio=next/oldScale;
   return {mode:'custom',scale:next,...bounds(next,anchor.x-(anchor.x-current.x)*ratio,anchor.y-(anchor.y-current.y)*ratio)};
  });
 },[fitScale,bounds]);
 const switchEngine=useCallback(next=>{setEngine(next);fit();setImageError(false);setMessage('');drag.current=null;setMoving(false)},[fit]);
 const toggleFullscreen=useCallback(async()=>{
  setMessage('');
  try{
   if(document.fullscreenElement===root.current)await document.exitFullscreen();
   else if(root.current.requestFullscreen&&document.fullscreenEnabled)await root.current.requestFullscreen();
   else setMessage('이 브라우저는 전체 화면을 지원하지 않습니다. 현재 확대 보기로 감상할 수 있습니다.');
  }catch{setMessage('전체 화면을 열지 못했습니다. 브라우저에서 허용한 뒤 다시 시도해 주세요.')}
 },[]);

 useLayoutEffect(()=>{
  const element=stage.current;
  const resize=()=>setSize({width:element.clientWidth,height:element.clientHeight});
  resize();const observer=new ResizeObserver(resize);observer.observe(element);
  return()=>observer.disconnect();
 },[]);
 useEffect(()=>{
  const previous=document.activeElement,overflow=document.body.style.overflow,element=root.current;
  document.body.style.overflow='hidden';closeButton.current.focus({preventScroll:true});
  const changed=()=>setFullscreen(document.fullscreenElement===element);
  document.addEventListener('fullscreenchange',changed);
  return()=>{
   document.removeEventListener('fullscreenchange',changed);
   if(document.fullscreenElement===element)document.exitFullscreen().catch(()=>{});
   document.body.style.overflow=overflow;
   if(previous?.isConnected)previous.focus({preventScroll:true});
  };
 },[]);
 useEffect(()=>{
  const element=stage.current;
  const wheel=event=>{
   event.preventDefault();if(imageError)return;
   const rect=element.getBoundingClientRect();
   zoom(Math.exp(-limit(event.deltaY,-100,100)*.003),{x:event.clientX-rect.left-rect.width/2,y:event.clientY-rect.top-rect.height/2},true);
  };
  element.addEventListener('wheel',wheel,{passive:false});
  return()=>element.removeEventListener('wheel',wheel);
 },[zoom,imageError]);

 function keydown(event){
  const tag=event.target.tagName;
  if(tag==='INPUT'||tag==='SELECT')return;
  if(event.key==='Escape'){
   event.preventDefault();event.stopPropagation();
   if(document.fullscreenElement===root.current)document.exitFullscreen().catch(()=>{});else onClose();
  }else if(event.key==='Tab'){
   const controls=[...root.current.querySelectorAll('button:not(:disabled),a[href],[tabindex="0"]')];
   const first=controls[0],last=controls[controls.length-1];
   if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus()}
   else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()}
  }else if(event.key==='+'||event.key==='='){event.preventDefault();zoom(1.25,undefined,true)}
  else if(event.key==='-'){event.preventDefault();zoom(.8,undefined,true)}
  else if(event.key==='0'){event.preventDefault();fit()}
  else if(event.key==='1'){event.preventDefault();zoom(1)}
  else if(event.key.toLowerCase()==='f'){event.preventDefault();toggleFullscreen()}
  else if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key)&&canPan){
   event.preventDefault();
   const dx=event.key==='ArrowLeft'?60:event.key==='ArrowRight'?-60:0;
   const dy=event.key==='ArrowUp'?60:event.key==='ArrowDown'?-60:0;
   setView(previous=>({...previous,mode:'custom',scale,...bounds(scale,pan.x+dx,pan.y+dy)}));
  }
 }
 function pointerDown(event){
  if(event.button!==0||!canPan||imageError||drag.current)return;
  event.preventDefault();stage.current.focus({preventScroll:true});stage.current.setPointerCapture(event.pointerId);
  drag.current={id:event.pointerId,startX:event.clientX,startY:event.clientY,x:pan.x,y:pan.y,scale};setMoving(true);
 }
 function pointerMove(event){
  const current=drag.current;if(!current||event.pointerId!==current.id)return;
  setView({mode:'custom',scale:current.scale,...bounds(current.scale,current.x+event.clientX-current.startX,current.y+event.clientY-current.startY)});
 }
 function pointerEnd(event){
  if(drag.current?.id!==event.pointerId)return;
  drag.current=null;setMoving(false);
  if(stage.current.hasPointerCapture(event.pointerId))stage.current.releasePointerCapture(event.pointerId);
 }

 return createPortal(<div ref={root} className="image-viewer" role="dialog" aria-modal="true" aria-labelledby="viewer-title" aria-describedby="viewer-help" onKeyDown={keydown}>
  <header className="viewer-header">
   <div className="viewer-title"><h2 id="viewer-title">{name(engine)} <span>확대 보기</span></h2><p>{natural.width} × {natural.height} · {job.request.samples} samples{typeof job.measurements?.[engine]==='number'?` · 생성 당시 ${job.measurements[engine].toFixed(2)} s`:''}</p></div>
   <div className="viewer-header-actions">
    <button className="viewer-fullscreen" onClick={toggleFullscreen} aria-label={fullscreen?'전체 화면 종료':'전체 화면 보기'} title="전체 화면 전환 (F)"><Symbol type={fullscreen?'exit':'full'}/><span>{fullscreen?'전체 화면 종료':'전체 화면'}</span></button>
    <button ref={closeButton} onClick={onClose} className="viewer-close" aria-label="확대 보기 닫기" title="닫기 (Esc)"><Symbol type="close"/></button>
   </div>
  </header>
  <div className="viewer-toolbar">
   <div className="viewer-engines" role="group" aria-label="확대할 엔진">{['eevee','cycles'].filter(item=>job.images.includes(item)).map(item=><button key={item} onClick={()=>switchEngine(item)} aria-pressed={engine===item}>{name(item)}</button>)}</div>
   <div className="viewer-zoom" role="group" aria-label="이미지 확대 배율">
    <button onClick={()=>zoom(.8,undefined,true)} disabled={scale<=MIN_SCALE+.001||imageError} aria-label="이미지 축소" title="축소 (-)"><Symbol type="minus"/></button>
    <output aria-label="현재 확대 배율" aria-live="polite">{Math.round(scale*100)}%</output>
    <button onClick={()=>zoom(1.25,undefined,true)} disabled={scale>=MAX_SCALE-.001||imageError} aria-label="이미지 확대" title="확대 (+)"><Symbol type="plus"/></button>
    <button onClick={fit} aria-pressed={view.mode==='fit'} disabled={imageError} title="화면에 맞춤 (0)">맞춤</button>
    <button onClick={()=>zoom(1)} aria-pressed={view.mode==='custom'&&Math.abs(scale-1)<.001} disabled={imageError} title="원본 픽셀 크기 (1)">100%</button>
   </div>
  </div>
  <div ref={stage} className={'viewer-stage '+(canPan?'pannable ':'')+(moving?'panning':'')} tabIndex="0" role="region" aria-label="확대 이미지 영역" onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerEnd} onPointerCancel={pointerEnd} onLostPointerCapture={pointerEnd} onDoubleClick={()=>{if(!imageError){if(view.mode==='fit')zoom(2);else fit()}}}>
   {imageError?<p className="viewer-image-error" role="alert">이미지를 불러오지 못했습니다. 창을 닫고 결과를 다시 선택해 주세요.</p>:<img key={src} src={src} alt={`${name(engine)} 갤러리 확대 렌더 결과`} draggable="false" onLoad={event=>setNatural({width:event.currentTarget.naturalWidth,height:event.currentTarget.naturalHeight})} onError={()=>setImageError(true)} style={{width:natural.width,height:natural.height,transform:`translate(-50%, -50%) translate(${pan.x}px, ${pan.y}px) scale(${scale})`}}/>}
  </div>
  <footer className="viewer-footer"><p id="viewer-help">휠로 확대 · 드래그로 이동 <span>· + / − 확대 · 0 맞춤 · 1 원본 · F 전체 화면 · Esc 돌아가기</span></p><a href={src} target="_blank" rel="noreferrer">원본 열기 ↗</a></footer>
  {message&&<p className="viewer-message" role="status">{message}</p>}
 </div>,document.body);
}
