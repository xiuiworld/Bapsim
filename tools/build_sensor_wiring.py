"""Build an editable Fritzing breadboard plan and a local visual guide.

No firmware changes. Custom parts are drawing/connection models, not simulation
models or PCB footprints. Coordinates refer to the top printed breadboard scale.
"""
from pathlib import Path
from collections import defaultdict
from html import escape
import json
import zipfile
import xml.etree.ElementTree as E

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/hardware/wiring'
OUT.mkdir(parents=True, exist_ok=True)
PARTS = OUT / 'parts'
PARTS.mkdir(exist_ok=True)
RED, GND = '#d13b45', '#263648'
COLORS = ['#ca7916', '#147cba', '#8d55bd', '#19856a', '#c34f8c', '#63790f']

def text(x,y,s,size=10,fill='#24374a',anchor='start'):
    return f'<text x="{x}" y="{y}" font-family="Malgun Gothic,Arial,sans-serif" font-size="{size}" fill="{fill}" text-anchor="{anchor}">{escape(str(s))}</text>'

def rect(x,y,w,h,fill,rx=0,stroke='none'):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}"/>'

def line(x,y,x2,y2,color,width=2):
    return f'<line x1="{x}" y1="{y}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{width}" stroke-linecap="round"/>'

def svg(body,w,h,layer='breadboard'):
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{w/90}in" height="{h/90}in" viewBox="0 0 {w} {h}"><g id="{layer}">{body}</g></svg>'

class Part:
    def __init__(self,key,title,w,h,body,pins,buses=(),breadboard=False):
        self.key,self.title,self.w,self.h,self.body,self.pins,self.buses=key,title,w,h,body,pins,list(buses)
        self.layer='breadboardbreadboard' if breadboard else 'breadboard'
        self.mid='Bapsim_'+key+'_v1'
        self.draw=body
        for name,(x,y) in pins.items():
            self.draw+=f'<circle id="{name}pin" cx="{x}" cy="{y}" r="2.2" fill="#d1aa55" stroke="#374151" stroke-width="0.6"/>'
        self.svg=svg(self.draw,w,h,self.layer)
        m=E.Element('module',{'moduleId':self.mid,'fritzingVersion':'0.9.4'})
        for tag,value in [('version','1'),('author','Bapsim'),('title',title),('label',key),('date','2026-10-06'),('description','Breadboard wiring illustration only. Not a PCB footprint or simulator.')]:
            E.SubElement(m,tag).text=value
        props=E.SubElement(m,'properties')
        E.SubElement(props,'property',{'name':'family'}).text='Bapsim '+key
        views=E.SubElement(m,'views')
        for view in ['iconView','breadboardView']:
            v=E.SubElement(views,view)
            ls=E.SubElement(v,'layers',{'image':f'breadboard/{key}.svg'})
            E.SubElement(ls,'layer',{'layerId':self.layer})
        cs=E.SubElement(m,'connectors')
        for name in pins:
            c=E.SubElement(cs,'connector',{'id':name,'name':name,'type':'female' if breadboard else 'male'})
            E.SubElement(c,'description').text=name
            v=E.SubElement(E.SubElement(c,'views'),'breadboardView')
            E.SubElement(v,'p',{'layer':self.layer,'svgId':name+'pin'})
        bs=E.SubElement(m,'buses')
        for n,members in enumerate(self.buses):
            b=E.SubElement(bs,'bus',{'id':f'bus{n}'})
            for name in members:E.SubElement(b,'nodeMember',{'connectorId':name})
        self.fzp=E.tostring(m,encoding='utf-8',xml_declaration=True)
        (PARTS/f'{key}.fzp').write_bytes(self.fzp)
        (PARTS/f'{key}.svg').write_text(self.svg,encoding='utf-8')
        with zipfile.ZipFile(PARTS/f'{key}.fzpz','w',zipfile.ZIP_DEFLATED) as z:
            z.writestr('part.'+key+'.fzp',self.fzp)
            z.writestr('svg.breadboard.'+key+'.svg',self.svg)

# Header orientation matches the user's photo: USB at top, P3 left of P2.
bp={}
b=rect(0,0,240,550,'#184876',12)+text(120,55,'nRF54LM20-DK',17,'white','middle')+text(120,76,'PCA10184  rev 0.7.0',10,'#d6e9fa','middle')
b+=rect(52,0,45,30,'#c3cbd2',3)+rect(115,0,45,30,'#c3cbd2',3)
b+=text(74,42,'DEBUG USB',7,'white','middle')+text(139,42,'nRF USB',7,'white','middle')
b+=rect(42,100,45,45,'#27313a',3)+rect(158,115,38,38,'#27313a',3)
b+=text(120,194,'PORT1',15,'white','middle')
for group,x,start in [('P3',64,16),('P2',147,0)]:
    b+=rect(x-8,226,29,140,'#152637',3)
    b+=text(x+6,211,'16-31' if group=='P3' else '00-15',11,'white','middle')
    b+=text(x+6,388,group,13,'white','middle')
    for row in range(10):
        names=['VDD IO','GND'] if row==0 else (['GND','VDD IO'] if row==9 else [f'{start+2*(row-1):02}',f'{start+2*(row-1)+1:02}'])
        for side in [0,1]:
            xx,yy=x+12*side,235+14*row
            b+=rect(xx-2,yy-2,4,4,'#c0a162')
            b+=text(xx-8 if side==0 else xx+8,yy+2,names[side],6,'white','end' if side==0 else 'start')
            name=names[side]
            if group=='P2' and row==0:
                bp['VDD_IO' if side==0 else 'GND']=(xx,yy)
            elif name in ['03','04','05','06','10'] and group=='P2': bp['P1_'+name]=(xx,yy)
            elif name=='29' and group=='P3':bp['P1_29']=(xx,yy)
for i,(pin,color) in enumerate(zip(['P1_03','P1_04','P1_05','P1_06','P1_29','P1_10'],COLORS)):
    xx,yy=bp[pin]
    b+=f'<circle cx="{xx}" cy="{yy}" r="5" fill="none" stroke="{color}" stroke-width="2"/>'
b+=text(120,436,'5V headers omitted',10,'#fce2a6','middle')+text(120,455,'SENSOR SUPPLY: VDD IO',10,'white','middle')
b+=text(120,488,'Connection model only',9,'#cce1f0','middle')+text(120,507,'No PCB footprint / simulation',8,'#cce1f0','middle')
BOARD=Part('nRF54LM20_DK_PCA10184','nRF54LM20-DK PCA10184 0.7.0 (selected pins)',240,550,b,bp)

# All 630 terminal holes; split rails with 50 holes each, total 830.
holes={}; buses=[]
bb=rect(0,0,630,240,'#f4f1e9',7, '#c8c5bd')+rect(15,117,600,12,'#d9d7cf',2)
rowY={**dict(zip('ABCDE',range(68,105,9))),**dict(zip('FGHIJ',range(137,174,9)))}
for n in range(63):
    for r,y in rowY.items():holes[r+str(n)]=(36+9*n,y)
    buses += [[r+str(n) for r in 'ABCDE'],[r+str(n) for r in 'FGHIJ']]
    if n%5==0:
        bb+=text(36+9*n,57,n,7,anchor='middle')+text(36+9*n,190,n,7,anchor='middle')
for r,y in rowY.items():bb+=text(14,y+3,r,8)
for vertical,yp,yn in [('T',19,32),('B',208,221)]:
    for side,offset in [('L',0),('R',34)]:
        for pol,y,col in [('P',yp,RED),('N',yn,GND)]:
            members=[]
            for j in range(25):
                name=f'{vertical}{side}{pol}{j}';holes[name]=(36+9*(offset+(j//5)*6+j%5),y);members.append(name)
            buses.append(members)
            bb+=line(28+9*offset,y-5,36+9*(offset+28),y-5,col,1)
    bb+=text(14,yp+3,'+',10,RED)+text(14,yn+3,'−',10,GND)
bb+=text(319,12,'GAP',6,'#6b7280','middle')+text(319,236,'GAP',6,'#6b7280','middle')
BREAD=Part('MB102_reference','MB-102 reference grid 0-62 / top-number convention',630,240,bb,holes,buses,True)

resistors={}
for span in [45,90]:
    r=line(0,9,span,9,'#939a9f',2)+rect(span/2-13,3,26,12,'#c4e4df',5,'#51706d')
    for dx,col in [(-8,'#804929'),(-4,'#202124'),(0,'#202124'),(4,'#bd533c'),(9,'#804929')]:r+=rect(span/2+dx,3,2,12,col)
    r+=text(span/2,0,'10k',7,anchor='middle')
    resistors[span]=Part('R10k_'+str(span),'10k ohm 1% resistor',span,19,r,{'connector0':(0,9),'connector1':(span,9)})

def sensor(key,title,kind,top=False):
    s=rect(0,0,112,68,'#f4f7fa',6,'#cad4df')+text(56,14,title,10,anchor='middle')
    if kind=='ldr':
        s+='<circle cx="56" cy="37" r="16" fill="#decaa7" stroke="#926a4d" stroke-width="2"/>'
        s+='<path d="M46 28H64V33H47V38H65V43H48V47H63" fill="none" stroke="#96563f" stroke-width="2"/>'
    elif kind=='ntc':s+=rect(31,26,50,14,'#a1a9b2',7)+text(56,52,'10K B3950',8,anchor='middle')
    else:s+=rect(23,27,36,16,'#dedbd6',2)+rect(70,27,19,16,'#dedbd6',2)+text(56,54,'NO contact + magnet',7,anchor='middle')
    if top:
        s+=line(14,0,14,40,'#727d89')+line(98,0,98,40,'#727d89')
        pins={'connector0':(14,0),'connector1':(98,0)}
    else:
        s+=line(42,55,42,68,'#727d89')+line(70,55,70,68,'#727d89')
        pins={'connector0':(42,68),'connector1':(70,68)}
    return Part(key,title,112,71,s,pins)
LDR=sensor('GL5528','GL5528','ldr'); NTC=sensor('NTC10K','NTC probe','ntc'); REED=sensor('NO_contact','5C-39 NO','reed')
LDR_TOP=sensor('GL5528_top','GL5528','ldr',True)
REED_TOP=sensor('NO_contact_top','5C-39 NO','reed',True)

instances={}; connections=defaultdict(list); wires=[]; attachments=[]; counter=100
def inst(part,title,x,y,group='base'):
    global counter
    counter+=1;i=counter
    instances[i]={'part':part,'title':title,'x':x,'y':y,'group':group}
    return i
bi=inst(BOARD,'DK / USB at top',30,160); hi=inst(BREAD,'MB-102 / top-number coordinate reference',340,300)
def point(endpoint):
    i,p=endpoint;o=instances[i];x,y=o['part'].pins[p];return o['x']+x,o['y']+y
def connect(a,b):
    connections[a].append(b);connections[b].append(a)
def attach(a,b):
    assert point(a)==point(b),(a,b,point(a),point(b))
    connect(a,b);attachments.append((a,b))
def wire(a,b,color,group='base',via=(),label=''):
    global counter
    points=[point(a),*via,point(b)];ids=[]
    for p,q in zip(points,points[1:]):
        if p==q:continue
        counter+=1;ids.append(counter)
        wires.append({'id':counter,'a':p,'b':q,'color':color,'group':group,'label':label})
    assert ids
    connect(a,(ids[0],'connector0'))
    for u,v in zip(ids,ids[1:]):connect((u,'connector1'),(v,'connector0'))
    connect((ids[-1],'connector1'),b)
def hole(s):return hi,s
def R(name,a,b,group):
    x,y=point(hole(a));x2,y2=point(hole(b));assert y==y2
    ri=inst(resistors[x2-x],name,x,y-9,group)
    attach((ri,'connector0'),hole(a));attach((ri,'connector1'),hole(b));return ri

# Rails: explicitly bridge every midpoint and link lower rails to upper rails.
wire((bi,'VDD_IO'),hole('TLP0'),RED,via=[(177,383),(287,383),(287,279),(376,279)],label='VDD IO -> upper red rail')
wire((bi,'GND'),hole('TLN0'),GND,via=[(280,395),(280,290),(367,290)],label='GND -> upper blue rail')
for pos in ['T','B']:
    for pol,col in [('P',RED),('N',GND)]:
        wire(hole(pos+'L'+pol+'24'),hole(pos+'R'+pol+'0'),col,label='Rail midpoint bridge')
for pol,col in [('P',RED),('N',GND)]:
    x=329 if pol=='P' else 318
    a,b=hole('TL'+pol+'1'),hole('BL'+pol+'1')
    wire(a,b,col,via=[(x,point(a)[1]),(x,point(b)[1])],label='Upper/lower rail link')

channels=[]
for k,(base,lower,pin,label) in enumerate([(10,False,'P1_03','잠금 표시'),(25,False,'P1_04','보온 표시'),(10,True,'P1_05','백미 표시'),(25,True,'P1_06','시간 첫 표시')]):
    group='ch'+str(k);color=COLORS[k];node=base+2;ground=base+7;output=base+12
    sr,rr,tr,outr=('J','I','H','F') if lower else ('A','B','C','E')
    side='B' if lower else 'T'
    si=inst(LDR_TOP if lower else LDR,'LDR '+str(k+1)+' / '+label,385+(base-10)*10,700 if lower else 110,group)
    a,b=hole(sr+str(base)),hole(sr+str(node))
    # External sensors are explicitly connected by two extension wires.
    sy=610+k*12 if lower else 255+k*12
    wire((si,'connector0'),a,RED,group,via=[(point((si,'connector0'))[0],sy),(point(a)[0],sy)])
    wire((si,'connector1'),b,color,group,via=[(point((si,'connector1'))[0],sy+7),(point(b)[0],sy+7)])
    r1=R('R'+str(k*2+1)+' 10k / divider',rr+str(node),rr+str(ground),group)
    r2=R('R'+str(k*2+2)+' 10k / ADC series',tr+str(node),tr+str(output),group)
    wire(hole(rr+str(base)),hole(side+'LP'+str(5+k)),RED,group)
    wire(hole(tr+str(ground)),hole(side+'LN'+str(10+k)),GND,group)
    a=hole(outr+str(output));q=point((bi,pin));yy=550+k*12;xx=290+k*7
    py=q[1]+6 if pin in ['P1_04','P1_06'] else q[1]
    wire(a,(bi,pin),color,group,via=[(point(a)[0],yy),(xx,yy),(xx,py),(q[0],py)])
    channels.append({'key':group,'label':label,'pin':pin.replace('_','.'),'sensor':[sr+str(base),sr+str(node)],'resistors':[[rr+str(node),rr+str(ground)],[tr+str(node),tr+str(output)]],'supply':rr+str(base),'ground':tr+str(ground),'output':outr+str(output),'sensor_id':si,'rids':[r1,r2],'color':color})

# NTC: fixed resistor above node; NTC goes from node to ground.
si=inst(NTC,'NTC / 외부 표면',805,110,'ch4')
wire((si,'connector0'),hole('A48'),COLORS[4],'ch4',via=[(847,239),(808,239)])
wire((si,'connector1'),hole('A53'),GND,'ch4',via=[(875,246),(853,246)])
r1=R('R9 10k / NTC pull-up','B43','B48','ch4');r2=R('R10 10k / ADC series','C48','C58','ch4')
wire(hole('A43'),hole('TRP12'),RED,'ch4');wire(hole('B53'),hole('TRN20'),GND,'ch4')
a=hole('E58');q=point((bi,'P1_29'))
wire(a,(bi,'P1_29'),COLORS[4],'ch4',via=[(898,622),(18,622),(18,q[1]+6),(q[0],q[1]+6)])
channels.append({'key':'ch4','label':'외부 온도','pin':'P1.29','sensor':['A48','A53'],'resistors':[['B43','B48'],['C48','C58']],'supply':'A43','ground':'B53','output':'E58','sensor_id':si,'rids':[r1,r2],'color':COLORS[4]})
si=inst(REED_TOP,'개폐 / 뚜껑·본체',805,700,'ch5')
wire((si,'connector0'),hole('J48'),COLORS[5],'ch5',via=[(819,661),(808,661)])
wire((si,'connector1'),hole('J53'),GND,'ch5',via=[(903,669),(853,669)])
r1=R('R11 10k / contact pull-up','I43','I48','ch5')
wire(hole('J43'),hole('BRP12'),RED,'ch5');wire(hole('I53'),hole('BRN20'),GND,'ch5')
a=hole('F48');q=point((bi,'P1_10'))
wire(a,(bi,'P1_10'),COLORS[5],'ch5',via=[(808,610),(311,610),(311,q[1]+6),(q[0],q[1]+6)])
channels.append({'key':'ch5','label':'뚜껑 개폐','pin':'P1.10','sensor':['J48','J53'],'resistors':[['I43','I48']],'supply':'J43','ground':'I53','output':'F48','sensor_id':si,'rids':[r1],'color':COLORS[5]})

def layer_for(i):return instances[i]['part'].layer if i in instances else 'breadboardWire'
def save_connections(v,i):
    cs=E.SubElement(v,'connectors')
    for (idx,pin),targets in connections.items():
        if idx!=i:continue
        c=E.SubElement(cs,'connector',{'connectorId':pin,'layer':layer_for(i)})
        E.SubElement(c,'geometry',{'x':'0','y':'0'})
        dest=E.SubElement(c,'connects')
        for j,jpin in targets:E.SubElement(dest,'connect',{'modelIndex':str(j),'connectorId':jpin,'layer':layer_for(j)})

fz=E.Element('module',{'fritzingVersion':'0.9.4'})
vv=E.SubElement(fz,'views');E.SubElement(vv,'view',{'name':'breadboardView','backgroundColor':'#f5f7fa','gridSize':'0.1in','showGrid':'0','alignToGrid':'0'})
ii=E.SubElement(fz,'instances')
for i,o in instances.items():
    p=o['part'];e=E.SubElement(ii,'instance',{'moduleIdRef':p.mid,'modelIndex':str(i),'path':p.key+'.fzp'})
    E.SubElement(e,'title').text=o['title']
    v=E.SubElement(E.SubElement(e,'views'),'breadboardView',{'layer':p.layer})
    E.SubElement(v,'geometry',{'x':str(o['x']),'y':str(o['y']),'z':'1' if i in [bi,hi] else '3'})
    save_connections(v,i)
for w in wires:
    e=E.SubElement(ii,'instance',{'moduleIdRef':'WireModuleID','modelIndex':str(w['id']),'path':':/resources/parts/core/wire.fzp'})
    E.SubElement(e,'title').text=w['label'] or 'Jumper'
    v=E.SubElement(E.SubElement(e,'views'),'breadboardView',{'layer':'breadboardWire'})
    x,y=w['a'];x2,y2=w['b']
    E.SubElement(v,'geometry',{'x':str(x),'y':str(y),'x1':'0','y1':'0','x2':str(x2-x),'y2':str(y2-y),'z':'4','wireFlags':'64'})
    E.SubElement(v,'wireExtras',{'color':w['color'],'opacity':'1','mils':'20','banded':'0'})
    save_connections(v,w['id'])
E.indent(fz)
fz_bytes=E.tostring(fz,encoding='utf-8',xml_declaration=True)
used={o['part'].key:o['part'] for o in instances.values()}
with zipfile.ZipFile(OUT/'Bapsim_All_Sensors.fzz','w',zipfile.ZIP_DEFLATED) as z:
    z.writestr('Bapsim_All_Sensors.fz',fz_bytes)
    for p in used.values():
        z.writestr('part.'+p.key+'.fzp',p.fzp);z.writestr('svg.breadboard.'+p.key+'.svg',p.svg)

# Electrical validation: union only wires and breadboard buses, never resistors.
parent={}
def find(x):
    parent.setdefault(x,x)
    if parent[x]!=x:parent[x]=find(parent[x])
    return parent[x]
def union(a,b):parent[find(a)]=find(b)
for a,targets in connections.items():
    for b in targets:union(a,b)
for w in wires:union((w['id'],'connector0'),(w['id'],'connector1'))
for bus in BREAD.buses:
    for pin in bus[1:]:union(hole(bus[0]),hole(pin))
vcc=find((bi,'VDD_IO'));gnd=find((bi,'GND'));assert vcc!=gnd
occupied=defaultdict(list)
for a,targets in connections.items():
    if a[0]==hi:
        occupied[a[1]]+=targets
assert all(len(v)==1 for v in occupied.values()),{k:v for k,v in occupied.items() if len(v)>1}
for c in channels:
    node=find(hole(c['sensor'][1 if c['key'] in ['ch0','ch1','ch2','ch3'] else 0]))
    out=find(hole(c['output']));pin=(bi,c['pin'].replace('.','_'))
    assert find(pin)==out
    assert node not in [vcc,gnd] and out not in [vcc,gnd]
    for a,b in c['resistors']:assert find(hole(a))!=find(hole(b))
    assert find(hole(c['supply']))==vcc and find(hole(c['ground']))==gnd
    if len(c['rids'])==2:
        a,b=c['resistors'][1];assert find(hole(a))==node and find(hole(b))==out
    else:assert node==out
    a,b=c['resistors'][0]
    if c['key'] in ['ch0','ch1','ch2','ch3']:
        assert find(hole(a))==node and find(hole(b))==gnd
        assert find(hole(c['sensor'][0]))==vcc
    else:
        assert find(hole(a))==vcc and find(hole(b))==node
        assert find(hole(c['sensor'][1]))==gnd
assert len({find(hole(c['output'])) for c in channels})==6
report={'result':'PASS','checks':['No VDD/GND short','All 6 output nets distinct','All resistor terminals on distinct nets','Divider and pull-up topology correct','All 6 midpoint and upper/lower rail jumpers present','No breadboard socket occupied twice'],'resistors':11,'sensors':6,'wire_segments':len(wires),'scope':'Static connection graph only; not hardware, voltage, calibration or simulation validation.'}
(OUT/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
(OUT/'connections.json').write_text(json.dumps(channels,ensure_ascii=False,indent=2),encoding='utf-8')

def full_drawing(selected='all'):
    d=rect(0,0,1020,940,'white')+text(30,37,'밥심 · 전체 센서 배선도',26)+text(30,61,'nRF54LM20-DK / PCA10184 0.7.0  ·  광센서 4 + NTC 1 + 개폐 1',12)
    d+=text(340,83,'브레드보드: 위쪽 인쇄 번호를 기준으로 A–J를 읽으세요.',11)
    for i,o in instances.items():
        opacity='1' if selected=='all' or o['group'] in ['base',selected] else '.09'
        d+=f'<g class="grp {o["group"]}" opacity="{opacity}" transform="translate({o["x"]},{o["y"]})">{o["part"].draw}</g>'
    for w in wires:
        opacity='1' if selected=='all' or w['group'] in ['base',selected] else '.05'
        x,y=w['a'];x2,y2=w['b']
        d+=f'<g class="grp {w["group"]}" opacity="{opacity}">'+line(x,y,x2,y2,'white',4.5)+line(x,y,x2,y2,w['color'],2.2)+'</g>'
    for k,c in enumerate(channels):
        o=instances[c['sensor_id']]
        d+=text(o['x']+56,o['y']+90 if k in [2,3,5] else o['y']-13,f'{k+1}. '+c['label'],13,c['color'],'middle')
        d+=text(o['x']+56,o['y']+105 if k in [2,3,5] else o['y']+106,' / '.join(c['sensor']),10,c['color'],'middle')
    d+=text(30,838,'빨강 = VDD IO   검정 = GND   채널별 색 = 신호 · 선 색이 다르면 라벨로 구분하세요.',12)
    d+=text(30,864,'센서 그림은 밥솥에 부착할 부품입니다. 브레드보드까지 두 가닥을 연장합니다.',12)
    d+=text(30,890,'전원 줄 중앙은 끊겨 있다고 가정하고 연결했습니다. 5V0 핀은 사용하지 않습니다.',12)
    d+=text(30,919,'설치용 배치 초안 · 전압/센서 보정 미완료 · PCB 제작도나 시뮬레이터 아님',11,'#a34828')
    return svg(d,1020,940)

(OUT/'overview.svg').write_text(full_drawing(),encoding='utf-8')
for c in channels:(OUT/(c['key']+'.svg')).write_text(full_drawing(c['key']),encoding='utf-8')

rows=''
for k,c in enumerate(channels):
    rr='<br>'.join(' ↔ '.join(r)+' (10kΩ)' for r in c['resistors'])
    rows+=f'<tr><td><b style="color:{c["color"]}">{k+1}. {c["label"]}</b></td><td>{" ↔ ".join(c["sensor"])}</td><td>{rr}</td><td>{c["supply"]} → 빨강<br>{c["ground"]} → 파랑</td><td>{c["output"]} → <b>{c["pin"]}</b></td></tr>'
buttons='<button data-key="all" class="active">전체 보기</button>'+''.join(f'<button data-key="{c["key"]}">{k+1}. {c["label"]}</button>' for k,c in enumerate(channels))
html='''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>밥심 센서 배선도</title><style>
*{box-sizing:border-box}body{margin:0;background:#eef2f6;color:#24374a;font:16px/1.65 'Malgun Gothic',sans-serif}main{max-width:1260px;margin:35px auto;padding:0 24px}h1{font-size:32px;margin:0}h2{margin-top:36px}p{max-width:1000px}.eyebrow{font-size:13px;color:#386989;font-weight:700;letter-spacing:2px}.card{background:white;border-radius:14px;padding:25px;margin:20px 0;border:1px solid #dae2eb}.note{background:#fff4dc;padding:16px;border-left:4px solid #bd811b}nav{display:flex;gap:8px;flex-wrap:wrap;position:sticky;top:0;background:#eef2f6;padding:12px 0;z-index:2}button{border:1px solid #bbc9d7;background:white;padding:10px 14px;border-radius:8px;cursor:pointer;font:inherit}button.active{background:#184876;color:white}.drawing{overflow:auto}.drawing img{width:100%;min-width:920px}table{border-collapse:collapse;width:100%}th,td{text-align:left;border-bottom:1px solid #dce3e9;padding:13px 10px;vertical-align:top}th{background:#edf3f8}code{background:#e9eef3;padding:2px 5px}a{color:#1263a6}.two{display:grid;grid-template-columns:1fr 1fr;gap:20px}@media(max-width:800px){.two{display:block}}.small{font-size:14px;color:#5d6c7a}li{margin:7px 0}</style><main>
<div class="eyebrow">BAPSIM · HARDWARE WIRING · 2026.10.06</div><h1>보고 연결하는 센서 배선도</h1><p>먼저 <b>전원 배선</b>을 확인하고, 아래 버튼으로 센서를 하나씩 선택하세요. 색은 선을 구분하기 위한 표시이며, 같은 색의 실제 점퍼선이 없어도 라벨로 구분할 수 있습니다.</p>
<div class="note"><b>지금까지 확인한 것:</b> 광센서 1채널이 가림 524–528 / 노출 4092로 변했습니다. 이 값은 밥솥 LED 임계값이 아닙니다. 밝은 쪽은 상한에 가까우며, 공급 전압·저항·전체 회로·실제 LED 판정은 아직 실측 검증 전입니다. 배선 변경 전 USB를 빼세요.</div>
<nav>BUTTONS</nav><div class="card drawing"><img id="drawing" src="overview.svg" alt="전체 센서와 브레드보드, Nordic 개발보드 배선도"></div>
<div class="card"><h2>구멍별 연결표</h2><p>현재 시험한 첫 광센서의 A10/A12, B12/B17, C12/C22, E22 위치를 기준으로 확장했습니다. 밥솥 부착 시 센서를 빼고, 센서의 두 선을 같은 두 구멍에 연결하세요.</p><p class="note">이 그림은 <b>사진 위쪽 번호 기준 0–62 참조 격자</b>입니다. 아래쪽 거꾸로 인쇄된 번호를 섞어 읽지 마세요. 제품마다 시작 번호가 다르면 같은 이름의 구멍인지 먼저 대조해야 합니다. 그림의 선 길이는 실제 케이블 길이가 아닙니다.</p><div style="overflow:auto"><table><thead><tr><th>센서</th><th>센서의 두 선</th><th>저항 다리</th><th>전원 줄 연결</th><th>보드 신호선</th></tr></thead><tbody>ROWS</tbody></table></div></div>
<div class="two"><section class="card"><h2>전원 줄은 이렇게 연결</h2><ol><li>보드 P2의 <b>VDD IO → 위쪽 빨간 줄 왼쪽 구간</b></li><li>보드 P2의 <b>GND → 위쪽 파란 줄 왼쪽 구간</b></li><li>위 빨강 왼쪽 ↔ 오른쪽 / 위 파랑 왼쪽 ↔ 오른쪽</li><li>아래 빨강 왼쪽 ↔ 오른쪽 / 아래 파랑 왼쪽 ↔ 오른쪽</li><li>위 빨강 ↔ 아래 빨강 / 위 파랑 ↔ 아래 파랑</li></ol><p>중앙을 잇는 4개 + 위아래를 잇는 2개, 총 <b>6개의 전원 줄 연결선</b>이 있습니다. 전원 줄을 잇는 선도 각각 별도 구멍에 꽂습니다.</p></section><section class="card"><h2>보드 핀을 찾는 방향</h2><p><b>USB 단자가 위로</b> 오게 놓습니다. P2는 <b>PORT1 00–15</b>, 그 왼쪽 P3는 <b>PORT1 16–31</b>입니다.</p><ul><li>P2 맨 위 왼쪽 VDD IO / 오른쪽 GND</li><li>P1.03: P2 오른쪽 03</li><li>P1.04: P2 왼쪽 04</li><li>P1.05: P2 오른쪽 05</li><li>P1.06: P2 왼쪽 06</li><li>P1.10: P2 왼쪽 10</li><li>P1.29: P3 오른쪽 29</li></ul><p><b>별도 5V0 헤더는 사용하지 않습니다.</b></p><a href="parts/nRF54LM20_DK_PCA10184.svg" target="_blank">보드 그림 크게 보기 ↗</a></section></div>
<section class="card"><h2>밥솥까지 선을 빼는 방법</h2><p>광센서 4개는 잠금 표시, 보온 표시, 백미 표시, 시간 표시 맨 왼쪽에 각각 부착합니다. 센서마다 두 가닥을 이 표의 센서 구멍까지 연장합니다. 저항 11개는 브레드보드에 남습니다.</p><p>NTC는 외부 표면의 반복 측정 가능한 위치, 개폐센서는 뚜껑·본체에 나누어 부착합니다. 전선이 나온 접점 부품을 본체에 두면 움직이는 전선을 줄일 수 있습니다. 증기구·압력 장치·뚜껑 패킹을 피하고 뚜껑을 완전히 열 때도 당겨지지 않게 배치하세요. 길이는 설치 경로를 끈으로 재서 결정합니다.</p><p><b>현재 배치의 역할:</b> 전면 광센서 4개와 접점·온도 센서의 조립 초안. 온도값을 음식 온도로 해석하지 않으며, NO 접점만으로 단선은 구별할 수 없습니다.</p></section>
<section class="card"><h2>Fritzing에서 편집</h2><p><a href="Bapsim_All_Sensors.fzz">Bapsim_All_Sensors.fzz</a>를 Fritzing에서 열고 <b>브레드보드 뷰</b>를 사용하세요. 센서·저항·보드·선은 개별 객체이고 내부 연결 정보가 들어 있습니다. 보드 부품은 <a href="parts/nRF54LM20_DK_PCA10184.fzpz">사용자 정의 부품 파일</a>로도 제공됩니다.</p><p>이 파일은 브레드보드 조립용입니다. PCB 제작용 풋프린트, 회로 시뮬레이션, 자동 보정은 제공하지 않습니다. 다른 뷰의 자동 배치를 제조용 설계로 사용하지 마세요.</p><p class="small">근거: 프로젝트 A08 배선 사전설계, 사용자 보드 사진, Nordic PCA10184 0.7.0 공식 회로도·설계 파일. 전기 연결 그래프의 단락·채널 분리·저항 위치 검사는 <a href="validation.json">validation.json</a>에 있습니다.</p><p><a href="https://www.nordicsemi.com/Products/Development-hardware/nRF54LM20-DK/Hardware-files">Nordic 공식 하드웨어 파일</a> · <a href="https://github.com/fritzing/fritzing-app/releases/tag/CD-548">설치한 Fritzing 공식 개발 빌드</a></p></section></main>
<script>document.querySelectorAll('button').forEach(b=>b.onclick=()=>{document.querySelectorAll('button').forEach(x=>x.classList.remove('active'));b.classList.add('active');document.querySelector('#drawing').src=b.dataset.key==='all'?'overview.svg':b.dataset.key+'.svg'});</script></html>'''
html=html.replace('BUTTONS',buttons).replace('ROWS',rows)
(OUT/'index.html').write_text(html,encoding='utf-8')
print(json.dumps(report,ensure_ascii=True))
print(OUT/'Bapsim_All_Sensors.fzz')
