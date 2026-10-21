# DIAMOND

**Beautiful code. Hard guarantees.**  
*A secure-by-construction programming language for the web*

**Language Design Specification | Version 0.3 | 21 October 2026**  
**Status:** Architectural proposal. Examples are proposed Diamond syntax, not executable implementations.  
**Supersedes:** Diamond Language Design v0.2 (21 October 2026).

---

## 0. Executive summary

Diamond is a web-native, statically typed programming language with Ruby-like readability and a Rust-inspired commitment to eliminating preventable errors by construction. Its language, standard library, compiler, web runtime, and deployment tooling are designed together. They are not separate layers in which security can quietly become optional.

Diamond's primary innovation is not a particular syntax, garbage collector, or templating engine. It is an allocation of responsibility:

> **Diamond's First Law: If a safety feature requires a developer to remember it, they shouldn't have to remember it.**

When a protection can be applied universally and correctly by the language, compiler, standard library, or runtime, it **MUST** be automatic. A developer should not remember to HTML-escape output, parameterize database values, prevent accidental secret serialization, close a transaction, perform a CSRF check on a browser form, or call authorization middleware before accessing protected records.

Where protection depends on a business decision the language cannot infer, Diamond **MUST** require that decision at the appropriate boundary and then enforce it everywhere. For example, Diamond cannot decide who *ought* to delete a post. It can require a deletion policy and prevent the deletion operation from proceeding without applying it.

The intended result is an approachable language in which ordinary programs are secure by construction within a stated trust boundary; dangerous operations are explicit, rare, capability-limited, and auditable.

### 0.1 Changes in v0.3

This revision specifies previously incomplete security contracts:

1. Transaction results distinguish confirmed commit, confirmed abort, and an indeterminate outcome; cancellation and connection failure do not imply rollback.
2. Managed mutable values belong to one task unless accessed through checked synchronization; GC and structured task lifetimes alone do not establish freedom from data races.
3. Capability scopes propagate through closures, callbacks, containers, and dynamic dispatch without granting ambient or longer-lived authority.
4. A restricted portable policy core has defined query composition, update pre/post-state checks, and rejection rules for unsupported translations.
5. Browser WebSockets receive automatic origin enforcement, distinct connection scopes, and authorization at message and delivery boundaries.

The roadmap now establishes authority, concurrency, and policy semantics before building the safe web backend. Formal-core obligations, adversarial tests, and release acceptance criteria cover these contracts.

### 0.2 Architectural decisions retained from v0.2

The following architectural choices remain in force:

1. Tracing generational garbage collection, compiler-selected request arenas, and affine ownership of security-sensitive resources.
2. First-class typed authorization policies, enforced automatically at protected operations, default deny.
3. Internal effect inference with explicit effect budgets at module, application, and public API boundaries.
4. Native server builds, server-rendered HTML, generated JavaScript by default for client interactions, optional WebAssembly for compute.
5. Standard CSS with checked references and typed dynamic style values; no replacement CSS language.
6. Versioned, compatibility-checked expand/backfill/contract database migrations.
7. Structured concurrency, scoped streaming and WebSocket lifetimes, and capability-bound uploads.
8. Formal specifications for the security core plus adversarial, property-based, fuzz, mutation, and differential testing for the implementation.

**No ambient authority** remains a foundational design constraint. Everyday examples must not rely on optional security rituals.

---

## 1. The First Law and its consequences

> **If a safety feature requires a developer to remember it, they shouldn't have to remember it.**

This is a design constraint, not a marketing slogan. Every language feature and standard-library API must be reviewed against it.

### 1.1 Safety ownership hierarchy

When adding a protective mechanism, Diamond's designers must try these solutions **in order**:

1. **Make the unsafe state unrepresentable.** Use typed representations, capabilities, and restricted construction.
2. **Have the compiler enforce the rule.** Reject code that violates an invariant or omits a required decision.
3. **Have the runtime enforce the rule automatically.** Use generated checks where the answer depends on runtime facts.
4. **Have the deployment system enforce the rule.** Validate configurations, migrations, and released artifacts.
5. **Require explicit exceptional authority.** If a safe generalization is impossible, isolate the dangerous operation behind a narrow, audited escape hatch.

Documentation and lint warnings are complementary defenses, never substitutes for a feasible mandatory control.

### 1.2 No security by remembering a call

In safe Diamond code:

| Developer must not remember to… | Diamond's responsibility |
| --- | --- |
| Escape HTML and attribute values | Typed DOM/HTML nodes and context-checked attributes |
| Parameterize SQL | SQL represented as a typed query tree, values bound structurally |
| Validate form *shape* | Typed request parsers generated from declared input types |
| Add CSRF middleware | Browser-session mutation routes receive automatic CSRF enforcement |
| Add authorization checks before data operations | Policies execute at the protected data/service operation |
| Prevent a secret from entering logs or a browser bundle | Secret types and sink/serialization restrictions |
| Release a transaction or connection | Scoped affine cleanup, rollback where possible, and explicit indeterminate outcomes |
| Restrict outbound network requests | Explicit capabilities with runtime destination enforcement |
| Avoid a write side effect in a read route | Route/effect compatibility enforced by the compiler |
| Check whether a dependency has filesystem/network privileges | Package capability manifests and application capability budgets |

Some *decisions* cannot be automated. Diamond can require a policy, a trusted outbound host list, or an explicitly public route. Those are application requirements, not optional repetitions of a safety check.

### 1.3 The omission test

For every security feature, the language team must ask:

> If the programmer simply forgets this step, does the system silently become insecure?

If yes, the design has failed the First Law unless there is a demonstrated technical reason why the obligation cannot be transferred to the compiler or runtime. A compiler error asking the developer for a missing business policy is acceptable. A successfully compiled program silently skipping the check is not.

### 1.4 Failure behavior

When safety checks cannot be completed, Diamond defaults to **deny or fail closed**. A missing policy, unavailable identity service, invalid token, prohibited destination, unverified dependency, or incompatible migration must not quietly degrade into permissive behavior.

Failures must also be debuggable: clear errors, traceable policy decisions, and useful local diagnostics without revealing secrets.

---

## 2. Goals, non-goals, and threat model

### 2.1 Goals

- **Friendly authoring:** readable syntax, type inference, fast feedback, clear errors, minimal boilerplate.
- **Strong static types:** algebraic data types, generics, pattern matching, explicit fallibility, no implicit coercion or null sentinel.
- **Automatic web safety:** safe HTML, forms, database access, route dispatch, browser sessions, security headers, and resource cleanup.
- **Authority confinement:** no unscoped access to network, filesystem, subprocesses, secrets, databases, or privileged services.
- **Automatic policy enforcement:** authorization happens at the operation being protected, not by convention in a controller.
- **One language across web boundaries:** typed server code, browser components, and interprocess messages.
- **Auditable exceptions:** a machine-readable inventory of unsafe operations, external code, permissions, and deployment relaxations.
- **Operational safety:** checked schema evolution, dependency provenance, deterministic builds, secure production defaults, and testable claims.

### 2.2 Non-goals

- Guaranteeing the correctness of a developer's business policy. A deliberately permissive policy remains permissive.
- Proving a system secure against a compromised operating system, browser, hardware, compiler, database, or malicious administrator.
- Making arbitrary foreign-language libraries inherit Diamond's safety guarantees.
- Preserving Ruby's runtime semantics, supporting arbitrary Ruby gems, or reproducing Rails conventions.
- Replacing HTML, CSS, HTTP, SQL, or JavaScript as web standards.
- Treating all possible runtime facts as statically knowable.

### 2.3 Threat model

Diamond is designed to defend against common errors and hostile inputs within an otherwise correctly configured and implemented system: XSS, SQL injection, path traversal, SSRF, CSRF, missing authorization checks, accidental disclosure of secrets, unbounded resource use, unsafe concurrency, and accidental overprivilege in dependencies.

The language also aims to reduce the blast radius of partially compromised application components by enforcing capabilities at runtime.

Its guarantees are conditional on the **trusted computing base (TCB)**: compiler, safe standard library, runtime, security-sensitive adapters, deployment policy engine, and underlying platforms. Software cannot honestly promise more than its TCB supports.

### 2.4 Normative terms

**MUST / MUST NOT:** required for a conforming Diamond implementation.  
**SHOULD / SHOULD NOT:** expected unless an explicit, documented exception applies.  
**MAY:** optional behavior consistent with the model.

---

## 3. Application development experience

An ordinary web developer should be able to write a small application without seeing an effect handler, a borrow annotation, an HTML escaping function, or a CSRF token.

```diamond
app Forum:
  database Postgres<ForumSchema>
  session SecureCookie
  routes Posts

# Public access is deliberately declared. All other routes
# require a valid authenticated session by default.
public route GET "/posts/{id:PostId}" as PostDetails:
  let post = posts.get(id)?

  html:
    <article>
      <h1>{post.title}</h1>
      <p>{post.body}</p>
    </article>

route POST "/posts":
  let form = request.form<NewPost>()?
  let post = posts.create(form)?
  redirect route_url(PostDetails, post.id)
```

The following are **automatic**, with no calls to remember:

- `{post.title}` and `{post.body}` become safe text nodes.
- `posts.get` and `posts.create` construct parameterized queries.
- Database policy rules are applied at the data access boundary, including ownership and tenant scoping.
- The private POST route requires a valid session, and the authenticated principal is available through its compiler-provided request context.
- Cookie-authenticated POST handling includes CSRF protection.
- The typed `NewPost` parser verifies shape, field lengths, and domain validators associated with that type.
- The public GET can reveal only posts that its read policy permits anonymous visitors to see.
- A request cannot acquire filesystem, secret, or network authority merely by calling a library.

The app still needs meaningful domain declarations and policies. The absence of those declarations is a compilation or startup error, not silent authorization bypass.

This is illustrative syntax. The v0.3 specification fixes the **behavioral contract** before finalizing exact punctuation.

---

## 4. Core language

### 4.1 Syntax and readability

Diamond uses indentation-sensitive blocks, named arguments, straightforward literals, concise function syntax, and pattern matching. It borrows Ruby's readability without dynamic dispatch being the default.

```diamond
type UserId = Id<"user">

type User:
  id: UserId
  name: Text
  email: Email

enum AccountState:
  Active
  Suspended(reason: Text)
  Closed

fn greeting(user: User) -> Text:
  "Hello, #{user.name}!"

fn can_sign_in(state: AccountState) -> Bool:
  match state:
    Active => true
    Suspended(_) | Closed => false
```

### 4.2 Type rules

- Statically checked types with **local inference**; public data and exported interfaces have explicit stable contracts.
- Immutable bindings by default; mutation uses an explicit `var` or a scoped mutable resource.
- Algebraic data types and exhaustive pattern matching.
- `Option<T>` instead of `null`; `Result<T, E>` for expected failure.
- No implicit numeric/string coercion, truthiness conversions, or unchecked downcasts.
- Generics and traits/protocols, with no implicit authority gained through trait implementations.
- Interfaces can carry effect budgets and capability constraints.
- The compiler checks call-site and module boundaries without requiring everyday type annotations.

### 4.3 Strings are never code

`Text` is human-readable text, not universal syntax. Interpolation such as `"hello #{name}"` creates `Text` only. It cannot be implicitly converted into SQL syntax, HTML markup, shell commands, executable JavaScript, security principals, or unrestricted paths.

Structured domains have structured builders. Any operation that intentionally treats arbitrary text as code is an explicitly privileged escape hatch.

### 4.4 Errors

Expected failures use `Result` and `?`. A route's error mapping is generated by the framework using typed error classes. User-visible responses must not contain stack traces or sensitive internals in production.

Unhandled programmer defects terminate the affected request/task according to runtime isolation rules and generate safe diagnostic events; they do not bypass resource cleanup.

---

## 5. Memory management and ownership

**Decision:** Generational tracing GC for ordinary managed values; optional compiler-allocated request arenas for proven non-escaping allocations; affine ownership for resources and authority.

Diamond is not going to expose Rust-style lifetime annotations as a normal requirement of web development.

### 5.1 Ordinary values

- Managed objects are memory safe under the safe language and runtime.
- The compiler MAY place request-local, non-escaping objects in an arena, provided escape analysis proves that this does not alter observable behavior.
- Longer-lived objects use the GC heap.
- Cyclic references are collected without developer intervention.
- Request deadlines and allocation budgets are enforced by the runtime.

### 5.2 Affine resources

Transactions, file capabilities, scoped network grants, one-use tokens, and other authority-bearing resources cannot be freely cloned or silently retained after their scope closes. Their ownership is tracked by the compiler; a resource may be used at most once where consuming semantics apply.

```diamond
transaction db:
  let user = users.get(id)?
  users.update_name(user, new_name)?
```

On normal completion of the block, the runtime attempts to commit and reports success only after the adapter confirms commit. On an error or cancellation, it automatically performs the cleanup appropriate to the transaction state. An interrupted commit or lost connection can leave the database outcome unknown; the runtime MUST NOT report rollback without evidence. Section 12.3 defines the result and cleanup contract. The programmer does not call `commit`, `rollback`, or `close` in the ordinary pattern.

Some durable handles can be used repeatedly while inside a declared scope. This does not weaken the rule that they cannot escape or be duplicated without authority.

### 5.3 Secrets and memory

Secrets containing sensitive bytes should use specialized storage with best-effort zeroization and exclusion from diagnostics and memory dumps when supported. GC alone is not a guarantee of prompt erasure. Hardware, swapping, crash handling, and FFI remain outside any absolute zeroization promise.

### 5.4 Managed mutation and freedom from data races

Ordinary mutable managed values are **task-owned**. An immutable binding does not make the reachable object graph deeply immutable, and garbage collection does not authorize concurrent mutation.

Safe Diamond MUST enforce these rules:

- Deeply immutable data may be shared between tasks. A value containing mutable state or scoped authority is not implicitly deeply immutable.
- A mutable object graph may move to another task only when the compiler proves that usable aliases do not remain in the sending task or any other task. GC references, container contents, and closure captures participate in this check. Otherwise, use an explicit message copy or checked shared storage.
- Concurrent access to mutable shared storage is available only through standard synchronization abstractions with compiler-checked access scopes. Mutable views and iterators obtained under a lock cannot escape its protection. Safe atomics expose only operations covered by the specified memory model.
- An ordinary lock-protected access scope MUST NOT suspend at `await` or another yielding operation. Any asynchronous synchronization abstraction requires its own specified ownership, cancellation, and release rules.
- Authority-bearing handles may be used by child tasks only through adapter-declared concurrency-safe operations and scope-checked borrowing. Sharing data does not clone a capability or extend its lifetime.

The compiler MUST reject an unproven transfer or unsynchronized shared mutation. Module interfaces and generated native/browser code preserve these rules; an unsafe/native boundary cannot silently confer shareability. The formal core must define the allowed synchronization and memory-ordering behavior before a conforming parallel backend ships.

These rules prevent data races in safe code. They do not prove freedom from deadlocks, incorrect business ordering, or distributed consistency errors.

---

## 6. Trust boundaries and typed input

All bytes from requests, network responses, files, database fields, and external APIs are **untrusted at entry** unless an explicitly verified provenance rule applies.

Diamond distinguishes parsing and validation from authorization:

- `Text` can safely be displayed as text without being "trusted."
- `Email`, `UserId`, `PositiveInt`, and bounded text types express validated shape.
- A successfully parsed identifier is **not** permission to access the identified object.
- A verified signature confirms authenticity under a configured key, **not** that the action is authorized.
- Domain validation derives from type declarations so handlers do not repeat input checks.

```diamond
type NewPost:
  title: Text<length: 1..120>
  body: Text<length: 1..10_000>

route POST "/posts":
  let form = request.form<NewPost>()?
  posts.create(form)?
  redirect "/posts"
```

The parser enforces request limits, shape, encoding, and declared validators. The database operation applies authorization. HTML output remains safe even for accepted content that contains markup-looking characters.

Arbitrary custom validation may be written as a typed validator, registered with the field type, and invoked automatically whenever the field is parsed.

---

## 7. Effects, capabilities, and no ambient authority

**Decision:** A compiler-tracked effect system with scoped runtime capabilities. Implementation details are inferred; security budgets are explicit at exposed boundaries.

### 7.1 No ambient authority

Normal Diamond code MUST NOT silently access:

- Files or directories.
- Arbitrary network destinations.
- Processes and shell commands.
- Environment variables or configuration secrets.
- Databases or privileged service APIs.
- Sensitive logging sinks.
- The identity or permissions of an arbitrary request.

A package receives only the authority explicitly made available to it. Importing a package must not grant it ambient operating-system privileges.

### 7.2 Effects are automatically inferred inside the application

```diamond
fn format_post(post: Post):
  "#{post.title}: #{post.body}"
```

This function is pure. No annotation is needed.

```diamond
fn publish(post: NewPost):
  let saved = posts.create(post)?
  events.emit(PostPublished(saved.id))?
  saved
```

The compiler infers database-write and event-publish requirements. Those effects are checked against the calling module's and application's declared budgets. The code will not compile or run with authority it has not been granted.

### 7.3 Budgets at public boundaries

```diamond
module Publishing
  allows DbWrite<Posts>, EventPublish<PostEvents>

app Forum:
  provides DbRead<Posts>, DbWrite<Posts>
  provides EventPublish<PostEvents>
```

For public libraries, exported APIs publish an explicit effect contract, generated or initially proposed by the compiler and committed in the package interface. An implementation change cannot quietly expand an API's privileges.

**The developer declares permitted authority once at a meaningful boundary, not at every call site.** If a dependency requires new authority, the compiler and package manager present the change for explicit approval.

### 7.4 Scoped context without ambient globals

The compiler passes opaque request and service capabilities through typed effect handlers or an equivalent lexical mechanism. Code can use convenient injected database APIs without manually threading authorization parameters through every function.

There is no globally mutable or thread-local `current_user` that can be accidentally reused across requests. The runtime binds the principal to each request scope, and access operations receive that scoped principal implicitly through the checked context.

An effect requirement describes possible use of authority; it does not create a grant. At each protected operation, the usable authority MUST satisfy all of the following: a valid capability carried by the checked lexical context or an explicitly delegated operation handle, the callee's effect contract and module/package budget, and the application's deployment budget. An import or effect annotation cannot manufacture a missing capability.

The following propagation rules are normative:

- **Closure capture:** A closure's type includes its effects, captured authority, affinity, and scope bounds. A closure capturing request authority cannot enter a global, detached job, longer-lived callback registry, or return value that outlives that request.
- **Containers and abstraction:** A container or object holding a scoped value inherits that value's scope restriction. Generics, traits, interface objects, type erasure, and serialization MUST NOT hide or remove effects, affinity, or scope bounds.
- **Callbacks:** APIs declare callback effects explicitly or through bounded effect parameters. Invoking a callback remains subject to that contract and the receiving boundary's permitted callback effects. Accepting a callback does not grant the package general access to the callback's captured capabilities.
- **Dynamic dispatch:** An interface provides a checked upper bound on the effects and authority of every implementation. Overrides cannot expand it. Dispatch through an unknown implementation cannot be treated as pure or authority-free.
- **Delegation:** A capability can be borrowed within its valid scope or moved when its ownership permits. Explicit attenuation may narrow resources, operations, destinations, or duration; delegation MUST NOT widen any bound or change the authenticated principal. Repeated or concurrent use requires an adapter-declared handle contract.
- **Scope exit:** All child work using borrowed authority must finish or be canceled and joined before that authority is released. Runtime checks invalidate handles on closure of their scope as defense in depth. A fresh request or background job receives a fresh verified context; it cannot reactivate an expired one.

Principal authority and scope tokens have restricted constructors. Constructing a user identifier or deserializing principal-shaped data cannot create a verified principal. Scope parameters may remain compiler-internal; ordinary application code need not write lifetime annotations. If a scope or effect bound cannot be inferred safely, compilation fails with a diagnostic at the boundary.

### 7.5 Runtime enforcement

A static effect annotation cannot stop a compromised native library or an unsafe backend from making a system call. Capability confinement therefore also needs runtime sandboxing, OS permissions, network egress controls, and isolated extension boundaries where available.

Each supported execution profile MUST state its isolation boundary and reject packages or operations whose declared confinement requirements it cannot enforce. An untrusted native extension requiring isolation MUST run behind an appropriate process or equivalent sandbox boundary; a manifest alone does not isolate libraries sharing an address space.

---

## 8. HTTP routing and secure request handling

### 8.1 Route exposure

Routes require one unambiguous access model:

```diamond
route GET "/account":             # Authenticated by default
  ...

public route GET "/about":       # Anonymous access explicitly declared
  ...
```

The ordinary `route` declaration defaults to requiring a verified session or another configured authentication mechanism. `public route` is an explicit interface decision, not the accidental result of forgetting middleware.

If the application has no configured authentication provider, private routes are rejected at build/startup rather than silently treated as public.

A declaration may bind a typed route handle, for example `public route GET "/posts/{id:PostId}" as PostDetails:`. `route_url(PostDetails, id)` uses that declaration's checked parameter types; a missing or ambiguous handle is a compile-time error.

### 8.2 Safe HTTP methods are read-only

GET and HEAD routes may not perform application state-changing effects. Observability effects with explicitly declared non-business mutation semantics may be allowed. A write operation in a GET route is a compile-time error. This prevents accidental state-changing links and helps preserve safe-method semantics.

A WebSocket upgrade handshake follows this read-only rule. Subsequent messages have separate checked operation/effect contracts under Section 16.2; the handshake's GET method does not make message effects read-only or exempt them from authorization.

### 8.3 Automatic security controls

- Strict parsers with bounded request sizes and timeouts.
- HTTPS-only deployment defaults, proxy trust configured explicitly.
- Secure, HttpOnly, SameSite session cookies with rotation and expiration.
- Security headers and an application-specific CSP generated by the compiler/runtime.
- Restrictive CORS defaults; credentials are not exposed to arbitrary origins.
- Safe redirects using typed internal routes or explicitly approved external destinations.
- Request context and privilege boundaries tied to cancellation and lifetime.
- Rejection of ambiguous host, forwarding, and protocol interpretation unless trusted proxies are configured.

Security headers must reflect the actual deployment topology. The deployment tool validates reverse-proxy, TLS, and cookie assumptions; it does not pretend that a source-level declaration can inspect all production networking.

### 8.4 CSRF without developer memory

State-changing routes using ambient browser credentials (especially cookie sessions) automatically receive CSRF defenses. The runtime supplies tokens or equivalent validated origin-bound mechanisms in rendered forms and verifies them on submission.

Developers neither create nor check the tokens manually.

Machine-to-machine APIs, signed webhooks, and non-cookie bearer-authenticated endpoints may use specialized route types with their own proof requirements. Disabling CSRF without a replacement guarantee requires an auditable privileged exception, not an innocuous boolean flag.

Browser WebSocket handshakes using ambient credentials MUST receive automatic origin enforcement under Section 16.2. Form-token generation and CORS configuration alone do not satisfy that requirement.

---

## 9. Authentication and authorization

**Decision:** Built-in authentication adapters plus **typed, default-deny authorization policies automatically enforced at protected operations.**

### 9.1 Authentication is not authorization

The runtime verifies identity and produces a request-bound principal. A valid session does not grant access to arbitrary data. Policies decide which principal may perform which action on which resource under current application state.

### 9.2 Policy authoring

Policies use ordinary, restricted Diamond expressions rather than a second full programming language:

```diamond
policy Posts:
  read(post, by principal):
    post.published or principal.is_user(post.author)

  create(input, by principal):
    principal.authenticated

  delete(post, by principal):
    principal.is_user(post.author) or principal.has_role(.admin)
```

Policy expressions are typed and side-effect free. The compiler checks referenced fields, principals, actions, and coverage of protected operations.

For public reads, the principal may be `Anonymous`. The built-in predicates `principal.is_user(id)` and `principal.has_role(role)` return `false` for anonymous principals; they cannot accidentally dereference a nonexistent user. Policies may alternatively pattern-match explicitly on `Anonymous | SignedIn(user)`. Operations without a policy **deny by default**. An unsafe blanket `allow_all` must be clearly visible in code and audit output.

#### 9.2.1 Portable policy core

Purity alone does not make an arbitrary function suitable for a policy or translatable into SQL. The initial portable policy core permits only typed record fields, constants, the verified principal's declared attributes, Boolean operators, explicit optional-value matching, specified scalar comparisons, and the built-in identity/role predicates. User helpers MUST be acyclic, statically resolved compositions of this same subset. Recursion, arbitrary dynamic dispatch, I/O, implicit database reads, clocks, randomness, and unrestricted application functions are excluded.

Policy evaluation is total over valid typed inputs and bounded by runtime budgets. Only an explicit `true` grants access. Missing attributes, invalid stored values, evaluator errors, and budget exhaustion MUST NOT grant access or silently weaken a predicate. An evaluation failure produces a safe typed operation failure; it is not reinterpreted as a successful query with a partial result.

The reference evaluator defines scalar equality, ordering, optional-value behavior, and any admitted arithmetic. Adapters MUST preserve those semantics, including text collation and numeric bounds. SQL `NULL` or three-valued logic MUST NOT become an implicit grant. A policy or query combination an adapter cannot implement equivalently MUST be rejected during checking for that target, before deployment; there is no permissive or approximate translation fallback.

Relationship predicates may be added only with specified lookup authority, policy composition, dependency-cycle handling, and bounded execution. Until then, they are rejected in policies. Additional policy constructs require a normative semantic definition and adapter conformance cases before becoming part of safe Diamond.

### 9.3 Enforcement at the operation

```diamond
route DELETE "/posts/{id:PostId}":
  posts.delete(id)?
  redirect "/posts"
```

This does **not** forget authorization. `posts.delete` is a protected operation. Its generated adapter obtains the current principal, resolves the relevant resource, evaluates the `delete` policy, and performs the mutation only when authorized.

Policy inputs and operation order are defined as follows:

- `read(record, by principal)` governs visibility of the stored record in the operation's authorized snapshot.
- `create(proposed, by principal)` checks the complete proposed record after declared defaults and server-derived fields have been applied. A client cannot override those derived fields.
- `update(before, after, by principal)` receives both the stored pre-state and the complete proposed post-state. Protected changes to ownership, tenant, or other policy-relevant fields therefore cannot be authorized solely against the old record. A missing update policy denies updates.
- `delete(record, by principal)` checks the stored pre-state. A mutation's permission does not independently grant permission to return protected record contents; returned records require their read policy as well.

Authorization and mutation MUST be transactionally consistent, including database-backed facts on which a decision depends. The adapter MUST use predicates in the same statement or suitable locking/isolation with checked policy inputs. An application-side check followed by an unprotected write is forbidden. Database defaults, triggers, or generated fields that can change policy-relevant post-state must be modeled and checked before commit; unsupported combinations are rejected. If an adapter cannot preserve these obligations, it MUST reject the operation rather than introduce a time-of-check/time-of-use gap.

Query composition, bulk mutation behavior, and observable errors follow Section 12.2. The adapter cannot choose weaker semantics for a convenient query shape.

An application may define policies incorrectly. Diamond's claim is that **the configured policy cannot be omitted accidentally**, not that every policy expresses the morally or commercially correct rule.

### 9.4 Service-to-service access

Non-user jobs and services receive explicit machine principals and scoped grants. A background worker does not inherit a fictional all-powerful human session. Service identities, authorization rules, and approval/audit requirements are first-class.

### 9.5 Revocation

Session validation, permission changes, and revocation semantics are implemented by the runtime, with explicit bounded freshness for caches. Long-lived connections must revalidate at policy-defined boundaries. The compiler cannot know future permission changes.

---

## 10. Typed HTML and XSS resistance

HTML is parsed into a typed document/tree model, not assembled through untyped string concatenation.

```diamond
component PostCard(post: PublicPost):
  html:
    <article class="post">
      <h2>{post.title}</h2>
      <p>{post.body}</p>
      <a href={route_url(PostDetails, post.id)}>Read more</a>
    </article>
```

- `Text` interpolation always creates text nodes, escaped by the renderer as required.
- Attributes use context-appropriate types; URL-bearing attributes cannot take arbitrary executable schemes.
- Event handlers are compiler-checked client callbacks, not injected text.
- Inline script construction from text is excluded from the safe API.
- Raw HTML insertion is a privileged `UnsafeHtml` operation, permanently visible in audits and prohibited by default in sandboxed dependencies.
- The browser runtime generates a nonce/hash-aware Content Security Policy and compatible script loading strategies.

The implementation MUST test actual browser parsing behavior, including cross-context encodings and mutation-XSS cases, not merely unit test a nominal escaping function.

---

## 11. CSS and styling

**Decision:** Use standard CSS, not a new CSS language. Offer typed dynamic values and validated references.

```diamond
component Badge(status: Status):
  html:
    <span class={status.css_class}>
      {status.label}
    </span>

style:
  width: 240.px
  color: Color.red
  opacity: 0.8
```

Static stylesheets remain normal CSS. The compiler SHOULD resolve referenced class and identifier names against project stylesheets where this can be done reliably. Dynamic values use typed lengths, colors, enumerations, and constrained constructors.

Arbitrary untrusted text MUST NOT implicitly become executable CSS syntax or unsafe URL-bearing style values. A deliberate raw-style API, if provided, is privileged and auditable.

Compatibility with real-world CSS takes precedence over inventing an attractive but incomplete typed styling DSL.

---

## 12. SQL, data access, and transactions

### 12.1 Queries are structure, not strings

Database queries are typed ASTs checked against a schema. User-supplied values are bound as parameters automatically. A `Text` value cannot be treated as a table name, column name, or SQL fragment by implicit conversion.

```diamond
fn find_posts(author: UserId):
  query posts:
    select id, title, body
    where author_id == ^author
    order by created_at.desc
    all
```

Here `^author` is a structural bound parameter. Dynamic filtering must use constrained typed query combinators. No developer-written quoting or escaping function is required.

### 12.2 Policies are part of data access

Generated repositories enforce resource policies. Safe query semantics are defined over **authorized relations**, not over an unrestricted result filtered later:

1. Each base-table reference, including each alias, subquery, and side of a join, is restricted by its read policy before user-defined expressions, joins, aggregates, ordering, pagination, or limits can observe its records. Counts and aggregates use only these authorized inputs. A query involving several resources requires the applicable policy for each resource.
2. The optimizer may reorder operations only when the adapter can establish equivalent disclosure behavior. Potentially revealing expressions, including functions that fail differently for different values, MUST NOT run on hidden records. Post-filtering an unrestricted aggregate, joined result, or paginated page does not satisfy this contract.
3. A bulk query mutation selects candidates from authorized relations and requires its action policy for every selected record, including the proposed post-state of every update. The batch is atomic: any denied action or invalid post-state aborts the batch. It MUST NOT silently skip denied candidates. Returned records also require read authorization.
4. A direct operation addressed by an opaque identifier may apply its action policy without granting read access to the record. Nonexistent and unauthorized identifiers produce the same public failure class and response shape. Success discloses that the requested action was permitted; record contents, related records, and additional query predicates require their applicable read policies.
5. Operation results, affected-row counts, and errors MUST follow a declared disclosure contract. Safe defaults expose no hidden record values or distinctions between forbidden and nonexistent identifiers. Raw driver errors, constraint names, and conflicting values are not public errors.

Constraint success/failure itself can disclose information even after error-message redaction. A global uniqueness or referential-integrity rule that reveals facts outside the authorized relation requires an explicit disclosure decision, or a schema/operation design that confines the check to the authorized domain. The verifier MUST reject an undeclared disclosure; normalization alone is insufficient. Policies do not promise elimination of timing, resource-cost, or other covert channels. Supported disclosure classes and those residual channels must be documented per adapter.

The policy compiler MUST reject unsupported query shapes before deployment. It MUST NOT fall back to an unrestricted query, omit a resource's policy, or turn an evaluation error into a weaker filter. Database adapters must be tested against the reference authorized-relation semantics, including optional values, outer joins, pagination, and pre/post-state mutation checks.

Native driver calls, direct raw connections, and arbitrary SQL are outside safe data access. The build manifests them as privileged escape hatches and permits them only under the application's explicit authority budget.

### 12.3 Transaction lifecycle

`transaction db:` handles commit attempts, rollback attempts, cancellation, and cleanup. The developer chooses a transaction boundary where business semantics require it, but does not remember cleanup calls. The expression returns the block's value through `Result`; its public contract distinguishes three database outcomes:

| Outcome | Required evidence and behavior |
| --- | --- |
| **Committed** | The adapter confirmed commit. The result is successful; a completed block alone is insufficient evidence. |
| **Aborted(cause)** | The adapter established that the transaction did not commit. The typed error preserves the safe cause. A confirmed commit rejection or rollback can establish this outcome. |
| **OutcomeUnknown(operation_id)** | The adapter cannot establish whether the transaction committed. The typed error carries an opaque correlation identifier for diagnosis or supported reconciliation, not a promise that the database can recover the answer. |

The runtime MUST track whether a commit has been issued and which acknowledgments have arrived. If an error or cancellation is observed before commit is issued, it MUST NOT initiate commit and must attempt rollback. If commit has already been issued, cancellation cannot promise to undo it. Loss of the commit acknowledgment can produce `OutcomeUnknown` even when the database committed successfully. A failed cleanup attempt MUST NOT be presented as confirmed rollback.

Cleanup runs in a bounded cancellation-shielded scope. The runtime releases local handles and returns a connection to the pool only after the adapter confirms it is reusable and has no open transaction; otherwise it discards the connection. If the outcome remains indeterminate, the error MUST preserve that uncertainty. Process or power failure may prevent cleanup entirely and remains an external recovery concern.

`OutcomeUnknown` is a distinct error variant with its own generated route mapping and diagnostic. It MUST NOT become an ordinary validation failure, a claimed abort, or an automatic retry instruction. The runtime, generated clients, and adapters MUST NOT blindly replay an indeterminate transaction. Retry of a confirmed abort is permitted only under a checked replay-safe effect contract; otherwise the caller receives the typed failure. An explicit idempotency/reconciliation facility may resolve uncertainty only under its documented storage and protocol guarantees.

Loss of a mutation's HTTP/RPC response can likewise leave its outcome unknown to the client. Generated clients MUST NOT automatically replay such a mutation without an explicit idempotency/reconciliation contract, even when no typed server error was received.

The transaction scope excludes unmodeled implicit commits and unmanaged driver calls. Backend features whose commit behavior cannot satisfy this contract are rejected in safe transaction blocks.

Diamond cannot infer every business invariant that requires a transaction. The standard library should provide transaction-safe patterns and warn when a protected multi-step operation demonstrably requires atomicity.

### 12.4 External effects and replay

A database transaction does not roll back an email, payment, event publication, or other external side effect. The effect checker MUST distinguish transaction-local database effects from external effects. Automatic transaction replay MUST exclude external effects unless a typed adapter supplies a checked replay/idempotency contract. For atomic database changes and later event delivery, the standard library SHOULD provide a transactional outbox with explicit delivery and deduplication semantics. It MUST NOT imply universal exactly-once delivery.

---

## 13. Versioned schema and migration safety

**Decision:** Expand/backfill/contract migrations with automatic compatibility verification against live rolling-deployment versions.

```diamond
migration "add_display_name":
  expand:
    users.add_column(:display_name, Text.optional)

  backfill:
    users.each_batch:
      user.display_name = user.name

  contract:
    users.require(:display_name)
```

The migration system must:

1. Version schemas and retain deployment compatibility metadata.
2. Validate that the old and new application versions can coexist with each intermediate schema.
3. Execute safe additive phases before code requiring them.
4. Perform data backfills with bounded batches, resumability, and observability.
5. Delay destructive contracts until incompatible older application versions are absent and rollback requirements are resolved.
6. Stop deployment on incompatible or ambiguous transformations; require explicit approval for destructive changes.

Not all data transformations can be proven safe, and not all databases support equivalent transactional DDL. The tool must document those limitations rather than claiming universal rollback safety.

The programmer declares the desired evolution. The deployment system owns the mechanically checkable ordering and compatibility obligations.

---

## 14. Secrets, serialization, and client/server separation

`Secret<T>` is an opaque type with restrictive operations. It has no ordinary string conversion, debug representation, equality, logging, or generic serialization path.

```diamond
secret API_KEY: Secret<ApiKey>

fn charge(invoice: Invoice):
  payments.charge(invoice, credential: API_KEY)?
```

The secret is used through a typed integration that has permission to consume it; application code does not need to reveal it as raw text. Any raw reveal facility requires a narrow `SecretReveal` capability and appears in an audit report.

All data crossing into a browser or public API is checked against a `ClientSafe` / `Serializable` contract. Secret-bearing types and server-only capabilities are rejected at build time. Generated RPC schemas are versioned and explicit about visibility.

The system must not accidentally expose secrets in stack traces, panic messages, telemetry attributes, generated source maps, compiled client bundles, or error pages. Runtime integrations need automated redaction and targeted security testing.

A library that is legitimately authorized to use a secret can still abuse it; safe APIs and capability limits reduce exposure but do not prove the intentions of the library.

---

## 15. Browser compilation and full-stack model

**Decision:** Server-native by default, server rendering first, JavaScript code generation for typical client components, optional WebAssembly for computation.

```diamond
component Counter:
  state count = 0

  html:
    <button onclick={count += 1}>
      Clicked {count} times
    </button>
```

The compiler generates minimal browser code, hydration or event wiring where required, and typed message contracts between server and client. Developers shouldn't manually synchronize API definitions or write hydration boilerplate for standard components.

Browser and server code are separated by compilation capabilities:

- Browser code cannot import server-only APIs or private secrets.
- The browser does not possess database authority simply because it shares types with the server.
- Generated JavaScript interop APIs are checked and expose their unsafe boundaries.
- Client-side validation improves ergonomics, but all security-relevant checks are independently enforced on the server.
- WebAssembly is an opt-in target for workloads where its benefits justify browser interop costs.

JavaScript target generation does not make the browser trustworthy. Diamond relies on server-side enforcement for privilege and confidentiality.

---

## 16. Structured concurrency, streaming, WebSockets, and uploads

**Decision:** Asynchronous work uses structured concurrency, bounded channels, scoped resources, and runtime revalidation of long-lived authority.

### 16.1 Tasks and streams

Child tasks belong to a lexical task scope. All children MUST finish or be canceled and joined before the parent releases resources or authority borrowed by them. Safe code cannot detach a task carrying request authority. Durable background jobs use explicit job submission and receive their own verified machine context under Section 9.4. Shared values and captured mutable state obey Section 5.4; structured task lifetimes do not relax those rules.

```diamond
route GET "/events":
  stream Event:
    for event in events.subscribe():
      yield event
```

The runtime automatically handles cancellation, stream closure, subscription cleanup, deadline propagation, and flow control/backpressure. Buffer size, frame size, message rate, concurrent connection count, and connection lifetime have safe bounded defaults.

### 16.2 WebSocket handshakes and message authority

WebSocket messages use typed schemas, not arbitrary unbounded JSON. The runtime enforces authentication at connection establishment, authorization for each protected message operation, and revalidation within the configured bounded-freshness contract. A once-authenticated connection does not silently confer perpetual authority.

Browser WebSocket endpoints using ambient credentials MUST automatically validate the handshake's `Origin` against exact allowed origins derived from the verified deployment configuration. Matching uses the normalized scheme, host, and effective port, not a suffix or substring test. Wildcard credentialed origins are forbidden. An unlisted, malformed, multiple, missing, or `null` origin is rejected by the browser endpoint. Generated clients need no application-authored check. Cookie attributes, CORS settings, and ordinary form CSRF tokens are complementary controls, not replacements for this handshake check.

Non-browser clients use an explicitly declared endpoint/authentication profile that requires independently verified credentials and does not authenticate through ambient cookies. It MUST NOT be selected automatically because an origin is missing or invalid. An `Origin` value is not authentication for such clients; a non-browser client can forge it.

The upgrade handshake is read-only under Section 8.2. After successful verification, the runtime creates a distinct connection-bound principal and capability scope. Request-scoped capabilities cannot simply be retained by a connection. Each inbound message runs under its declared effect budget and applies resource policies at its protected operations. Outbound events require the recipient's applicable read authorization before protected content is sent, within the documented freshness bound; a subscription is not permanent read permission.

The runtime MUST enforce session expiration and revocation revalidation for active connections. Failure to revalidate denies the operation or closes the connection; it MUST NOT extend stale authority. Closing a connection cancels and joins its child work and releases its scoped capabilities.

### 16.3 Uploads

Resumable uploads use opaque, unforgeable handles scoped to a principal, destination, size quota, content policy, and expiration. Chunk processing enforces limits and atomic completion. Uploads are not made executable or publicly served by default; scanning and content-type policies are deployment controls.

The programmer expresses intent (`stream`, `websocket`, `upload`); the runtime handles the repetitive safety machinery.

---

## 17. Filesystem and network capabilities

### 17.1 Filesystem

Safe filesystem APIs operate through directory capabilities, not unrestricted string paths. A user-supplied path segment is not an absolute path and cannot simply escape a granted directory through `..`, symbolic links, or platform-specific aliasing.

The runtime must use OS primitives and filesystem-resolution rules that preserve the directory sandbox, including race-resistant open operations where supported. Unsupported safety requirements cause the operation to fail, not fall back to insecure path concatenation.

### 17.2 Network / SSRF

Outgoing requests require a scoped network capability. Egress policy is enforced on actual resolved and connected destinations, not merely on the spelling of a URL before DNS resolution.

Redirects, alternate IP families, DNS rebinding, proxy configuration, and internal address ranges must be checked against the policy. Requests to private or metadata endpoints require explicit authority. TLS verification is mandatory in safe clients; disabling it is a privileged, audited exception.

```diamond
app Forum:
  network:
    payments => https://api.payments.example
    images   => https://cdn.example
```

Application code using `payments` receives that destination's capability, not universal internet access.

The exact manifest syntax remains provisional, but destination-scoped runtime enforcement is mandatory.

---

## 18. Packages, dependency security, and build provenance

Diamond's package model is designed for a world where dependencies may be buggy or malicious.

- Dependency versions and content hashes are locked deterministically.
- Package manifests publish requested effects and unsafe/native extension requirements.
- Dependency updates that expand authority require explicit review or an organizational policy decision.
- Build outputs include SBOMs, dependency provenance, effect graphs, and unsafe-operation inventories.
- The compiler rejects transitive use of authority the root application did not grant.
- Packages are built and executed with constrained capabilities where the host OS supports them.
- Trusted signing/attestation sources and policies are configurable and explicit; signatures alone do not establish that code is benevolent.
- Reproducible builds are a design objective and a release gate for supported build targets.

Build-time scripts are particularly dangerous because they execute before application runtime restrictions apply. Diamond MUST sandbox build steps and require declared build-time capabilities, rather than allowing arbitrary installation scripts unrestricted machine access.

---

## 19. Interoperability and unsafe operations

Diamond must be useful with existing ecosystems. It will support controlled interoperability with JavaScript, C/ABI libraries, existing databases, web APIs, and native system facilities.

But interop may not silently erase Diamond's guarantees.

An `unsafe` operation must:

1. Be syntactically explicit and require an appropriate authority capability.
2. Identify the boundary being bypassed (e.g. raw SQL, unchecked HTML, arbitrary process execution, secret reveal, unchecked FFI).
3. Be prohibited or restricted for sandboxed/untrusted packages.
4. Appear in build and deployment audit manifests with provenance and call sites.
5. Remain subject to runtime sandboxing wherever technically possible.

A project may elect a **strict mode** that forbids classes of unsafe operations altogether. Ordinary safe projects should not have to enable strict mode to receive Diamond's core protections: strict mode narrows the exceptional authority permitted to experts, not the baseline security rules.

---

## 20. Compiler and runtime architecture

The reference implementation is written in Rust and separates a small semantic core from replaceable compilation backends.

| Layer | Responsibilities |
| --- | --- |
| Lexer / parser | Indentation-aware grammar; lossless syntax tree for editor tools |
| Name resolution / type checker | Generics, traits, variants, exhaustiveness, inference, module interfaces |
| Trust / effect checker | Resource and task ownership, checked sharing, closure/callback effects, scope propagation, capability delegation, serialization limits |
| Policy compiler | Portable policy core, coverage, authorized-relation semantics, pre/post-state checks, adapter rejection rules |
| Typed intermediate representation | Explicit moves, effects, scopes, safe HTML/query nodes, source locations |
| Interpreter / reference evaluator | Semantics-first execution and differential-testing oracle |
| Server backend | Native code generation, initially prioritizing a practical Linux target |
| Browser backend | Generated JavaScript and optional WebAssembly kernels |
| Runtime | GC, synchronization, scoped cleanup, transaction outcomes, auth/session, WebSocket origin checks, policies, networking, safe rendering |
| Build/deployment verifier | Capability manifest, schema compatibility, signed artifacts, security configuration |

An interpreter or reference evaluator is valuable early, both for rapid language iteration and as an independent behavioral oracle for optimized backends. Native backend selection (Cranelift, LLVM, or another strategy) should be driven by prototype measurements and correctness needs.

The runtime and compiler must not rely on a global mutable principal or unscoped thread-local security state. Safe APIs receive compiler-verified scope and capabilities.

### 20.1 Trusted computing base inventory

Every release should publish the components on which its guarantees depend: compiler passes, runtime services, rendering libraries, database adapters, cryptographic providers, generated browser bridges, and relevant sandbox integration. This inventory is a practical security artifact, not an assertion that all third-party components have been proven correct.

---

## 21. Tooling and developer experience

A single CLI and language server are part of the product, not optional ecosystem projects.

```text
diamond new
diamond check
diamond fmt
diamond test
diamond run
diamond build
diamond audit
diamond explain <diagnostic>
diamond deploy check
```

Compiler errors should explain the invariant and offer the shortest secure remedy.

```text
error D204: protected operation has no policy
  posts.delete(id) requires Posts.delete authorization.

  Diamond will not execute this operation without a policy.
  Define `policy Posts.delete`, or use an explicitly audited
  administrative capability if this is an administrative task.
```

Other diagnostic priorities:

- Unsafe text-to-HTML conversion: show a safe text-node example.
- Missing application capability: show the inferred effect and the boundary that needs approval.
- Browser bundle containing server-only data: show the shortest offending reference chain.
- Incompatible migration: show which deployed versions and schema states conflict.
- Package update requesting new authority: produce a readable permission diff.

The language server SHOULD display inferred effects, policy coverage, and unsafe call sites on demand without cluttering everyday editing.

---

## 22. Formal specification and real testing

**Decision:** Formalize the security core; prove key invariants where feasible; test the production implementation adversarially.

### 22.1 Formal core

Define a small typed operational semantics covering:

- Type preservation and progress (including explicit expected failure).
- Capability creation, confinement, and non-forgeability at safe-language level.
- Effect-budget preservation across module calls, effect-polymorphic callbacks, and dynamic dispatch.
- Affine resource movement, closure/container scope propagation, attenuation, and scope exit.
- Task ownership, permitted sharing, synchronization, and freedom from data races under the defined memory model.
- Portable policy evaluation and authorized-relation semantics, including mutation pre/post-state checks and explicit disclosure boundaries.
- Transaction state transitions that distinguish confirmed commit, confirmed abort, and unknown outcome without assuming reliable acknowledgments.
- Separation of text from executable contexts.
- Non-serialization of restricted types in safe compilation paths.

Proofs about the formal core do not automatically establish correctness of code generation, operating systems, browsers, drivers, or deployment systems. The implementation must be tested against the claimed semantics.

### 22.2 Adversarial test program

The release pipeline MUST include:

- Property-based tests for parsing, type-checking, query construction, and policy logic.
- Coverage-guided fuzzing of all untrusted-data parsers and cross-context encoders.
- Mutation testing to demonstrate that security test suites catch introduced failures.
- Differential tests between reference evaluator and native/browser targets.
- Real browser XSS suites, including HTML parser oddities and hydration behavior.
- Database injection, TOCTOU authorization, tenant-isolation, aggregate-leakage, outer-join, pagination, update pre/post-state, and constraint-disclosure tests; differential policy tests across the reference evaluator and each supported adapter.
- SSRF tests against redirects, proxies, dual-stack resolution, and DNS rebinding.
- CSRF tests across cookies, origins, redirects, and method overrides.
- Real-browser WebSocket tests for hostile and same-site cross-origin pages, missing/`null`/malformed origins, exact origin matching, cookie-bearing handshakes, and explicit non-browser authentication; message authorization, outbound-event revocation, and session-expiration tests.
- Malicious-dependency tests for package/build/runtime capability isolation.
- Negative programs and runtime attempts that launder authority through closures, callback registries, containers, interface objects, expired scopes, or widened delegation; effect-contract conformance for dynamic dispatch.
- Concurrency tests for mutable alias transfer, synchronized sharing, child-task cancellation/join, and lock scopes that suspend or leak mutable views. Rejected unsafe sharing programs and legitimate parallel use cases are both required.
- Transaction fault injection before commit, during commit, after database commit but before acknowledgment, and during rollback/cleanup. Verify actual database state independently, distinguish confirmed and unknown outcomes, discard unusable connections, and detect unintended replay or duplicate external effects.
- Fault-injection tests for GC pressure, cancellation, partial migrations, and network failures.

Testing must target externally observable outcomes, not merely mock implementations that behave according to the expected result.

### 22.3 Standards and implementation references

These references inform the contracts above; adopting a pattern does not establish Diamond implementation correctness:

- [PostgreSQL connection status and unknown transaction state](https://www.postgresql.org/docs/current/libpq-status.html#LIBPQ-PQTRANSACTIONSTATUS): a failed connection can leave the client without a known transaction state.
- [PostgreSQL row security policies](https://www.postgresql.org/docs/current/ddl-rowsecurity.html) and [security-barrier evaluation rules](https://www.postgresql.org/docs/current/rules-privileges.html): policy composition, expression ordering, and residual disclosure require explicit treatment.
- [RFC 6455, Section 10.2: Origin Considerations](https://www.rfc-editor.org/rfc/rfc6455.html#section-10.2): browser-origin validation is distinct from client authentication.

---

## 23. Security guarantee matrix

These are **design obligations inside Diamond's documented TCB**, not claims that a v0.3 implementation already exists.

| Risk / developer omission | Automatic prevention or enforcement | Remaining boundary |
| --- | --- | --- |
| Missing HTML escaping | Typed text nodes and contextual attribute types | Browser/runtime bugs; privileged raw HTML |
| SQL injection through interpolated values | Typed query AST and structural binding | Raw SQL escape hatch; DB adapter defects |
| Missing input-shape validation | Type-derived request parsers | Correct choice of application validators |
| Forgotten route authentication | Authenticated routes by default; explicit `public` | Misconfigured identity provider or intentionally public routes |
| Forgotten authorization check | Default-deny policies enforced at protected operations | Correctness of the policy and external data sources |
| Missing CSRF protection | Automatic checks for cookie-authenticated mutations | Nonstandard external clients and audited exception routes |
| Cross-origin browser WebSocket use | Automatic exact-origin handshake checks, connection scopes, message policies, and bounded revalidation | Correct allowed-origin configuration; explicitly authenticated non-browser profiles |
| Unsafe outbound URL / SSRF | Destination-scoped capabilities and runtime egress enforcement | OS, resolver, proxy, and sandbox implementation |
| Filesystem traversal | Capability-relative paths and race-resistant opens | Filesystem and OS behavior |
| Secret serialization or logging | Opaque secret types and restricted sinks | Explicit reveal, FFI, runtime compromise |
| Leaked transaction / connection | Scoped affine cleanup; rollback attempts and pooling only after confirmed reusable state | Runtime/power failures; indeterminate outcomes remain explicit |
| False rollback or unsafe transaction replay | Distinct committed/aborted/unknown outcomes; checked replay contracts | Lost acknowledgments and external effects cannot be universally reversed or resolved |
| Accidental side effects in GET | Method/effect compatibility | Incorrect effect classification or unsafe code |
| Dependency overprivilege | Package effect manifests and reviewed authority budgets | Malicious behavior within explicitly granted authority |
| Breaking database deployment | Compatibility-checked migration phases | Data-dependent operations and vendor limitations |
| UAF/data races in safe code | Memory-safe runtime, task-owned mutation, checked graph transfer, synchronization, and scoped authority | Compiler/runtime/native interop bugs; business ordering and deadlocks remain separate |
| Escaped or implicitly widened authority | Scope/effect preservation through callbacks, containers, dispatch, and attenuated delegation | Explicitly granted authority; unsafe/native boundaries and sandbox enforcement |

Diamond is accountable for testing and documenting each advertised guarantee. An implementation must not market an invariant as assured until its enforcement is present and covered by regression tests.

---

## 24. Reference application: a message board

This example demonstrates the division of responsibility: handlers declare business intent, while generated boundaries enforce security policy.

```diamond
app MessageBoard:
  database Postgres<BoardSchema>
  session SecureCookie
  routes PostRoutes
  provides DbRead<Posts>, DbWrite<Posts>

type PostId = Id<"post">

type Post:
  id: PostId
  author: UserId @created_by_principal
  title: Text
  body: Text
  published: Bool = false

type NewPost:
  title: Text<length: 1..120>
  body: Text<length: 1..10_000>

policy Posts:
  read(post, by principal):
    post.published or principal.is_user(post.author)

  create(input, by principal):
    principal.authenticated

  delete(post, by principal):
    principal.is_user(post.author) or principal.has_role(.admin)

public route GET "/posts/{id:PostId}" as PostDetails:
  let post = posts.get(id)?

  html:
    <article>
      <h1>{post.title}</h1>
      <p>{post.body}</p>
    </article>

route POST "/posts":
  let form = request.form<NewPost>()?
  let saved = posts.create(form)?
  redirect route_url(PostDetails, saved.id)

route DELETE "/posts/{id:PostId}":
  posts.delete(id)?
  redirect "/posts"
```

In this sketch, `@created_by_principal` declares that a post's author is a server-derived attribute. The generated repository determines it from the authenticated request context; it must not accept a client-supplied `author` field. Creation applies the declared `published = false` default before the create policy examines the complete proposed record. The public GET still uses the `read` policy. The mutation routes are private by default and have automatic browser CSRF enforcement. Rendering cannot reinterpret text as code. All data operations are parameterized and policy-aware. A create whose commit outcome cannot be determined produces the distinct generated indeterminate-outcome response; it does not redirect as if creation succeeded or trigger an automatic replay.

The application author is responsible for the **meaning** of `published`, `create`, and `delete`; Diamond is responsible for applying their declarations reliably.

### 24.1 Compile-time negative examples

The following should fail in safe Diamond:

```diamond
# ERROR: arbitrary Text is not HTML markup
html: <div>{raw_html(untrusted_text)}</div>

# ERROR: Text cannot become SQL syntax
posts.execute("DELETE FROM posts WHERE id = #{id}")

# ERROR: a browser component cannot import a server secret
component PaymentForm:
  let key = Secrets.payment_api_key

# ERROR: a GET route cannot perform a protected write
public route GET "/delete-everything":
  posts.delete_all()

# ERROR: a protected operation without a policy is denied
route DELETE "/accounts/{id:AccountId}":
  accounts.delete(id)   # No Accounts.delete policy exists

# ERROR: a request-bound closure cannot escape into a durable registry
route POST "/deferred-delete/{id:PostId}":
  global_callbacks.store(fn(): posts.delete(id))

# ERROR: application functions outside the portable core are not policies
policy Reports:
  read(report, by principal):
    arbitrary_application_function(report, principal)
```

Unsafe escape hatches require deliberately different syntax and permission grants. The compiler does not suggest silently turning these examples into permissive code.

---

## 25. Delivery roadmap

| Phase | Deliverables | Exit criteria |
| --- | --- | --- |
| **0. RFC and semantics** | Normative grammar, First Law corpus, threat model, task/memory model, scope/effect/callback rules, portable policy and disclosure semantics, transaction outcomes | Every advertised invariant has an enforcement owner, precise failure behavior, and positive/negative conformance cases |
| **1. Core language** | Rust parser, type checker, ADTs, Result/Option, reference evaluator, formatter, CLI | Invalid programs fail deterministically; executable core semantics agree with the conformance corpus |
| **2. Authority, concurrency, and policy core** | Effect inference and budgets, scope propagation, affine resources, checked mutation/sharing, secrets, portable policy evaluator, authorized-relation model, supported isolation profile | Missing authority or policy denies; escape/dispatch/sharing cases are checked; policy composition and transaction-state semantics have executable reference cases |
| **3. Safe web backend** | HTTP routes, typed HTML/forms, secure cookies, CSRF, WebSocket origin/message boundaries, policy-aware SQLite/Postgres adapters, transaction outcome handling | Beginner CRUD needs no manual guards; adapters preserve the policy model; lost-acknowledgment and hostile-origin cases pass real-interface checks |
| **4. Full-stack** | Browser JS compiler, components, typed RPC, source maps, optional WASM compute | Secret/server imports rejected in client build; compatibility verified |
| **5. Production tooling** | Migration verifier, package capability audits, SBOMs, deterministic builds, deployment checks | Rolling migrations and permission diffs gate deployment |
| **6. Hardening and review** | Fuzz/mutation/differential suites, formal core results, TCB inventory, external security audits | Published evidence supports each promised guarantee |

The roadmap is deliberately cumulative. Phases 0–2 establish the semantics and enforcement foundations used by the web backend; web integration must not postpone those obligations until after its APIs are fixed. Earlier phases may not ship as a "secure-by-construction" production language before the required enforcement exists. An early prototype must be labeled as such. Adversarial and differential checks begin with the feature they validate; phase 6 adds broader hardening and external review.

---

## 26. Acceptance criteria and measurable usability

### 26.1 Security conformance

A v1 release MUST demonstrate that:

1. An ordinary CRUD app needs **zero application-authored** HTML escaping, SQL parameterization, CSRF token handling, or transaction cleanup.
2. Omitting an authorization policy for a protected operation results in rejection, not authorization bypass.
3. A route is authenticated unless explicitly declared public, and the runtime enforces that status.
4. Browser session mutations receive automatic CSRF checks; explicit alternate protocol types are verified.
5. Safe code cannot gain undeclared network, filesystem, process, secret, or data-store authority.
6. Protected resources cannot be accessed by silently bypassing declared authorization policies through aggregation, bulk APIs, generated joins, expression evaluation, or pagination. Updates check both pre-state and proposed post-state; unsupported policy/query translations are rejected before deployment.
7. Client bundles cannot include server secrets or server-only capabilities through safe compilation paths.
8. Resource cleanup runs automatically on success, expected failure, unexpected request failure, and cancellation while the runtime can execute. Children are joined before borrowed authority is released; unusable connections are discarded. Cleanup does not imply that an external transaction was rolled back, and process/power failure remains a documented recovery boundary.
9. Published security regression suites include both positive legitimate-use cases and negative attack cases.
10. Release artifacts include a TCB inventory, dependency/permission diff, unsafe-operation manifest, and documented residual risks.
11. A lost commit acknowledgment can produce a typed indeterminate outcome without a false success or rollback report. Real database fault injection verifies the outcome classification, connection handling, and absence of blind transaction or external-effect replay.
12. Safe programs cannot share mutable managed state concurrently without checked synchronization, or transfer a mutable graph while usable aliases remain. Legitimate immutable sharing, ownership transfer, and synchronized sharing execute correctly.
13. Scoped authority cannot escape through closures, callbacks, containers, generics, dynamic dispatch, or serialization. Callback/interface effects remain checked, and delegation cannot widen scope, identity, or privilege.
14. Browser WebSockets automatically reject unapproved origins and enforce message/delivery policies and bounded session revalidation. Explicit non-browser profiles require independently verified credentials and cannot fall back to ambient-cookie authentication.
15. Each adapter publishes its supported policy/query subset and disclosure contract. Conformance covers aggregate/join behavior, constraint disclosures, evaluation failures, and the difference between protected data and documented covert channels.

### 26.2 Ease-of-use conformance

Security is not successful if ordinary developers must fight the language to get useful work done. Diamond's DX program should evaluate novice and experienced web developers on realistic tasks:

- Build a blog, account settings flow, upload endpoint, and small interactive component.
- Measure steps needed, compile-error comprehension, time-to-completion, and resort to unsafe workarounds.
- Verify the shortest documented example already uses the secure APIs.
- Treat recurring requests for disabled safety checks as a design defect to investigate, not merely a user education problem.
- Require secure examples and error suggestions in official documentation and tooling.

No invented benchmark score is presumed in this proposal. Concrete release thresholds should be set from usability studies and adversarial test baselines.

---

## 27. Settled decisions and remaining engineering questions

### 27.1 Architectural decisions retained and refined in v0.3

| Architectural question | Decision |
| --- | --- |
| Memory management | Generational tracing GC; compiler-selected request arenas; affine resources |
| Policy location | First-class typed policies using restricted ordinary Diamond expressions |
| Effect declarations | Local inference; explicit public/module/application authority budgets |
| Managed mutable sharing | Task ownership, checked graph transfer, and scoped synchronization |
| Capability propagation | Scope/affinity/effects preserved through closures, containers, callbacks, and dispatch; delegation only attenuates |
| Policy semantics | Restricted portable core, authorized relations, update pre/post-state checks, unsupported translations rejected |
| Transaction uncertainty | Confirmed commit, confirmed abort, or explicit unknown outcome; no blind replay |
| Frontend strategy | Generated JS first; SSR by default; optional WASM compute |
| CSS strategy | Standard CSS; typed dynamic properties and validated references |
| Database migration | Versioned expand/backfill/contract with deployment compatibility gates |
| Streaming / WebSockets / uploads | Structured concurrency, origin-checked browser handshakes, message policies, runtime-scoped capabilities |
| Formal verification | Small formal security core plus real adversarial testing of all targets |

**Additionally settled:** no ambient authority, private routes by default, automatic CSRF, policy enforcement at the operation, default-deny missing policy, and privileged/audited unsafe escape hatches. These are normative design choices; full formal rules and implementation conformance remain required deliverables under phases 0–3.

### 27.2 Engineering questions intentionally left for prototypes

These are implementation investigations, **not permission to weaken the security contract**:

- Which exact GC algorithm meets latency and throughput targets without exposing ownership complexity to users?
- What IR/backend and runtime architecture offers the best verification-to-performance trade-off?
- Which query plans, security barriers, and locking/isolation strategies preserve the specified portable policies and disclosure semantics across SQLite and Postgres? Unsupported shapes must be rejected, not assigned weaker semantics.
- Which compiler analyses and runtime synchronization primitives enforce task ownership and scope propagation with useful diagnostics and acceptable overhead?
- Which adapter-specific reconciliation or idempotency facilities can resolve an indeterminate transaction outcome? Unsupported resolution must preserve uncertainty rather than claim rollback or exactly-once execution.
- How can policy freshness and revocation requirements be expressed concisely for long-lived connections?
- Which browser APIs require generated JS versus Wasm bridges, and how are source maps audited?
- What minimum sandbox guarantees are available on each target OS, and how do unsupported targets fail closed?
- Which subsets of the core semantics can receive mechanized proofs before v1?

Prototypes must be judged against the First Law. An implementation shortcut that forces developers to remember a safety ritual is not an acceptable resolution.

---

## 28. The Diamond constitution

1. **Safety is the language's job.** Developers do not repeatedly opt into protections that can be applied automatically.
2. **Omission must not grant authority.** Missing security decisions fail closed.
3. **Business intent belongs to developers; enforcement belongs to Diamond.** Define policy once, apply it everywhere.
4. **Text is data, not executable syntax.** Cross-context interpretation is typed and restricted.
5. **Authority is explicit and scoped.** No ambient access to powerful resources.
6. **The common path is pleasant.** Security machinery belongs behind concise, readable code.
7. **Exceptions leave fingerprints.** Unsafe behavior is deliberate, narrow, reviewable, and detectable.
8. **Claims require evidence.** Compiler proof, runtime testing, deployment checks, and published boundaries back every safety guarantee.

### Closing thesis

Ruby demonstrated that programming could feel natural. Rust demonstrated that a language could systematically prevent whole classes of bugs. Diamond's ambition is to combine the experience of the first with the structural discipline of the second, adapted to the actual threat model of the web.

**Diamond should not ask ordinary web developers to become security engineers. It should embody the security engineering they should never have needed to remember.**

**DIAMOND — Beautiful code. Hard guarantees.**
