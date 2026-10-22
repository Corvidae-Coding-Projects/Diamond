#!/usr/bin/env python3
"""Materialize independent semantic cases as shared Diamond and expected browser text."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parent


def main():
    spec = importlib.util.spec_from_file_location('independent_semantics', DIRECTORY / 'semantics.py')
    semantics = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(semantics)
    definitions, values, probes, exact = [], [], [], []
    for index, case in enumerate(semantics.cases()):
        name = ('probe_' if case['reject'] else 'case_') + str(index).zfill(3)
        definition = case['source'].replace('fn main()', ('export fn ' if case['reject'] else 'fn ') + name + '()')
        definitions.append(definition)
        exact.append(case | {'function': name})
        if case['reject']:
            probes.append({'function': name, 'case': case['name'], 'expected': 'uncaught runtime failure; event preserves prior state'})
        else:
            expression = name + '()'
            if 'fn main() -> Bool:' in case['source']:
                expression = 'format_bool(' + expression + ')'
            label = f'{index:03d}-{case["name"]}:'
            values.append((semantics.literal(label) + ' + ' + expression, label + case['expected']))
    definitions.append('''fn format_bool(value: Bool) -> Text:
  match value:
    true => "true"
    false => "false"

''')
    definitions.append('''enum CorpusPhase:
  Waiting
  Assigned(owner: Text, count: Int)

type CorpusKey = Id<"CorpusKey">

type CorpusRecord:
  label: Text = "default <text>"
  count: Int = 17

fn option_text(value: Option<Int>) -> Text:
  match value:
    Some(number) => "some:" + int_text(number)
    None => "none"

fn phase_text(value: CorpusPhase) -> Text:
  match value:
    Waiting => "waiting"
    Assigned(owner, count) => owner + ":" + int_text(count)

fn default_record_text() -> Text:
  let value = CorpusRecord()
  value.label + ":" + int_text(value.count)

fn overridden_record_text() -> Text:
  let value = CorpusRecord(label: "custom 雪")
  value.label + ":" + int_text(value.count)

''')
    additions = [
        ('option-some', 'option_text(Some(9007199254740993))', 'some:9007199254740993'),
        ('option-none', 'option_text(None)', 'none'),
        ('enum-payload', 'phase_text(Assigned("owner 雪", 41))', 'owner 雪:41'),
        ('enum-unit', 'phase_text(Waiting)', 'waiting'),
        ('nominal-id', 'id_text(CorpusKey("independent-17_x"))', 'independent-17_x'),
        ('record-defaults', 'default_record_text()', 'default <text>:17'),
        ('record-override-default', 'overridden_record_text()', 'custom 雪:17'),
    ]
    for name, expression, expected in additions:
        label = 'extra-' + name + ':'
        values.append((semantics.literal(label) + ' + ' + expression, label + expected))
        exact.append({'name': name, 'expression': expression, 'expected': expected, 'reject': False})
    body = ['export fn corpus() -> List<Text>:', '  let values_0 = [' + ', '.join(expression for expression, _ in values[:40]) + ']']
    for index, (expression, _) in enumerate(values[40:], 1):
        body.append(f'  let values_{index} = list_append(values_{index - 1}, {expression})')
    body.append(f'  values_{len(values) - 40}')
    definitions.append('\n'.join(body) + '\n')
    output = DIRECTORY / 'browser_corpus'
    output.mkdir(exist_ok=True)
    (output / 'shared.dia').write_text('\n'.join(definitions))
    (output / 'expected.json').write_text(json.dumps([text for _, text in values], ensure_ascii=False, indent=2) + '\n')
    (output / 'cases.json').write_text(json.dumps({'seed': semantics.SEED, 'valid_values': len(values), 'error_probes': probes, 'exact_cases': exact}, ensure_ascii=False, indent=2) + '\n')
    ui = ['export component App:', '  state values: List<Text> = []',
          '  state output: Text = "prior value preserved"', '  derived count: Int = len(values)',
          '  on run:', '    values = corpus()', '  on recover:', '    output = "recovered"']
    for probe in probes:
        ui += ['  on ' + probe['function'] + ':', '    output = ' + probe['function'] + '()']
    ui += ['  html:', '    <main><h1>Independent browser semantic corpus</h1><button id="run-corpus" type="button" on-click={run}>Run independent corpus</button><p>Values: <strong id="corpus-count">{count}</strong></p><pre id="probe-output">{output}</pre><button type="button" on-click={recover}>Recover</button>']
    for probe in probes:
        ui += ['      <button type="button" on-click={' + probe['function'] + '}>Failure ' + probe['case'] + '</button>']
    ui += ['      <ul id="corpus-values"><each items={values} as="item" key={item}><li>{item}</li></each></ul>', '    </main>']
    (output / 'ui.dia').write_text('\n'.join(ui) + '\n')
    print(json.dumps({'directory': str(output), 'valid_values': len(values), 'error_probes': len(probes)}))


if __name__ == '__main__':
    main()
