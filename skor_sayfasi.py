"""Etkileşimli skor sayfası: model seçilir, seçili modellerin ortalama puanına göre sıralanır."""

from __future__ import annotations

import html
import json
import math
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

CSS = """
:root{--bg:#f6f7f9;--kart:#fff;--yazi:#1c2128;--soluk:#5b6470;--cizgi:#e3e6ea;--vurgu:#0b6bcb;
--vurgu-bg:#e7f0fb;--pos:#16803c;--neg:#c62828;--orta:#8a6d00;--uyari:#8a5a00;--uyari-bg:#fff6e0;--bar:#d6dbe1}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0f1216;--kart:#171b21;--yazi:#e6e9ee;
--soluk:#9aa4b1;--cizgi:#2a3039;--vurgu:#5aa9ff;--vurgu-bg:#16273d;--pos:#3fbf6a;--neg:#ff6b6b;--orta:#e0b84a;
--uyari:#f0c060;--uyari-bg:#2b2412;--bar:#2f3640}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--yazi);
font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:760px;margin:0 auto;padding:20px 16px 64px}
a{color:var(--vurgu);text-decoration:none}
h1{font-size:22px;margin:4px 0 2px}.alt{color:var(--soluk);font-size:13px}
.ust{display:flex;justify-content:space-between;align-items:center;gap:8px;font-size:14px}
.banner{background:var(--uyari-bg);color:var(--uyari);padding:9px 12px;border-radius:8px;margin:12px 0;font-size:13px}
.kutu{background:var(--kart);border:1px solid var(--cizgi);border-radius:12px;padding:12px;margin-top:14px}
.grup{margin-top:10px}.grup:first-child{margin-top:0}
.grup-bas{display:flex;justify-content:space-between;align-items:center;font-size:13px;color:var(--soluk);margin-bottom:6px}
.grup-bas button{background:none;border:0;color:var(--vurgu);font:inherit;padding:2px 0;cursor:pointer}
.cipler{display:flex;flex-wrap:wrap;gap:6px}
.cip{border:1px solid var(--cizgi);background:var(--bg);color:var(--yazi);border-radius:999px;padding:6px 11px;
font:inherit;font-size:13px;cursor:pointer}
.cip[aria-pressed="true"]{background:var(--vurgu-bg);border-color:var(--vurgu);color:var(--vurgu);font-weight:600}
.secim-alt{display:flex;justify-content:space-between;align-items:center;margin-top:10px;font-size:13px;color:var(--soluk)}
.secim-alt button{background:none;border:0;color:var(--vurgu);font:inherit;cursor:pointer;padding:2px 0 2px 12px}
details.bilgi{margin-top:10px;font-size:13px}details.bilgi summary{cursor:pointer;color:var(--vurgu)}
details.bilgi dl{margin:8px 0 0}details.bilgi dt{font-weight:600;margin-top:8px}
details.bilgi dd{margin:2px 0 0;color:var(--soluk)}
.filtre{display:flex;gap:10px;align-items:center;margin-top:14px;flex-wrap:wrap}
.filtre input[type=search]{flex:1;min-width:150px;padding:9px 11px;border:1px solid var(--cizgi);border-radius:8px;
background:var(--kart);color:var(--yazi);font:inherit}
.filtre label{font-size:13px;color:var(--soluk);display:flex;gap:5px;align-items:center}
.sayac{font-size:12px;color:var(--soluk);margin:10px 2px 6px}
ol{list-style:none;margin:0;padding:0;background:var(--kart);border:1px solid var(--cizgi);border-radius:12px;overflow:hidden}
li{border-bottom:1px solid var(--cizgi)}li:last-child{border-bottom:0}
.satir{display:grid;grid-template-columns:30px 1fr auto;gap:10px;align-items:center;width:100%;
padding:11px 12px;background:none;border:0;color:inherit;font:inherit;text-align:left;cursor:pointer}
.sira{color:var(--soluk);font-size:12px;font-variant-numeric:tabular-nums}
.sembol{min-width:0}.sembol>b{display:block;font-size:15px}.sembol>.sirket{display:block;color:var(--soluk);font-size:12px;
white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:62vw}
.gun{font-size:13px;font-variant-numeric:tabular-nums;text-align:right;min-width:62px}
.puan{min-width:44px;text-align:center;font-weight:700;font-variant-numeric:tabular-nums;border-radius:8px;padding:4px 0;font-size:15px}
.p-yuk{color:var(--pos)}.p-dus{color:var(--neg)}.p-ort{color:var(--orta)}
.pos{color:var(--pos)}.neg{color:var(--neg)}
.detay{padding:4px 12px 14px 52px}
.detay .bilgiler{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:13px;color:var(--soluk);margin-bottom:8px}
.detay .bilgiler b{color:var(--yazi);font-weight:600}
.uyari{background:var(--uyari-bg);color:var(--uyari);font-size:12px;padding:6px 9px;border-radius:6px;margin-bottom:8px}
.mdl{display:grid;grid-template-columns:1fr 34px;gap:2px 10px;padding:7px 0;border-top:1px dashed var(--cizgi)}
.mdl:first-of-type{border-top:0}
.mdl .ad{font-size:13px}.mdl.secili .ad{font-weight:600}.mdl:not(.secili){opacity:.6}
.mdl .deg{font-size:12px;color:var(--soluk);grid-column:1}
.mdl .np{grid-row:1/3;grid-column:2;text-align:right;font-weight:700;font-variant-numeric:tabular-nums;align-self:center}
.cubuk{grid-column:1;height:5px;background:var(--bar);border-radius:3px;overflow:hidden;margin:3px 0}
.cubuk i{display:block;height:100%;background:var(--vurgu)}
.disari{display:inline-block;margin-top:8px;font-size:13px}
.daha{display:block;width:100%;margin-top:10px;padding:11px;border:1px solid var(--cizgi);border-radius:10px;
background:var(--kart);color:var(--vurgu);font:inherit;cursor:pointer}
.bos{padding:16px;color:var(--soluk);text-align:center}
footer{color:var(--soluk);font-size:12px;margin-top:24px;line-height:1.5}
@media (max-width:420px){.detay{padding-left:12px}.satir{grid-template-columns:24px 1fr auto;gap:8px}}
.katki{display:flex;flex-wrap:wrap;gap:4px 6px;margin-top:6px}
.katki .fazla{font-size:11.5px;color:var(--soluk);align-self:center}
.katki em{font-style:normal;font-size:11.5px;padding:1px 7px;border-radius:999px;background:var(--bg);border:1px solid var(--cizgi);
color:var(--soluk);white-space:nowrap}
.katki em b{font-weight:700;margin-left:3px}
.katki em.p-yuk b{color:var(--pos)}.katki em.p-dus b{color:var(--neg)}.katki em.p-ort b{color:var(--orta)}
.katki .ham{font-size:12px;color:var(--soluk)}
.cip:disabled{opacity:.45;cursor:not-allowed;text-decoration:line-through}
"""

JS = r"""
const D=JSON.parse(document.getElementById('veri').textContent);
const M=D.modeller, ANAHTAR='secim_'+D.kod;
const VAR=new Set(M.filter(m=>m.kapsam>=0.3).map(m=>m.id));
const hepsiIds=()=>M.filter(m=>VAR.has(m.id)).map(m=>m.id);
const fmtSayi=(x,b=2)=>x==null?'–':x.toLocaleString('tr-TR',{minimumFractionDigits:b,maximumFractionDigits:b});
const fmtYuzde=x=>x==null?'–':(x>0?'+':x<0?'−':'')+'%'+fmtSayi(Math.abs(x*100),2);
const fmtKat=x=>x==null?'–':fmtSayi(x,1)+'x';
const kisalt=x=>{if(x==null)return'–';for(const[s,e]of[[1e12,'Tn'],[1e9,'Mr'],[1e6,'Mn'],[1e3,'B']])if(Math.abs(x)>=s)return fmtSayi(x/s,1)+' '+e;return fmtSayi(x,0)};
const bicim=(m,x)=>x==null?'–':m.bicim==='yuzde'?fmtYuzde(x):m.bicim==='oran'?'%'+fmtSayi(x*100,1):m.bicim==='kat'?fmtKat(x):fmtSayi(x,2);
const sinif=p=>p==null?'':p>=70?'p-yuk':p<=30?'p-dus':'p-ort';
let secili=new Set(), adet=50, acik=new Set();
function yukle(){
  const h=new URLSearchParams(location.hash.slice(1)).get('m');
  let l=null; try{l=localStorage.getItem(ANAHTAR)}catch(e){}
  const k=(h??l??hepsiIds().join(',')).split(',').filter(x=>VAR.has(x));
  secili=new Set(k.length?k:hepsiIds());
}
function kaydet(){
  const v=[...secili].join(',');
  try{localStorage.setItem(ANAHTAR,v)}catch(e){}
  history.replaceState(null,'','#m='+v);
}
function cipler(){
  const kap=document.getElementById('modeller');kap.innerHTML='';
  for(const[g,ad]of Object.entries(D.gruplar)){
    const ms=M.filter(m=>m.grup===g), gecerli=ms.filter(m=>VAR.has(m.id)),
      sadece=gecerli.length===secili.size&&gecerli.every(m=>secili.has(m.id));
    const d=document.createElement('div');d.className='grup';
    d.innerHTML=`<div class="grup-bas"><span>${ad}</span><button data-g="${g}">${sadece?'Hepsini seç':'Sadece bunlar'}</button></div><div class="cipler"></div>`;
    for(const m of ms){const b=document.createElement('button');b.className='cip';b.textContent=m.ad;b.dataset.id=m.id;
      if(!VAR.has(m.id)){b.disabled=true;b.title='Bu borsa için yeterli veri yok';}
      b.setAttribute('aria-pressed',secili.has(m.id));d.querySelector('.cipler').appendChild(b);}
    kap.appendChild(d);
  }
  document.getElementById('secimSay').textContent=`${secili.size}/${VAR.size} model seçili · puan = seçililerin ortalaması`;
}
document.getElementById('modeller').addEventListener('click',e=>{
  const b=e.target.closest('button');if(!b)return;
  if(b.dataset.id){secili.has(b.dataset.id)?secili.delete(b.dataset.id):secili.add(b.dataset.id);}
  else if(b.dataset.g){const ms=M.filter(m=>m.grup===b.dataset.g&&VAR.has(m.id)).map(m=>m.id);
    secili=(ms.length===secili.size&&ms.every(i=>secili.has(i)))?new Set(hepsiIds()):new Set(ms);}
  adet=50;kaydet();cipler();ciz();
});
document.getElementById('hepsi').onclick=()=>{secili=new Set(hepsiIds());adet=50;kaydet();cipler();ciz();};
document.getElementById('temizle').onclick=()=>{secili=new Set();adet=50;kaydet();cipler();ciz();};
const idx=Object.fromEntries(M.map((m,i)=>[m.id,i]));
function bilesik(r){
  const ps=[...secili].map(i=>r.p[idx[i]]).filter(x=>x!=null);
  if(!secili.size||ps.length<Math.ceil(secili.size/2))return null;
  return Math.round(ps.reduce((a,b)=>a+b,0)/ps.length);
}
function detay(r){
  const sirali=[...M].sort((a,b)=>secili.has(b.id)-secili.has(a.id));
  let h=`<div class="bilgiler"><span>Kapanış <b>${fmtSayi(r.k)} ${D.para}</b></span><span>Günlük <b class="${r.g>0?'pos':r.g<0?'neg':''}">${fmtYuzde(r.g)}</b></span><span>RSI <b>${fmtSayi(r.rsi,0)}</b></span><span>Ort. işlem <b>${kisalt(r.oi)} ${D.para}</b></span></div>`;
  if(r.u)h+=`<div class="uyari">⚠ ${r.u}</div>`;
  for(const m of sirali){const p=r.p[idx[m.id]], v=r.h[idx[m.id]];
    h+=`<div class="mdl${secili.has(m.id)?' secili':''}"><span class="ad">${m.ad} <span class="alt">· ${m.kaynak.split(';')[0]}</span></span>
    <span class="np ${sinif(p)}">${p??'–'}</span><div class="cubuk"><i style="width:${p??0}%"></i></div>
    <span class="deg">${m.etiket}: ${bicim(m,v)}</span></div>`;}
  h+=`<a class="disari" href="${r.url}" target="_blank" rel="noopener">Grafiği aç ↗</a>`;
  return h;
}
function katki(r){
  const ids=[...secili];
  if(ids.length===1){const m=M[idx[ids[0]]];return `<span class="ham">${m.etiket}: ${bicim(m,r.h[idx[m.id]])}</span>`;}
  const ps=ids.map(i=>[M[idx[i]],r.p[idx[i]]]).filter(x=>x[1]!=null).sort((a,b)=>b[1]-a[1]);
  let h=ps.slice(0,3).map(([m,p])=>`<em class="${sinif(p)}">${m.ad}<b>${p}</b></em>`).join('');
  if(ps.length>3)h+=`<span class="fazla">+${ps.length-3} model</span>`;
  return h;
}
function ciz(){
  const q=document.getElementById('ara').value.trim().toLocaleUpperCase('tr');
  const lk=document.getElementById('likit').checked;
  let rs=D.hisseler.filter(r=>(!lk||r.l)&&(!q||(r.s+' '+r.a).toLocaleUpperCase('tr').includes(q)));
  rs.forEach(r=>r._b=bilesik(r));
  const yon=document.getElementById('sirala').value==='artan'?1:-1;
  rs.sort((a,b)=>(a._b==null)-(b._b==null)||yon*((a._b??0)-(b._b??0))||b.oi-a.oi);
  document.getElementById('sayac').textContent=`${rs.length.toLocaleString('tr-TR')} hisse`+(lk?'':` · ${D.hisseler.filter(r=>r.l).length.toLocaleString('tr-TR')} tanesi likit`);
  const ol=document.getElementById('liste');ol.innerHTML='';
  if(!secili.size){ol.innerHTML='<li class="bos">En az bir model seç.</li>';document.getElementById('daha').hidden=true;return;}
  if(!rs.length){ol.innerHTML='<li class="bos">Sonuç yok.</li>';document.getElementById('daha').hidden=true;return;}
  rs.slice(0,adet).forEach((r,i)=>{
    const li=document.createElement('li');
    li.innerHTML=`<button class="satir" aria-expanded="${acik.has(r.s)}"><span class="sira">${i+1}</span>
      <span class="sembol"><b>${r.s}${r.u?' <span style="display:inline;color:var(--uyari)">⚠</span>':''}</b><span class="sirket">${r.a}</span>
      <span class="katki">${katki(r)}</span></span>
      <span class="puan ${sinif(r._b)}">${r._b??'–'}</span></button>`;
    if(acik.has(r.s)){const d=document.createElement('div');d.className='detay';d.innerHTML=detay(r);li.appendChild(d);}
    li.firstElementChild.onclick=()=>{acik.has(r.s)?acik.delete(r.s):acik.add(r.s);ciz();};
    ol.appendChild(li);
  });
  const db=document.getElementById('daha');db.hidden=rs.length<=adet;db.textContent=`Daha fazla göster (${Math.min(50,rs.length-adet)})`;
}
document.getElementById('daha').onclick=()=>{adet+=50;ciz();};
['ara','likit','sirala'].forEach(i=>document.getElementById(i).addEventListener('input',()=>{adet=50;ciz();}));
yukle();cipler();ciz();
"""


def _sayi(x, basamak=4):
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else round(f, basamak)


def skor_html(kod: str, ayar: dict, m: pd.DataFrame, son_tarih, modeller: list, gruplar: dict,
              banner: str, rapor_adi: str, url_fn) -> str:
    g = m[m["gun_sayisi"] > 0].sort_values(["guncel", "ort_islem"], ascending=[False, False])
    guncel = m[m["guncel"]]
    kapsam = {md["id"]: float(guncel[f"p_{md['id']}"].notna().mean()) if len(guncel) else 0.0 for md in modeller}
    hisseler = []
    for sembol, r in g.iterrows():
        hisseler.append({
            "s": sembol.removesuffix(".IS"),
            "a": html.escape(str(r["ad"])[:48]),
            "k": _sayi(r["kapanis"]), "g": _sayi(r["gunluk"], 5), "rsi": _sayi(r["rsi"], 1),
            "oi": _sayi(r["ort_islem"], 0) or 0, "l": bool(r["likit"]),
            "u": html.escape(r["uyari"]) if r["uyari"] else "",
            "url": url_fn(sembol),
            "p": [None if pd.isna(r[f"p_{md['id']}"]) else int(r[f"p_{md['id']}"]) for md in modeller],
            "h": [_sayi(r[f"h_{md['id']}"], 5) for md in modeller],
        })
    veri = {
        "kod": kod, "para": ayar["para"], "gruplar": gruplar,
        "modeller": [{**{k: md[k] for k in ("id", "grup", "ad", "kaynak", "aciklama", "bicim", "etiket")},
                      "kapsam": round(kapsam[md["id"]], 3)} for md in modeller],
        "hisseler": hisseler,
    }
    js_veri = json.dumps(veri, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    bilgi = "".join(
        f"<dt>{html.escape(md['ad'])}</dt><dd>{html.escape(md['aciklama'])} <i>Kaynak: {html.escape(md['kaynak'])}.</i></dd>"
        for md in modeller)
    olusturma = datetime.now(ZoneInfo("Europe/Istanbul")).strftime("%d.%m.%Y %H:%M")
    return f"""<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>{ayar['ad']} skorları · {son_tarih:%d.%m.%Y}</title><style>{CSS}</style></head>
<body><main>
<div class="ust"><a class="geri-yer" href="../index.html">← Tüm raporlar</a><span><a href="../geriye_test_{kod}.html">Geriye dönük test</a> · <a href="{rapor_adi}">Günlük rapor →</a></span></div>
<h1>{ayar['ad']} · Skorlar</h1>
<div class="alt">Veri tarihi {son_tarih:%d.%m.%Y} · oluşturma {olusturma}</div>
{banner}
<section class="kutu">
  <div id="modeller"></div>
  <div class="secim-alt"><span id="secimSay"></span><span><button id="hepsi">Hepsi</button><button id="temizle">Temizle</button></span></div>
  <details class="bilgi"><summary>Modeller ne anlatıyor?</summary><dl>{bilgi}</dl></details>
</section>
<div class="filtre">
  <input type="search" id="ara" placeholder="Hisse ara">
  <label title="Son 20 gün ortalama işlem hacmi yeterli olanlar"><input type="checkbox" id="likit"> Sadece likit</label>
  <select id="sirala" aria-label="Sıralama"><option value="azalan">En yüksek puan</option><option value="artan">En düşük puan</option></select>
</div>
<div class="sayac" id="sayac"></div>
<ol id="liste"></ol>
<button class="daha" id="daha" hidden></button>
<footer>Her model hisseyi aynı borsadaki likit hisselerle karşılaştırır: 100 = o modele göre en iyi, 0 = en kötü.
Üstü çizili modeller için bu borsada yeterli veri yok.
Toplam puan seçtiğin modellerin ortalamasıdır; seçili modellerin yarısından azı hesaplanabiliyorsa puan verilmez.
Akademik bulgular ortalamalara dayanır ve yayımlandıktan sonra zayıflayabilir. Geçmişe bakıp en iyi görünen
model kombinasyonunu seçmek de yanıltıcı olabilir. Bu sayfa yatırım tavsiyesi değildir; veri kaynağı Yahoo Finance.</footer>
</main>
<script type="application/json" id="veri">{js_veri}</script>
<script>{JS}</script></body></html>"""
