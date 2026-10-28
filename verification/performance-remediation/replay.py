#!/usr/bin/env python3
"""Exercise the built Diamond server, real PostgreSQL, queue and persisted state."""
import concurrent.futures
import argparse
import hashlib
import http.client
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'benchmarks/rails-comparison'))
from campaign import mixed_phase, reset_state, state_oracle
from run import DIAMOND_PROJECT, HttpTarget, Service, diamond_setup, diamond_table


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, default=Path(__file__).with_name('runtime-final.json'))
    options = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix='diamond-perf-remediation-'))
    project = root / 'app'
    shutil.copytree(DIAMOND_PROJECT.parent, project)
    manifest = project / 'diamond.toml'
    original = manifest.read_text()
    binary = ROOT / 'target/release/diamond'
    service = Service(root, binary, manifest)
    service.extra_serve = ('--identity-mailbox', str(service.mailbox))
    evidence = {'root': str(root), 'status': 'FAIL', 'scenarios': [],
                'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
                'driver_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    def record(name, **values):
        evidence['scenarios'].append({'name': name, **values})
        print(json.dumps({'name': name, 'concurrency': values.get('concurrency'),
                          'repeat': values.get('repeat'), 'status': 'PASS'}), flush=True)
    def metrics():
        status, _, body = service.request('A', '/_diamond/health/metrics')
        assert status == 200
        return json.loads(body)
    def await_metric(key, value):
        end = time.monotonic() + 4
        while time.monotonic() < end:
            observed = metrics()
            if observed[key] == value:
                return observed
            time.sleep(.01)
        raise AssertionError((key, value, observed))
    def await_issue_lock():
        end=time.monotonic()+3
        while time.monotonic()<end:
            count=int(service.sql("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type='Lock' AND query LIKE '%"+diamond_table('Issues').split('.')[1].strip('"')+"%'"))
            if count:return
            time.sleep(.01)
        raise AssertionError('application issue operation did not reach held SQL lock')
    lock = None
    try:
        service.postgres()
        target, _, seed = diamond_setup(service, 100)
        target.denied_workspace = seed['denied_workspace']
        for concurrency in (1, 8, 32, 128):
            for repeat in (1, 2):
                reset_state(service, target, 100)
                phase = mixed_phase(target, concurrency, 2)
                a = phase['aggregate']
                oracle = state_oracle(service, target, 100, phase['successful_creates'])
                assert not a['unexpected_responses'] and not a['transport_errors'], a
                assert oracle['count_matches'] and oracle['create_count_matches'], oracle
                assert a['success_rate'] > .90, a
                record('mixed', concurrency=concurrency, repeat=repeat, aggregate=a, oracle=oracle)
        table = diamond_table('Issues')
        for revoke in (False, True):
            before_retries = metrics()['transaction_retries']
            lock = subprocess.Popen(['psql', service.owner, '-X', '-qAt', '-v', 'ON_ERROR_STOP=1'],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            lock.stdin.write(f"BEGIN; UPDATE {table} SET title='{bytes('External update', 'utf8').hex()}' WHERE id='{target.issue}'; SELECT 'locked';\n")
            lock.stdin.flush()
            assert lock.stdout.readline().strip() == 'locked'
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as worker:
                future = worker.submit(target.request, 'update', 0)
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    waiting = int(service.sql("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type='Lock'"))
                    if waiting:
                        break
                    time.sleep(.01)
                assert waiting > 0
                if revoke:
                    lock.stdin.write("UPDATE diamond_identity.credentials SET revoked=true WHERE username='alice';\n")
                lock.stdin.write('COMMIT;\n\\q\n')
                lock.stdin.flush()
                lock.communicate(timeout=3)
                lock = None
                result = future.result(timeout=4)
                assert result[0] == (401 if revoke else 200), result
            assert metrics()['transaction_retries'] > before_retries
            if revoke:
                assert service.sql(f"SELECT convert_from(decode(title,'hex'),'UTF8') FROM {table} WHERE id='{target.issue}'") == 'External update'
                service.sql("UPDATE diamond_identity.credentials SET revoked=false WHERE username='alice'")
            else:
                assert target.valid('read', *target.request('read', 0)[:2], 0)
            record('conflict-retry-revoked' if revoke else 'confirmed-conflict-retry', retries=metrics()['transaction_retries']-before_retries)
        service.stop_node('A')
        manifest.write_text(original + '\n[runtime]\nrequest_limit=1\nqueue_limit=2\nqueue_timeout_ms=150\nblocking_threads=1\ndatabase_connections=1\n')
        service.start_node('A')
        target.port = service.ports['A']
        _, build = service.build()
        artifact = next((build / 'assets').glob('*.js'))
        def client(mode, authenticated=False):
            config = {'path': '/api/v1/read' if authenticated else '/api/v1/_contract',
                      'method': 'POST' if authenticated else 'GET',
                      'headers': {'Host': service.authority, 'X-Diamond-API-Version': '1'}}
            if authenticated:
                config['headers'].update({'Authorization': 'Bearer '+target.bearer, 'Content-Type': 'application/json'})
                config['body'] = json.dumps({'arguments': [target.issue]})
            process = subprocess.Popen(['node', str(Path(__file__).with_name('client.cjs')), str(artifact),
                                        service.origin, mode], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True)
            process.stdin.write(json.dumps(config))
            process.stdin.close()
            process.stdin = None
            return process
        table = diamond_table('Issues')
        # Independent psql owns a real relation lock; release through its stdin.
        lock = subprocess.Popen(['psql', service.owner, '-X', '-qAt', '-v', 'ON_ERROR_STOP=1'],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        lock.stdin.write(f'BEGIN; LOCK TABLE {table} IN ACCESS EXCLUSIVE MODE; SELECT \'locked\';\n')
        lock.stdin.flush()
        assert lock.stdout.readline().strip() == 'locked'
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as workers:
            held = workers.submit(target.request, 'create', 700001)
            await_metric('active_requests', 1)
            await_issue_lock()
            queued = [workers.submit(target.request, 'create', n) for n in (700002, 700003)]
            await_metric('queued_requests', 2)
            started = time.monotonic()
            status, headers, body = service.request('A', '/api/v1/create', {'arguments': []},
                                                  {'X-Diamond-API-Version': '1'})
            assert status == 503 and json.loads(body)['error'] == 'overloaded', (status, body)
            assert headers.get('retry-after') == '1' and headers.get('x-diamond-retry') == 'safe'
            assert time.monotonic() - started < .15
            rejected = [future.result(timeout=1) for future in queued]
            assert all(status == 503 and value.get('retry_safe') for status, value, _ in rejected), rejected
            await_metric('queued_requests', 0)
            lock.stdin.write('COMMIT;\n\\q\n')
            lock.stdin.flush()
            lock.communicate(timeout=3)
            lock = None
            assert held.result(timeout=4)[0] == 200
        await_metric('active_requests', 0)
        rows = int(service.sql(f'SELECT count(*) FROM {table} WHERE rank BETWEEN 1000700001 AND 1000700003'))
        assert rows == 1, rows
        assert target.request('read', 1)[0] == 200
        record('bounded-overload-and-recovery', rejected=len(queued)+1, committed=rows, metrics=metrics())
        for mode in ('cancel', 'epoch', 'overload'):
            lock = subprocess.Popen(['psql', service.owner, '-X', '-qAt', '-v', 'ON_ERROR_STOP=1'],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            lock.stdin.write(f"BEGIN; LOCK TABLE {table} IN ACCESS EXCLUSIVE MODE; SELECT 'locked';\n")
            lock.stdin.flush()
            assert lock.stdout.readline().strip() == 'locked'
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as worker:
                held = worker.submit(target.request, 'read', 1)
                await_metric('active_requests', 1)
                await_issue_lock()
                if mode == 'cancel':
                    stream = socket.create_connection(('127.0.0.1', target.port))
                    stream.sendall(f'GET /api/v1/_contract HTTP/1.1\r\nHost: {service.authority}\r\n\r\n'.encode())
                    await_metric('queued_requests', 1)
                    stream.close()
                    await_metric('queued_requests', 0)
                    record('queued-disconnect-releases-capacity')
                probe = client(mode)
                if mode == 'overload':
                    first = probe.stdout.readline()
                    assert json.loads(first)['status'] == 503, first
                else:
                    stdout, stderr = probe.communicate(timeout=3)
                    assert probe.returncode == 0, (stdout, stderr)
                lock.stdin.write('COMMIT;\n\\q\n')
                lock.stdin.flush()
                lock.communicate(timeout=3)
                lock = None
                assert held.result(timeout=3)[0] == 200
                if mode == 'overload':
                    stdout, stderr = probe.communicate(timeout=5)
                    stdout = first + stdout
                    assert probe.returncode == 0, (stdout, stderr)
                record('generated-client-'+mode, observations=stdout.splitlines())
            await_metric('active_requests', 0)
            await_metric('queued_requests', 0)
        log_events = [json.loads(line) for line in service.node_logs['A'].read_text().splitlines()
                      if line.startswith('{')]
        summaries = [event for event in log_events if event.get('event') == 'overload_summary']
        assert summaries and not any(event.get('event') == 'request_complete' and event.get('status') == 503
                                     for event in log_events)
        record('aggregate-overload-logging', summaries=len(summaries), metrics=metrics())
        # A real executing SQL statement holds admission through all three
        # transport attempts; a lock wait would hit PostgreSQL's 2s lock timeout.
        service.sql(f"CREATE FUNCTION public.diamond_remediation_pause() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(4.7); RETURN NEW; END $$; CREATE TRIGGER diamond_remediation_pause BEFORE INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION public.diamond_remediation_pause()")
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as worker:
            held = worker.submit(target.request, 'create', 700004)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if int(service.sql("SELECT count(*) FROM pg_stat_activity WHERE wait_event='PgSleep'")):
                    break
                time.sleep(.01)
            probe = client('budget')
            stdout, stderr = probe.communicate(timeout=6)
            assert probe.returncode == 0, (stdout, stderr)
            assert held.result(timeout=3)[0] == 200
            record('generated-client-retry-budget', observations=stdout.splitlines())
        service.sql(f'DROP TRIGGER diamond_remediation_pause ON {table}; DROP FUNCTION public.diamond_remediation_pause()')
        await_metric('active_requests', 0)
        # Exercise transport reuse independently of the load generator.
        connection = http.client.HTTPConnection('127.0.0.1', target.port)
        for _ in range(2):
            connection.request('GET', '/_diamond/health/live', headers={'Host': service.authority})
            response = connection.getresponse()
            assert response.status == 200
            response.read()
            current = connection.sock.getsockname()
            if _ == 0:
                first = current
            else:
                assert current == first
        connection.close()
        reset_state(service, target, 100)
        target.connection_reuse = True
        first = None
        for sequence, operation in enumerate(('read', 'page', 'denied', 'update', 'create')):
            status, body, _ = target.request(operation, sequence)
            assert target.valid(operation, status, body, sequence), (operation, status, body)
            address = target.local.connection.sock.getsockname()
            if first is None:
                first = address
            assert address == first
        target.close_connection()
        record('persistent-connection', same_socket=True)
        service.stop_postgres()
        probe = client('generic', authenticated=True)
        stdout, stderr = probe.communicate(timeout=5)
        assert probe.returncode == 0, (stdout, stderr)
        record('generated-client-generic-failure', observations=stdout.splitlines())
        service.start_postgres()
        assert target.request('read', 1)[0] == 200
        for config in ('request_limit=0', 'queue_limit=4097', 'queue_timeout_ms=0',
                       'blocking_threads=65', 'database_connections=5', 'unknown=1'):
            manifest.write_text(original + '\n[runtime]\n' + config + '\n')
            outcome = service.cli('check', manifest, success=False)
            assert outcome.returncode != 0
        manifest.write_text(original)
        record('configuration-rejection', invalid_cases=6)
        evidence['status'] = 'PASS'
    except BaseException as failure:
        evidence['error'] = repr(failure)
        raise
    finally:
        if lock:
            lock.kill()
            lock.communicate(timeout=3)
        service.close()
        evidence['shutdowns'] = service.shutdowns
        evidence['cleanup_errors'] = service.cleanup_errors
        if not service.cleanup_errors:
            shutil.rmtree(root)
            evidence['temporary_root_removed'] = True
        options.report.parent.mkdir(parents=True, exist_ok=True)
        options.report.write_text(json.dumps(evidence, indent=2))
        print('Report:', options.report, flush=True)
    assert not service.cleanup_errors


if __name__ == '__main__':
    main()
