#!/usr/bin/env python3
"""경로 맵 (이 스킬 고유 스크립트) — 갈래 구조를 눌러 보는 단독 HTML. API 호출 없음 · 외부 자원 없음.

맵은 리포트(`render_report.py`)의 보조 파일이다. 해석 문장은 넣지 않고, 집계 결과(경로 수 · 줄기 수 · 검색량)만 보여 준다.
시드를 가운데 두고 왼쪽은 앞 검색, 오른쪽은 뒤 검색이다. 단계마다 상위 세 개를 먼저 보여 주고 나머지는 펼친다 [가정].

    python3 scripts/build_map.py --run <실행 폴더> -o <실행 폴더>/context_map.html
"""
import argparse, csv, html, json, pathlib, sys

CSS = """
:root{--bg:#f6f7f9;--panel:#fff;--fg:#1d2330;--mute:#5d6676;--line:#e3e6eb;--pri:#2f5bd3;--hot:#c2410c;--ours:#15803d;--comp:#a21caf;
--sans:"Pretendard","Noto Sans KR",system-ui,-apple-system,"Segoe UI",sans-serif;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--bg:#14171c;--panel:#1c2027;--fg:#e6e9ee;--mute:#9aa3b2;--line:#2c323c;--pri:#7f9cff;--hot:#fb923c;--ours:#4ade80;--comp:#e879f9;color-scheme:dark}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.6 var(--sans)}
.wrap{max-width:1280px;margin:0 auto;padding:20px 16px 40px;display:flex;flex-direction:column;gap:12px}
h1{font-size:20px;margin:0}.mute{color:var(--mute);font-size:12.5px}
.box{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.bound{border-left:4px solid var(--hot)}
.bar{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.bar input{flex:1;min-width:160px;font:13px var(--sans);background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:6px 10px}
button{font:600 12.5px var(--sans);border:1px solid var(--line);background:var(--panel);color:var(--fg);border-radius:6px;padding:5px 10px;cursor:pointer}
button:hover{border-color:var(--pri)}
.list{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:4px;max-height:180px;overflow:auto;margin-top:8px}
.list[hidden]{display:none}.list button{display:flex;justify-content:space-between;gap:6px;font-weight:400;text-align:left}
.list span:last-child{color:var(--mute);font-variant-numeric:tabular-nums}
.stage{height:min(70vh,560px);cursor:grab;touch-action:none;user-select:none}.stage svg{width:100%;height:100%;display:block}
.lk{fill:none;stroke:var(--pri);stroke-opacity:.35}.lk.on{stroke:var(--hot);stroke-opacity:1}
.nd{cursor:pointer}.nd circle{fill:var(--panel);stroke:var(--pri);stroke-width:1.5}.nd text{font:12px var(--sans);fill:var(--fg)}
.nd.seed circle{fill:var(--pri)}.nd.seed text{font-weight:700;font-size:13px}
.nd.more text{fill:var(--pri);font-weight:700;text-decoration:underline}.nd.more circle{fill:var(--pri)}
.nd.on circle{fill:var(--hot);stroke:var(--hot)}.nd.on text{fill:var(--hot);font-weight:700}.nd.same circle{stroke:var(--hot);stroke-width:3}
.nd.ours circle{stroke:var(--ours);stroke-width:3}.nd.comp circle{stroke:var(--comp);stroke-width:3}
.dim .lk:not(.on){stroke-opacity:.1}.dim .nd:not(.on):not(.same):not(.seed){opacity:.35}
.crumb{display:flex;flex-wrap:wrap;gap:4px;align-items:center}.crumb span{background:var(--bg);border:1px solid var(--line);border-radius:4px;padding:1px 7px}
.crumb span.cur{background:var(--pri);border-color:var(--pri);color:var(--panel);font-weight:700}.crumb i{font-style:normal;color:var(--mute)}
table{border-collapse:collapse;font-size:13px;margin-top:8px}th,td{border-bottom:1px solid var(--line);padding:4px 10px;text-align:left}td.n{text-align:right;font-variant-numeric:tabular-nums}
.tag{font-size:11px;border-radius:4px;padding:0 6px;border:1px solid var(--line);margin-left:4px}.tag.ours{color:var(--ours);border-color:var(--ours)}.tag.comp{color:var(--comp);border-color:var(--comp)}
"""

JS = r"""
(function(){
var D=JSON.parse(document.getElementById('d').textContent),svg=document.getElementById('map'),vp=document.getElementById('vp'),
    stage=svg.parentNode,panel=document.getElementById('sel'),KEEP=D.keep,DX=170,RH=26,open={},sel=null,view={k:1,x:0,y:0};
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function kids(id){var p=id.split('.'),n={c:D.tree[p[0]]};for(var i=1;i<p.length;i++)n=n.c[+p[i]];return n}
function shown(id,depth){var all=kids(id).c||[],n=open[id]?all.length:Math.min(all.length,KEEP),out=[];
  for(var i=0;i<n;i++){var cid=id+'.'+i;out.push({id:cid,node:all[i],depth:depth,kids:shown(cid,depth+1)})}
  if(all.length>KEEP)out.push({more:open[id]?0:all.length-n,parent:id,depth:depth,kids:[]});return out}
function place(items,row){items.forEach(function(t){if(t.kids.length){place(t.kids,row);t.y=(t.kids[0].y+t.kids[t.kids.length-1].y)/2}else t.y=row.v++})}
function chain(id){var p=id.split('.'),out=[];for(var i=2;i<=p.length;i++)out.push(p.slice(0,i).join('.'));return out}
function pathOf(id){var labs=chain(id).map(function(c){return kids(c).label});return id.charAt(1)==='r'?labs.reverse().concat([D.seed]):[D.seed].concat(labs)}
function draw(){var L=shown('pre',1),R=shown('post',1),a={v:0},b={v:0};place(L,a);place(R,b);
  var rows=Math.max(a.v,b.v,1),H=rows*RH,cy=H/2,oL=(rows-a.v)*RH/2,oR=(rows-b.v)*RH/2,tot=Math.max(1,D.total),on={},same=sel?kids(sel).k:null,links=[],nodes=[];
  if(sel)chain(sel).forEach(function(c){on[c]=1});
  function xy(t,sg,off){return[sg*t.depth*DX,off+t.y*RH+RH/2]}
  function walk(items,sg,off,from){items.forEach(function(t){var p=xy(t,sg,off),mx=(from[0]+p[0])/2,w=t.node?1+6*t.node.n/tot:1,hot=t.node&&on[t.id];
    links.push('<path class="lk'+(hot?' on':'')+'" d="M'+from[0]+','+from[1]+'C'+mx+','+from[1]+' '+mx+','+p[1]+' '+p[0]+','+p[1]+'" stroke-width="'+w.toFixed(1)+'"/>');
    var tx=p[0]+(sg>0?8:-8),anc=sg>0?'start':'end';
    if(!t.node){nodes.push('<g class="nd more" data-more="'+t.parent+'"><circle cx="'+p[0]+'" cy="'+p[1]+'" r="3"/><text x="'+tx+'" y="'+(p[1]+4)+'" text-anchor="'+anc+'">'+(t.more?'+'+t.more+'개 더':'접기')+'</text></g>');return}
    var m=D.kw[t.node.k]||{},cls='nd'+(hot?' on':'')+(same&&t.node.k===same&&!hot?' same':'')+(m.b==='ours'?' ours':m.b==='competitor'?' comp':''),lab=t.node.label;
    nodes.push('<g class="'+cls+'" data-id="'+t.id+'"><title>'+esc(lab)+' · 경로 '+t.node.n+' · 줄기 '+t.node.s+'</title><circle cx="'+p[0]+'" cy="'+p[1]+'" r="4"/><text x="'+tx+'" y="'+(p[1]+4)+'" text-anchor="'+anc+'">'+esc(lab.length>16?lab.slice(0,15)+'…':lab)+'</text></g>');
    walk(t.kids,sg,off,p)})}
  walk(L,-1,oL,[0,cy]);walk(R,1,oR,[0,cy]);
  nodes.push('<g class="nd seed"><circle cx="0" cy="'+cy+'" r="7"/><text x="0" y="'+(cy-12)+'" text-anchor="middle">'+esc(D.seed)+'</text></g>');
  vp.innerHTML=links.join('')+nodes.join('');svg.classList.toggle('dim',!!sel);
  try{var bb=vp.getBBox(),m=16;svg.setAttribute('viewBox',(bb.x-m)+' '+(bb.y-m)+' '+(bb.width+2*m)+' '+(bb.height+2*m))}catch(_){}}
function vol(k){var m=D.kw[k];return m&&m.v!==null&&m.v!==undefined?Number(m.v).toLocaleString():'–'}
function info(){if(!sel){panel.innerHTML='<span class="mute">맵에서 검색어를 누르면 시드부터 그 검색어까지의 경로가 여기에 나온다.</span>';return}
  var n=kids(sel),p=pathOf(sel),ks=chain(sel).map(function(c){return kids(c)});
  var h='<div class="crumb">'+p.map(function(x){return '<span'+(x===n.label?' class="cur"':'')+'>'+esc(x)+'</span>'}).join('<i>›</i>')+'</div>';
  h+='<p class="mute">이 지점을 지나는 경로 '+n.n+' / '+D.total+' · 서로 다른 줄기 '+n.s+(n.s===1&&n.n>1?' — 줄기가 하나라 «가장 흔한»으로 읽지 않는다':'')+'</p>';
  h+='<table><tr><th>검색어</th><th>경로 수</th><th>줄기 수</th><th>월 검색량(건)</th><th>묶인 표기</th></tr>'+ks.map(function(x){var m=D.kw[x.k]||{};
    return '<tr><td>'+esc(x.label)+(m.b==='ours'?'<span class="tag ours">우리</span>':m.b==='competitor'?'<span class="tag comp">경쟁</span>':'')+'</td><td class="n">'+x.n+'</td><td class="n">'+x.s+'</td><td class="n">'+vol(x.k)+'</td><td>'+esc(m.var||'')+'</td></tr>'}).join('')+'</table>';
  panel.innerHTML=h+'<p><button id="clr">선택 해제</button></p>'}
function pick(id){sel=id;draw();info()}
function apply(){vp.setAttribute('transform','translate('+view.x+','+view.y+') scale('+view.k+')')}
function pt(e){var p=svg.createSVGPoint();p.x=e.clientX;p.y=e.clientY;return p.matrixTransform(svg.getScreenCTM().inverse())}
svg.addEventListener('wheel',function(e){e.preventDefault();var p=pt(e),k=Math.min(8,Math.max(.3,view.k*Math.exp(-e.deltaY*.0015)));view.x=p.x-(p.x-view.x)*k/view.k;view.y=p.y-(p.y-view.y)*k/view.k;view.k=k;apply()},{passive:false});
var drag=null,moved=false;
svg.addEventListener('pointerdown',function(e){drag={p:pt(e),x:view.x,y:view.y,cx:e.clientX,cy:e.clientY};moved=false});
svg.addEventListener('pointermove',function(e){if(!drag)return;if(!moved&&Math.abs(e.clientX-drag.cx)+Math.abs(e.clientY-drag.cy)<6)return;moved=true;var p=pt(e);view.x=drag.x+(p.x-drag.p.x);view.y=drag.y+(p.y-drag.p.y);apply()});
['pointerup','pointercancel','pointerleave'].forEach(function(n){svg.addEventListener(n,function(){drag=null})});
svg.addEventListener('click',function(e){if(moved){moved=false;return}var t=e.target.closest('.nd');if(!t)return;
  var m=t.getAttribute('data-more'),id=t.getAttribute('data-id');if(m){if(open[m])delete open[m];else open[m]=1;draw();return}if(id)pick(id)});
panel.addEventListener('click',function(e){if(e.target.id==='clr'){sel=null;draw();info()}});
document.getElementById('fit').addEventListener('click',function(){view={k:1,x:0,y:0};apply()});
var q=document.getElementById('q'),list=document.getElementById('list'),index={};
['pre','post'].forEach(function(s){(function scan(id,depth){(kids(id).c||[]).forEach(function(c,i){var cid=id+'.'+i;if(!index[c.k]||depth<index[c.k].d)index[c.k]={id:cid,d:depth,label:c.label};scan(cid,depth+1)})})(s,1)});
function find(){var f=q.value.replace(/\s+/g,'').toLowerCase(),ks=Object.keys(index).filter(function(k){return !f||k.indexOf(f)>=0||index[k].label.replace(/\s+/g,'').toLowerCase().indexOf(f)>=0});
  ks.sort(function(a,b){return ((D.kw[b]||{}).v||0)-((D.kw[a]||{}).v||0)});
  list.innerHTML=ks.slice(0,200).map(function(k){return '<button data-k="'+esc(k)+'"><span>'+esc(index[k].label)+'</span><span>'+vol(k)+'</span></button>'}).join('');list.hidden=false}
q.addEventListener('input',find);document.getElementById('all').addEventListener('click',function(){if(list.hidden)find();else list.hidden=true});
list.addEventListener('click',function(e){var b=e.target.closest('[data-k]');if(!b)return;var id=index[b.getAttribute('data-k')].id,p=id.split('.');
  for(var i=1;i<p.length;i++)if(+p[i]>=KEEP)open[p.slice(0,i).join('.')]=1;list.hidden=true;pick(id)});
draw();info();
})();
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    run = pathlib.Path(a.run)
    j = lambda p: json.loads((run / p).read_text(encoding="utf-8"))
    tree, s, req = j("out/context_tree.json"), j("out/context_summary.json"), j("request.json")
    kw = {}
    for n in csv.DictReader(open(run / "out/context_nodes.csv", encoding="utf-8-sig")):
        kw[n["canonical"]] = {"v": int(n["volume_avg"]) if n["volume_avg"] else None, "b": n["brand"],
                              "var": n["variants"] if " | " in n["variants"] else ""}
    data = {"seed": tree["seed"], "tree": {"pre": tree["pre"], "post": tree["post"]}, "kw": kw, "total": s["seed_paths"], "keep": 3}
    e = html.escape
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    bound =(f"시드를 지나는 경로 중 받은 {s['returned_paths']}개(요청 {s.get('requested_limit') or '?'}개 · 현재 시점)를 그렸다. "
             + ("받은 경로 수가 요청 수와 같아 전체 경로가 아니다. " if s.get("truncated") else "")
             + "경로는 집계된 세션 인접 관계이며 한 사람의 여정이 아니다. 선 굵기는 경로 수이고 사람 수가 아니다.")
    page = (f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>경로 맵 — {e(tree["seed"])}</title><style>{CSS}</style></head><body><div class="wrap">'
            f'<div><div class="mute">read-search-context · {e(str(req.get("gl")))} · 조회일 {e(str(req.get("queried_at")))}</div><h1>경로 맵 — {e(tree["seed"])}</h1></div>'
            f'<div class="box bound">{e(bound)}</div>'
            f'<div class="box"><div class="bar"><input id="q" type="search" placeholder="검색어 찾기" aria-label="검색어 찾기" autocomplete="off">'
            f'<button id="all" type="button">목록</button><button id="fit" type="button">맞춤</button>'
            f'<span class="mute">왼쪽 = 앞 검색 · 오른쪽 = 뒤 검색 · 네 걸음까지 · 단계마다 상위 세 개 먼저 [가정] · 휠로 확대, 끌어서 이동</span></div>'
            f'<div class="list" id="list" hidden></div></div>'
            f'<div class="box stage"><svg id="map" role="img" aria-label="경로 맵" preserveAspectRatio="xMidYMid meet"><g id="vp"></g></svg></div>'
            f'<div class="box" id="sel"></div>'
            f'<p class="mute">검색량 «–» 은 값이 돌아오지 않은 검색어다(0 이 아니다) [데이터 공백]. 해석은 리포트(report.html)에 있다.</p></div>'
            f'<script type="application/json" id="d">{blob}</script><script>{JS}</script></body></html>')
    pathlib.Path(a.out).write_text(page, encoding="utf-8")
    print(f"경로 맵 → {a.out} · 검색어 묶음 {len(kw)}개")
    return 0


if __name__ == "__main__":
    sys.exit(main())
