# Dynamic select regression

This project preserves the browser-language behavior used by Diamond Forge's
recognition-first workspace controls. A bound `select` may render keyed dynamic
`option` children through `each`; server rendering emits direct options and the
browser keeps the selected text value through hydration and updates.

Replay the compiler and server-render boundary with:

```sh
cargo run -- check verification/regressions/w3_dynamic_select/diamond.toml
cargo run -- build verification/regressions/w3_dynamic_select/diamond.toml --out NEW_EMPTY_PATH
```

In a real browser, choose `snow 雪`; `#selection` must update to the exact same
Unicode text and the select must remain on that option after the reactive patch.
