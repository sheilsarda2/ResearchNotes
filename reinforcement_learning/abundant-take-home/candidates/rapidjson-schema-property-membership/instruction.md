# Correct schema property membership during instance validation

RapidJSON's schema validator shares internal bookkeeping for names from `properties`, `required`, and `dependencies`. It currently treats a name mentioned only in `required` or `dependencies` as though it were declared in `properties`, allowing instances that should fail `additionalProperties` validation.

Fix this behavior in `/workspace/repo`. For example, this schema must reject `{"B":1,"C":1}`:

```json
{"properties":{"B":{}},"required":["B","C"],"additionalProperties":false}
```

The schema itself is allowed to be unsatisfiable; the task is correct validation of instances, not mandatory rejection of the schema during construction.

Required behavior:

- A name mentioned only by `required`, or as a source/target of `dependencies`, must still follow `additionalProperties` unless it is declared in `properties` or matches `patternProperties`.
- Enforce both `additionalProperties:false` and schema-valued `additionalProperties`. Preserve the default/explicit `true` behavior.
- Keep presence bookkeeping working: required names and property/schema dependencies must still detect missing values and activate the appropriate dependent validation. Forbidden additional properties do not become permitted merely because they are required.
- Preserve declared empty schemas, `$ref` properties, matching pattern constraints, nested objects/array items, and exact property names (including Unicode and embedded null characters).
- Apply the same semantics to DOM validation through `SchemaValidator`, SAX validation, continued error collection, and repeated validation after `Reset()`.

Maintain the existing public API and relevant upstream object-schema behavior. Do not add a new validator or reject all schemas that use required/dependency names outside `properties`. Use any sound internal implementation. Non-string dependency-array entries and other invalid-schema handling are outside this task.

The environment includes the pinned upstream source and a C++17 compiler. The library is header-only; small reproductions can be compiled with `g++ -std=c++17 -I/workspace/repo/include example.cpp -o example`.

Only `/workspace/repo/include/rapidjson` is transferred to a fresh verifier. The verifier builds independent tests and protected upstream object-schema regressions against those headers, with networking disabled. Changes to tests, build configuration, or other files are not submitted.
