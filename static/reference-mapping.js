// Kept in sync with app.reference_order.resolve_references; exercised by shared fixtures.
function mediaReferenceTokens(binding) {
  const tokens=[...new Set(binding.tokens||[])];
  return tokens.filter(t=>/^REF_(IMG|AUD|VID)(0|[1-9]\d*)$/.test(t))
    .sort((a,b)=>Number(a.slice(7))-Number(b.slice(7))||a.localeCompare(b))
    .concat(tokens.includes('Drive_AUD')?['Drive_AUD']:[]);
}
function mapReferenceMedia(binding,assets) {
  const slots=mediaReferenceTokens(binding), mapping={}, errors=[];
  const prefixes={image:'REF_IMG',audio:'REF_AUD',video:'REF_VID'};
  for(const id of binding.image_order||[]) {
    if(!id)continue;
    const asset=assets.find(a=>a.id===id);
    if(!asset){errors.push(`找不到素材：${id}`);continue;}
    const suffix=String(asset.filename||asset.original_name||'').split('.').pop().toLowerCase();
    const kind=asset.media_type||(['wav','mp3','flac','m4a','aac','ogg'].includes(suffix)?'audio':['mp4','mov','mkv','webm','avi'].includes(suffix)?'video':'image');
    const kinds=kind==='character'?['image','audio']:[kind];
    let used=false;
    if(kinds.includes('audio')&&slots.includes('Drive_AUD')&&!mapping.Drive_AUD){mapping.Drive_AUD=id;used=true;}
    const pair=kind==='character'?slots.find(s=>s.startsWith('REF_IMG')&&!mapping[s]&&slots.includes(s.replace('REF_IMG','REF_AUD'))&&!mapping[s.replace('REF_IMG','REF_AUD')]):null;
    if(pair){mapping[pair]=id;mapping[pair.replace('REF_IMG','REF_AUD')]=id;used=true;}
    else for(const media of kinds){const token=slots.find(s=>s.startsWith(prefixes[media])&&!mapping[s]);if(token){mapping[token]=id;used=true;}}
    if(!used)errors.push(`素材 ${asset.original_name||id} 沒有可用的相容插槽`);
  }
  return {mapping, errors, missing:slots.filter(s=>!mapping[s])};
}
if(typeof module!=='undefined')module.exports={mediaReferenceTokens,mapReferenceMedia};
