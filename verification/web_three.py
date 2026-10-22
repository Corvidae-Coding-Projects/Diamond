#!/usr/bin/env python3
"""Real W3 browser fixture: two app processes, PostgreSQL, transport delivery gates.

The proxy only forwards and holds completed real responses. All application UI,
business handlers and expected-value corpus are compiled Diamond. No fabricated
pages, JavaScript handlers or persistence are supplied by the fixture.
"""
from __future__ import annotations
import argparse
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time
import web_two as w2

ROOT = Path(__file__).resolve().parents[1]
PROBES = r'''export component Reply(source: Text):
  state answer: Text = "No response yet."
  state mounts: Int = 0
  on mount:
    mounts = mounts + 1
  on unmount:
    answer = "Disposed."
  on load:
    answer = rpc.echo_value(source)
  html:
    <article class="issue-card"><h3>Child response</h3><p id="child-source">{source}</p><pre id="child-answer">{answer}</pre><p id="child-mounts">{mounts}</p><button type="button" on-click={load}>Load child response</button></article>

export component Exercises:
  state results: List<Text> = []
  state answer: Text = "No response yet."
  state request: Text = "slow-old"
  state child_source: Text = "props-old"
  state child_visible: Bool = true
  state note: Text = "\nInitial textarea 雪 <literal>"
  state agreed: Bool = true
  state choice: Text = "blue"
  state form_title: Text = ""
  state submits: Int = 0
  state items: List<Text> = ["first", "second"]
  state normalization: Text = "\r\nA\rB<NUL>"
  state numeric: Int = 2
  on run:
    results = corpus()
  on load:
    answer = rpc.echo_value(request)
  on props:
    child_source = "props-new"
  on unmount_case:
    child_source = "unmount-old"
  on toggle:
    child_visible = not child_visible
  on nested:
    answer = "Nested: " + rpc.echo_value("ordinary")
  on branch:
    answer = match agreed:
      true => rpc.echo_value("branch-true")
      false => "branch-skipped"
  on remote_list:
    results = for item in ["one", "two"]:
      rpc.echo_value(item)
  on submit:
    submits = submits + 1
  on duplicate:
    items = ["first", "first"]
  on overflow:
    numeric = 9223372036854775807 + 1
  on recover:
    numeric = numeric + 3
  on probe_170:
    answer = probe_170()
  on probe_171:
    answer = probe_171()
  on probe_172:
    answer = probe_172()
  on probe_173:
    answer = probe_173()
  on probe_174:
    answer = probe_174()
  on probe_175:
    answer = probe_175()
  on probe_176:
    answer = probe_176()
  on probe_184:
    answer = probe_184()
  html:
    <section class="panel"><h2>W3 qualification controls</h2><p id="normalization" title={normalization}>{normalization}</p><button type="button" on-click={run}>Run independent corpus</button><p id="corpus-count">{len(results)}</p><ul id="corpus-values"><each items={results} as="result" key={result}><li>{result}</li></each></ul>
      <label for="request-value">Request value</label><input id="request-value" type="text" bind-value={request}><button type="button" on-click={load}>Load parent response</button><pre id="parent-answer">{answer}</pre>
      <when test={pending}><p id="pending-status" role="status">Request pending</p></when><when test={error != ""}><p id="local-error" role="alert">{error}</p></when>
      <button type="button" on-click={props}>Change child property</button><button type="button" on-click={unmount_case}>Prepare unmount request</button><button type="button" on-click={toggle}>Mount/unmount child</button>
      <when test={child_visible}><use component="Reply" source={child_source}></use></when>
      <button type="button" on-click={nested}>Nested remote expression</button><button type="button" on-click={branch}>Conditional remote expression</button><button type="button" on-click={remote_list}>Remote list expression</button>
      <form on-submit={submit}><label for="form-title">Required title</label><input id="form-title" type="text" bind-value={form_title} required="true" minlength="3"><label for="form-note">Form note</label><textarea id="form-note" bind-value={note}></textarea><label for="agreed">Agreed</label><input id="agreed" type="checkbox" bind-checked={agreed}><label for="choice">Color</label><select id="choice" bind-value={choice}><option value="red">Red</option><option value="blue">Blue</option></select><button type="submit">Submit qualification form</button></form>
      <p id="submit-count">{submits}</p><pre id="note-output">{note}</pre><p id="agreed-output">{agreed}</p><p id="choice-output">{choice}</p>
      <ul id="duplicate-list"><each items={items} as="item" key={item}><li>{item}</li></each></ul><button type="button" on-click={duplicate}>Duplicate keys</button><button type="button" on-click={overflow}>Overflow</button><button type="button" on-click={recover}>Recover</button><p id="numeric-output">{numeric}</p>
      <button type="button" on-click={probe_184}>Failure runtime-reject-7</button><button type="button" on-click={probe_170}>Failure runtime-reject-0</button><button type="button" on-click={probe_171}>Failure runtime-reject-1</button><button type="button" on-click={probe_172}>Failure runtime-reject-2</button><button type="button" on-click={probe_173}>Failure runtime-reject-3</button><button type="button" on-click={probe_174}>Failure runtime-reject-4</button><button type="button" on-click={probe_175}>Failure runtime-reject-5</button><button type="button" on-click={probe_176}>Failure runtime-reject-6</button>
    </section>
'''

class Fixture(w2.Fixture):
    def __init__(self, root, public_port=39171, route_failure_probe=False):
        super().__init__(root, public_host="127.0.0.1", public_port=public_port)
        self.gates = {name: threading.Event() for name in ['slow-old', 'props-old', 'unmount-old']}
        self.held = {}
        self.route_failure_probe = route_failure_probe
        self.reloads = 0
        self.page_fault = None
        self.prepare()

    def prepare(self):
        for path in (ROOT / 'examples/workspace').iterdir():
            if path.is_file(): shutil.copy2(path, self.project.parent / path.name)
        shutil.copy2(ROOT / 'verification/w3_independent/browser_corpus/shared.dia', self.project.parent / 'corpus.dia')
        (self.project.parent / 'probes.dia').write_text(PROBES.replace('<NUL>', chr(0)))
        independent = ROOT / 'verification/w3_independent'
        (self.project.parent / 'composition_shared.dia').write_text((independent / 'event_composition/shared.dia').read_text())
        (self.project.parent / 'composition.dia').write_text((independent / 'event_composition/ui.dia').read_text().replace('export component App:', 'export component ComposedEvents:'))
        (self.project.parent / 'key_paths.dia').write_text((independent / 'key_paths/ui.dia').read_text().replace('export component App:', 'export component KeyPaths:'))
        manifest = self.project.read_text().replace('imports = ["shared", "parts"]', 'imports = ["shared", "parts", "probes", "composition", "key_paths"]')
        manifest = manifest.replace('imports = ["shared"]', 'imports = ["shared", "composition_shared"]', 1)
        manifest += '\ninspect_data="server.inspect_data"\necho_data="server.echo_data"\nadd="server.add"\ndouble="server.double"\necho_text="server.echo_text"\nfail_int="server.fail_int"\nfail_bool="server.fail_bool"\necho_value = "server.echo_value"\n\n[modules.corpus]\npath="corpus.dia"\ntarget="shared"\n\n[modules.probes]\npath="probes.dia"\ntarget="browser"\nimports=["corpus"]\n'
        manifest += '\n[modules.composition_shared]\npath="composition_shared.dia"\ntarget="shared"\n\n[modules.composition]\npath="composition.dia"\ntarget="browser"\nimports=["composition_shared"]\n\n[modules.key_paths]\npath="key_paths.dia"\ntarget="browser"\n'
        self.project.write_text(manifest)
        ui = self.project.parent / 'tracker.dia'
        ui.write_text(ui.read_text().replace('    </main>', '      <route path="/qualification"><use component="Exercises"></use><use component="ComposedEvents"></use><use component="KeyPaths"></use></route>\n    </main>'))
        if self.route_failure_probe:
            source = ui.read_text().replace('export component Tracker:', 'export component Tracker:\n  state route_divisor: Int = 1')
            source = source.replace('  html:', '  on prepare_route_failure:\n    route_divisor = 0\n  on recover_route_failure:\n    route_divisor = 1\n  html:', 1)
            source = source.replace('<nav>', '<nav><button type="button" on-click={prepare_route_failure}>Prepare route failure</button><button type="button" on-click={recover_route_failure}>Recover route failure</button>')
            source = source.replace('<use component="About"', '<use component="About" divisor={route_divisor}')
            ui.write_text(source)
            cards = self.project.parent / 'cards.dia'
            cards.write_text(cards.read_text().replace('export component About(note: Text):', 'export component About(note: Text, divisor: Int):\n  state quotient: Int = 10 / divisor'))
        server = self.project.parent / 'server.dia'
        server.write_text(server.read_text() + '\nexport fn add' + (independent / 'event_composition/server.dia').read_text().split('export fn add', 1)[1] + '\nexport fn echo_value(value: Text) -> Text:\n  "Echo: " + value\n')

    def start(self):
        self.loaded_binary_sha256 = hashlib.sha256(w2.BINARY.read_bytes()).hexdigest()
        super().start()
        self.proxy.shutdown(); self.proxy.server_close(); self.thread.join(timeout=5)
        fixture = self
        class Proxy(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def forward(self):
                node = 'B' if self.path in ['/_diamond/rpc/create_issue', '/_diamond/logout'] else 'A'
                body = self.rfile.read(min(int(self.headers.get('Content-Length', '0')), 20000))
                headers = {k:v for k,v in self.headers.items() if k.lower() not in ['connection', 'x-diamond-test-node']}
                conn = http.client.HTTPConnection('127.0.0.1', fixture.ports[node], timeout=20)
                try:
                    conn.request(self.command,self.path,body=body,headers=headers)
                    response=conn.getresponse(); data=response.read(); gate=None
                    if self.path=='/_diamond/rpc/echo_value':
                        values=json.loads(body).get('arguments',[])
                        gate=values[0] if values and values[0] in fixture.gates else None
                    row={'node':node,'method':self.command,'path':self.path,'status':response.status,'gate':gate}
                    fixture.trace.append(row)
                    if gate:
                        fixture.held[gate]=fixture.held.get(gate,0)+1
                        fixture.write_state()
                        if not fixture.gates[gate].wait(180): row['gate_expired']=True
                    if fixture.page_fault and self.command=='GET' and response.status==200 and response.getheader('Content-Type','').startswith('text/html'):
                        if fixture.page_fault=='contract':
                            data=data.replace(b'data-contract="', b'data-contract="mismatch-', 1)
                        elif fixture.page_fault=='structure':
                            data=data.replace(b'Workspace tracker</h1>', b'Changed initial heading</h1>', 1)
                        row['page_fault']=fixture.page_fault
                    self.send_response(response.status)
                    for k,v in response.getheaders():
                        if k.lower() not in ['connection','transfer-encoding','content-length']: self.send_header(k,v)
                    self.send_header('Content-Length',str(len(data)))
                    self.end_headers()
                    try: self.wfile.write(data)
                    except (BrokenPipeError,ConnectionResetError): row['client_disconnected']=True
                finally: conn.close()
            do_GET=do_HEAD=do_POST=forward
        self.proxy=ThreadingHTTPServer(('127.0.0.1',int(self.authority.rsplit(':',1)[1])),Proxy)
        self.thread=threading.Thread(target=self.proxy.serve_forever,daemon=True);self.thread.start();self.write_state()

    def write_state(self):
        state={'origin':self.origin,'configuration':self.configuration,'contract':getattr(self,'contract',None),'ports':self.ports,
               'node_pids':{n:p.pid for n,p in self.nodes.items()},'binary_sha256':getattr(self,'loaded_binary_sha256',None),
               'page_fault':self.page_fault,'held':self.held,'released':{n:e.is_set() for n,e in self.gates.items()},'routing':self.trace,
               'project_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.project.parent.iterdir() if p.is_file()}}
        (self.root/'state.json').write_text(json.dumps(state,indent=2,ensure_ascii=False)+'\n');return state

    def reload(self):
        for n in list(self.nodes): self.stop_node(n)
        self.prepare();self.reloads+=1
        build=self.root/f'build-{self.reloads}';self.cli('build',self.project,'--out',build)
        self.contract=json.loads((build/'contract.json').read_text())['rpc_id']
        self.loaded_binary_sha256=hashlib.sha256(w2.BINARY.read_bytes()).hexdigest()
        self.start_node('A');self.start_node('B');self.write_state()

    def close(self):
        for e in self.gates.values(): e.set()
        super().close()
        if hasattr(self,'held'): self.write_state()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve-fixture',type=Path,required=True)
    parser.add_argument('--public-port',type=int,default=39171)
    parser.add_argument('--route-failure-probe',action='store_true',help='Add real Diamond controls for route initialization failure/recovery.')
    args=parser.parse_args();root=args.serve_fixture.resolve();root.mkdir(mode=0o700,parents=True,exist_ok=False)
    fixture=Fixture(root,args.public_port,args.route_failure_probe)
    try:
        fixture.start();print(json.dumps({'status':'READY','state':str(root/'state.json'),'origin':fixture.origin}),flush=True)
        for command in sys.stdin:
            command=command.strip()
            if command=='stop': break
            if command=='reload': fixture.reload()
            elif command.startswith('fault '):
                mode=command.split(' ',1)[1]
                if mode not in ['clear','contract','structure']: raise ValueError('fault mode')
                fixture.page_fault=None if mode=='clear' else mode
            elif command.startswith('release '): fixture.gates[command.split(' ',1)[1]].set()
            elif command not in ['status','']: print(json.dumps({'error':'use status, reload, release NAME, fault MODE or stop'}),flush=True);continue
            state=fixture.write_state();print(json.dumps({'status':'READY','held':state['held'],'released':state['released'],'binary_sha256':state['binary_sha256']}),flush=True)
    except KeyboardInterrupt: pass
    finally: fixture.close()
    return 0

if __name__=='__main__': raise SystemExit(main())
