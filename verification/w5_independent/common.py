"""Qualification receipts, independent SQL observations, and owned fixture seed."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

from fixture import APP, ISSUES, MEMBERSHIPS, PROJECTS, table


class Receipt:
    def __init__(self):
        self.results, self.secrets = [], set()

    def secret(self, value):
        if isinstance(value, str) and value:
            self.secrets.add(value)
        return hashlib.sha256(value.encode()).hexdigest()

    def redact(self, value):
        if isinstance(value, dict):
            return {name: self.redact(item) for name, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [self.redact(item) for item in value]
        if isinstance(value, str):
            for secret in sorted(self.secrets, key=len, reverse=True):
                value = value.replace(secret, "<synthetic-secret-redacted>")
            return re.sub(r"dia_[0-9a-f]{64}", "<synthetic-api-secret-redacted>", value)
        return value

    def record(self, name, okay, observed, expected):
        self.results.append({"name": name, "status": "PASS" if okay else "FAIL", "observed": observed, "expected": expected})
        return bool(okay)

    def report(self, service, binary_sha256, *, error=None, **extra):
        status = "FAIL" if error or service.cleanup_errors or any(row["status"] != "PASS" for row in self.results) else "PASS"
        return self.redact({"status": status, "binary_sha256": binary_sha256, "error": error, "results": self.results,
                            "shutdowns": service.shutdowns, "cleanup_errors": service.cleanup_errors,
                            "http_requests": len(service.trace), **extra})


def app_id(name=APP):
    return hashlib.sha256(name.encode()).hexdigest()


def quote(value):
    return "'" + value.replace("'", "''") + "'"


def wait_for(predicate, label, timeout=6):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError(("bounded fixture schedule not reached", label))


def seed(service):
    """COPY fixed synthetic records into the owner's actual initialized PG.

    This is ordinary data setup. Product authorization/read/write methods remain
    actual Diamond operations, and expected visible rows come from these tuples.
    """
    def copy(resource, columns, values):
        data = "\n".join("\t".join("\\N" if value is None else str(value) for value in row) for row in values) + "\n"
        subprocess.run(["psql", service.owner, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-c",
                        f"COPY {table(resource)} ({','.join(chr(34) + name + chr(34) for name in columns)}) FROM STDIN"],
                       input=data, text=True, check=True, capture_output=True, timeout=30)
    copy("Globals", ["id", "label"], [("global-one", "Global fixture canary".encode().hex())])
    copy("Memberships", ["id", "workspace", "user", "role"], MEMBERSHIPS)
    copy("Projects", ["id", "workspace", "owner", "public"],
         [(a, b, c, "t" if d else "f") for a, b, c, d in PROJECTS])
    copy("Issues", ["id", "workspace", "project", "rank", "title"],
         [(a, b, c, d, e.encode().hex()) for a, b, c, d, e in ISSUES])
    service.sql("ANALYZE")


def restrict_runtime(service):
    """Restrict the isolated role to runtime DML; it is not an object owner."""
    schemas = service.sql("SELECT nspname FROM pg_namespace WHERE nspname LIKE 'diamond%' ORDER BY nspname").splitlines()
    service.sql("CREATE ROLE diamond_w5_runtime LOGIN")
    for schema in schemas:
        identifier = '"' + schema.replace('"', '""') + '"'
        service.sql(f"GRANT USAGE ON SCHEMA {identifier} TO diamond_w5_runtime")
        for relation in service.sql("SELECT tablename FROM pg_tables WHERE schemaname=" + quote(schema)).splitlines():
            quoted = identifier + '."' + relation.replace('"', '""') + '"'
            dml = (schema == "diamond_w4" and relation.startswith("r_")) or schema == "diamond_identity" and relation in ["users", "sessions", "challenges", "credentials", "rates"]
            privileges = "SELECT,INSERT,UPDATE,DELETE" if dml else "SELECT"
            service.sql(f"GRANT {privileges} ON TABLE {quoted} TO diamond_w5_runtime")
    service.configuration = service.owner.replace("user=diamond_w5_owner", "user=diamond_w5_runtime")
    return service.sql_json("SELECT nspname, has_schema_privilege('diamond_w5_runtime',oid,'CREATE') AS can_create FROM pg_namespace WHERE nspname LIKE 'diamond%' ORDER BY nspname")


def nominal(tag, value):
    return {"tag": tag, "value": value}


def issue_wire(row):
    key, workspace, project, rank, title = row
    return {"id": nominal("issue", key), "workspace": nominal("workspace", workspace),
            "project": nominal("project", project), "rank": str(rank), "title": title}


def project_wire(row):
    key, workspace, owner, public = row
    return {"id": nominal("project", key), "workspace": nominal("workspace", workspace), "owner": nominal("user", owner), "public": public}


def parallel(functions):
    with ThreadPoolExecutor(max_workers=len(functions)) as executor:
        return [future.result(timeout=25) for future in [executor.submit(function) for function in functions]]
