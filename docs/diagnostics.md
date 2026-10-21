# Diamond diagnostic reference

Compiler failures use a stable category, one-based Unicode source location,
bounded source excerpt, and caret:

```text
error[D004]: integer separators must be single underscores between digits
 --> example.dia:2:4
  |
2 |   1__2
  |    ^
```

Run `diamond explain D004` for the category and shortest remedy. Codes are
case-insensitive at that command boundary. Compiler diagnostics go to standard
error; successful `check` summaries go to standard output. Excerpts replace
terminal control and bidirectional formatting characters, but still contain
ordinary source text. Do not place secrets in source or publish diagnostic logs
without reviewing them.

Runtime failures such as denied authority, invalid external input, exhausted
budgets, database errors, and uncertain transactions do not pretend to be source
diagnostics. HTTP adapters return sanitized responses for those failures.

| Code | Category |
| --- | --- |
| D001 | Tab character |
| D002 | Inconsistent indentation |
| D003 | Empty HTML template |
| D004 | Invalid integer literal |
| D005 | Invalid string literal |
| D010 | Source syntax |
| D020 | Typed HTML syntax |
| D101 | Duplicate declaration |
| D102 | Invalid record field |
| D103 | Invalid type |
| D110 | Invalid expression |
| D111 | Invalid operator |
| D112 | Unknown value |
| D113 | Unknown field |
| D114 | Invalid field access |
| D115 | Invalid unary operation |
| D116 | Invalid function call |
| D117 | Invalid call arguments |
| D118 | Invalid record construction |
| D119 | Invalid repository operation |
| D120 | Invalid route use |
| D121 | Invalid form |
| D122 | Invalid return value |
| D123 | Invalid match |
| D124 | Invalid propagation |
| D125 | Invalid selection |
| D200 | Invalid policy |
| D201 | Unsafe policy expression |
| D204 | Policy-controlled mutation |
| D301 | Missing application effect |
| D302 | Invalid transaction |
| D310 | Invalid registered host operation or signature |
| D320 | Reserved compiler symbol in source |
| D330 | Invalid browser component type or scope |
| D401 | Route conflict |
| D402 | Invalid route path |
| D403 | Route parameter mismatch |
| D405 | Reserved runtime route |
| D900 | Feature intentionally outside the implemented MVP |

The category is stable for tooling. The detailed message and remedy may become
more precise without changing the code.
