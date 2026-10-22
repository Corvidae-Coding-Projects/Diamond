"""Reuse prior owned PG/HTTP lifecycle without editing historical W5 evidence."""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time

PREVIOUS = Path(__file__).resolve().parents[1] / 'w5_independent/service.py'
spec = importlib.util.spec_from_file_location('_w6_previous_service', PREVIOUS)
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
ENV_NAME = 'DIAMOND_W6_INDEPENDENT_POSTGRES'
RUNTIME_NAME = 'DIAMOND_W6_INDEPENDENT_RUNTIME'
previous.ENV_NAME = ENV_NAME  # private loaded module; no prior file is modified
Client = previous.Client
sha256 = previous.sha256
free_port = previous.free_port


class Service(previous.Service):
    def __init__(self, root, binary, project):
        super().__init__(root, binary, project)
        self.objects = self.root / 'objects'; self.objects.mkdir(mode=0o700)
        self.environment = {}
        self.runtime = {'objects':str(self.objects), 'http':{}}
        self.extra_serve = ('--identity-mailbox',str(self.mailbox),'--runtime-env',RUNTIME_NAME)

    def process_env(self, configuration=None):
        return os.environ | self.environment | {ENV_NAME:configuration or self.configuration,
                                               RUNTIME_NAME:json.dumps(self.runtime)}

    def cli(self,*arguments,success=True,configuration=None,input=None,timeout=40):
        run=subprocess.run([str(self.binary),*map(str,arguments)],env=self.process_env(configuration),input=input,
                           capture_output=True,text=True,timeout=timeout)
        if success is not None and (run.returncode==0)!=success:
            raise AssertionError({'command':run.args,'exit':run.returncode,'stdout':run.stdout,'stderr':run.stderr})
        return run

    def start_node(self,node,*,project=None,configuration=None,extra_serve=None):
        if node in self.nodes:raise AssertionError('owned node already running')
        log=(self.root/f'{node}-{time.monotonic_ns()}.log').open('w')
        self.logs.append(log);self.node_logs[node]=Path(log.name)
        args=self.extra_serve if extra_serve is None else tuple(map(str,extra_serve))
        self.nodes[node]=subprocess.Popen([str(self.binary),'serve',str(project or self.project),'--listen',
             f'127.0.0.1:{self.ports[node]}','--origin',self.origin,'--postgres-env',ENV_NAME,*args],
             stdout=log,stderr=log,env=self.process_env(configuration))
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            try:
                if self.request(node,'/')[0]==200:return
            except OSError:pass
            if self.nodes[node].poll() is not None:raise AssertionError(('startup exited',node,Path(log.name).read_text()))
            time.sleep(.025)
        raise AssertionError(('startup deadline',node,Path(log.name).read_text()))

    def files_cli(self,initialize=False):
        return self.cli('project-files-init' if initialize else 'project-files-clean',self.project,
                        '--postgres-env',ENV_NAME,'--runtime-env',RUNTIME_NAME)

    def transfer(self,node,client,path,body,*,upload=None,method='PUT',headers=None):
        headers={'Cookie':client.cookie or '', 'X-Diamond-CSRF':client.csrf, 'Content-Type':'application/octet-stream',
                 **(headers or {})}
        if upload:headers['X-Diamond-Upload']=upload
        return self.request(node,path,body,headers,method=method)
