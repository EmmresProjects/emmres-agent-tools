"""Bounded text extraction and guarded public HTTPS fetches for tools-main.py."""
import csv
import io
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

class Reader(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.parts = []
        self.links = []
        self.title = ''
        self.in_title = False
        self.tables = []
        self.row = None
        self.cell = None
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('script','style','nav','footer','header','aside','form','svg'):
            self.skip += 1
        if tag == 'title': self.in_title = True
        if self.skip: return
        if tag == 'a' and attrs.get('href'): self.links.append(attrs['href'])
        if tag == 'tr': self.row = []
        if tag in ('td','th') and self.row is not None: self.cell = []
    def handle_endtag(self, tag):
        if tag == 'title': self.in_title = False
        if tag in ('td','th') and self.cell is not None:
            self.row.append(' '.join(self.cell).strip());self.cell = None
        if tag == 'tr' and self.row is not None:
            if self.row:self.tables.append(self.row)
            self.row = None
        if tag in ('script','style','nav','footer','header','aside','form','svg') and self.skip: self.skip -= 1
        if tag in ('p','h1','h2','h3','li','article','section','blockquote','tr'): self.parts.append('\n')
    def handle_data(self, data):
        if self.in_title:self.title += data
        if self.skip:return
        t = data.strip()
        if t:
            self.parts.append(t)
            if self.cell is not None:self.cell.append(t)

class GuardedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def validate_url(url):
    p = urllib.parse.urlsplit(url)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.port not in (None,443):
        raise ValueError('Only public HTTPS URLs on port 443 without credentials are allowed')
    try:
        ips = socket.getaddrinfo(p.hostname,443,type=socket.SOCK_STREAM)
    except OSError as exc: raise ValueError('DNS lookup failed: '+str(exc)) from exc
    if not ips or any(not ipaddress.ip_address(info[4][0]).is_global for info in ips):
        raise ValueError('Private, loopback, reserved or unresolved hosts are blocked')
    return url

def fetch(url, max_bytes=1000000, timeout=10, user_agent='ResearchIntelligence/1.0 (respect robots)'):
    validate_url(url)
    p = urllib.parse.urlsplit(url)
    robots = urllib.parse.urlunsplit(('https',p.netloc,'/robots.txt','',''))
    opener = urllib.request.build_opener(GuardedRedirect())
    try:
        req = urllib.request.Request(robots,headers={'User-Agent':user_agent})
        with opener.open(req,timeout=timeout) as r: robot_text=r.read(200000).decode('utf-8','replace')
        rp=urllib.robotparser.RobotFileParser();rp.parse(robot_text.splitlines())
        if not rp.can_fetch(user_agent,url):raise ValueError('Robots policy disallows access')
    except urllib.error.HTTPError as e:
        if e.code not in (404,410): raise ValueError('Cannot confirm robots permission: HTTP '+str(e.code)) from e
    except urllib.error.URLError as e:raise ValueError('Cannot confirm robots permission: '+str(e)) from e
    req=urllib.request.Request(url,headers={'User-Agent':user_agent,'Accept':'text/html,text/plain,application/json,application/xml,text/xml,application/rss+xml,application/atom+xml'})
    with opener.open(req,timeout=timeout) as r:
        if r.status != 200: raise ValueError('HTTP '+str(r.status))
        typ=r.headers.get_content_type()
        if typ not in ('text/html','text/plain','application/json','application/xml','text/xml','application/rss+xml','application/atom+xml','text/csv'):
            raise ValueError('Unsupported content type: '+typ)
        data=r.read(max_bytes+1)
        if len(data)>max_bytes:raise ValueError('Response exceeds max_bytes')
        return data,typ,r.geturl()

def extract(data, kind, base=''):
    if kind in ('text/html','html'):
        r=Reader();r.feed(data.decode('utf-8','replace'))
        links=[urllib.parse.urljoin(base,x) for x in r.links]
        return {'title':r.title.strip(),'text':normalize(' '.join(r.parts)),'links':links,'tables':r.tables}
    if kind in ('application/rss+xml','application/atom+xml','application/xml','text/xml','xml'):
        root=ET.fromstring(data)
        entries=[]
        for elem in root.iter():
            if elem.tag.split('}')[-1] in ('item','entry','url'):
                title=elem.findtext('title') or elem.findtext('{*}title') or elem.findtext('{*}loc') or ''
                link=elem.findtext('link') or elem.findtext('{*}link') or elem.findtext('{*}loc') or ''
                for child in elem:
                    if child.tag.split('}')[-1]=='link' and child.attrib.get('href'):link=child.attrib['href']
                entries.append({'title':title,'url':urllib.parse.urljoin(base,link) if link else ''})
        return {'title':'Feed or sitemap','text':normalize(' '.join(e['title'] for e in entries)),'links':[e['url'] for e in entries if e['url']],'tables':[],'entries':entries}
    if kind in ('application/json','json'):
        value=json.loads(data)
        return {'title':'JSON document','text':normalize(json.dumps(value,ensure_ascii=False)),'links':[],'tables':[]}
    if kind in ('text/csv','csv'):
        rows=list(csv.reader(io.StringIO(data.decode('utf-8-sig','replace'))))[:2000]
        return {'title':'CSV table','text':normalize(' '.join(' '.join(x) for x in rows)),'links':[],'tables':rows}
    return {'title':'Text document','text':normalize(data.decode('utf-8-sig','replace')),'links':[],'tables':[]}

def normalize(s):return re.sub(r'\s+',' ',s).strip()
