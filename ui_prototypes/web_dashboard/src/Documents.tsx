import {useEffect,useRef,useState} from 'react';
import {api,Asset} from './api';

function Pdf({url}:{url:string}){
 const canvas=useRef<HTMLCanvasElement>(null),text=useRef<HTMLDivElement>(null),[page,setPage]=useState(1),[pages,setPages]=useState(0),[error,setError]=useState('');
 useEffect(()=>{
  let cancelled=false;let document:any;let loading:any;let render:any;let layer:any;
  (async()=>{try{
   const pdf=await import('pdfjs-dist');const worker=await import('pdfjs-dist/build/pdf.worker.min.mjs?url');pdf.GlobalWorkerOptions.workerSrc=worker.default;
   loading=pdf.getDocument({url,enableXfa:false});document=await loading.promise;if(cancelled){document.destroy();return;}setPages(document.numPages);
   const p=await document.getPage(page);const viewport=p.getViewport({scale:1.25});if(cancelled)return;
   const element=canvas.current!;element.width=viewport.width;element.height=viewport.height;
   render=p.render({canvas:element,viewport});await render.promise;if(cancelled)return;
   text.current!.replaceChildren();text.current!.style.setProperty('--scale-factor','1.25');
   layer=new pdf.TextLayer({textContentSource:await p.getTextContent(),container:text.current!,viewport});await layer.render();
  }catch(e){if(!cancelled)setError('PDF could not render. It may be encrypted, damaged or require a password. Original retained.');}})();
  return()=>{cancelled=true;render?.cancel();layer?.cancel();loading?.destroy();document?.destroy();};
 },[url,page]);
 return <section><div className="row"><button disabled={page<=1} onClick={()=>setPage(p=>p-1)}>←</button><span>Page {page}/{pages||'unknown'}</span><button disabled={page>=pages} onClick={()=>setPage(p=>p+1)}>→</button><a className="button" href={url} download>Save original PDF</a></div>{error&&<p>{error}</p>}<div className="pdf-scroll"><div className="pdf-page"><canvas ref={canvas}/><div ref={text} className="textLayer"/></div></div><small>Selectable text is available for native-text PDFs. Scans require a separate OCR pass.</small></section>;
}
function DocumentView({asset,kind}:{asset:Asset;kind:string}){
 const [body,setBody]=useState('');
 useEffect(()=>{const c=new AbortController();if(['txt','md'].includes(kind)&&asset.url)fetch(asset.url,{signal:c.signal}).then(r=>r.text()).then(setBody).catch(()=>setBody('Text could not be loaded.'));return()=>c.abort();},[asset.url,kind]);
 if(!asset.url)return <p>File unavailable.</p>;
 if(kind==='pdf')return <Pdf url={asset.url}/>;
 if(['txt','md'].includes(kind))return <pre className="document-text">{body}</pre>;
 if(asset.role==='video')return <video controls src={asset.url} preload="metadata" className="document-media"/>;
 if(asset.role==='audio')return <audio controls src={asset.url} preload="metadata"/>;
 return <img src={asset.url} alt="Local imported image" className="document-media"/>;
}
export default function Documents({notify}:{notify:(s:string)=>void}){
 const [items,setItems]=useState<any[]>([]),[active,setActive]=useState<any>(null),[busy,setBusy]=useState(false);
 const refresh=()=>api('documents').then(d=>setItems(d.items)).catch(e=>notify(String(e)));
 useEffect(()=>{refresh();},[]);
 async function upload(file:File){setBusy(true);try{const data=new FormData();data.append('file',file);const r=await fetch('/api/v2/documents',{method:'POST',headers:{'X-RedNote-Action':'explicit'},body:data});const d=await r.json();if(!r.ok)throw Error(d.error||'Import failed');setItems(d.items);notify('Local file registered. Exact file content reused when already imported.');}catch(e){notify(String(e));}finally{setBusy(false);}}
 return <section className="panel"><h2>Local media & documents</h2><p>PDF, text, images, GIFs, video and audio · originals copied into the local archive · maximum 100 MB per import</p><input aria-label="Import local media or document" type="file" disabled={busy} accept=".pdf,.txt,.md,.png,.jpg,.jpeg,.gif,.webp,.mp4,.webm,.mp3,.wav,.m4a" onChange={e=>{const file=e.target.files?.[0];if(file)upload(file);e.target.value='';}}/>{items.map(d=><button key={d.id} onClick={()=>setActive(active?.id===d.id?null:d)}>{d.name}</button>)}{active&&<><h3>{active.name}</h3><DocumentView asset={active.assets[0]} kind={active.kind}/></>}</section>;
}
