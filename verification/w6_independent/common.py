"""Independent receipt/data helpers; reuse actual prior lifecycle plumbing."""
from __future__ import annotations
import importlib.util
from pathlib import Path
from fixture import APP, PASSWORD, app_id
from service import Client, ENV_NAME

path=Path(__file__).resolve().parents[1]/'w5_independent/common.py'
spec=importlib.util.spec_from_file_location('_w6_previous_common',path)
previous=importlib.util.module_from_spec(spec);spec.loader.exec_module(previous)
Receipt=previous.Receipt
quote=previous.quote
nominal=previous.nominal
seed=previous.seed
wait_for=previous.wait_for


def provision(service,username):
    service.cli('project-admin-add',service.project,'--postgres-env',ENV_NAME,'--user',username,
                '--email',username+'@qualification.invalid','--password-stdin',input=PASSWORD+'\n',configuration=service.owner)


def client(service,contract,username):
    result=Client(service,contract);result.login(username,PASSWORD)
    return result


def objects(service,identifier=None):
    where='app='+quote(app_id())
    if identifier is not None:where+=' AND id='+quote(identifier)
    return service.sql_json('SELECT id,binding,parent,owner,name,media,length,state,sha256,expires>clock_timestamp() AS live FROM diamond_w6.objects WHERE '+where+' ORDER BY id')
