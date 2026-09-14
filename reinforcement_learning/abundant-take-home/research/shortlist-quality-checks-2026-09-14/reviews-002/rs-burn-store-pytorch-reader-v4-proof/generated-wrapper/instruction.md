You are reviewing a Harbor task for quality and completeness. Judge whether the task's artifacts meet the criteria below, and provide a short rationale for each.

The task to review is at /app/task. Here is the complete file tree:

<file_tree>
STATUS.md
environment/Dockerfile
environment/upstream.tar.gz
instruction.md
provenance.json
solution/changes.patch
solution/solve.sh
task.toml
tests/Dockerfile
tests/fixtures/gen_fixtures.py
tests/harness/fixture_matrix.rs
tests/hidden/mod.rs
tests/hidden/reader/create_legacy_with_offsets.py
tests/hidden/reader/create_protocol4.py
tests/hidden/reader/create_tar_format.py
tests/hidden/reader/mod.rs
tests/hidden/reader/simple_legacy.py
tests/hidden/reader/test_data/bfloat16.pt
tests/hidden/reader/test_data/big_endian.pt
tests/hidden/reader/test_data/bool.pt
tests/hidden/reader/test_data/broken.pt
tests/hidden/reader/test_data/buffers.pt
tests/hidden/reader/test_data/checkpoint.pt
tests/hidden/reader/test_data/complex_structure.pt
tests/hidden/reader/test_data/empty.pt
tests/hidden/reader/test_data/extreme_values.pt
tests/hidden/reader/test_data/float16.pt
tests/hidden/reader/test_data/float32.pt
tests/hidden/reader/test_data/float64.pt
tests/hidden/reader/test_data/int16.pt
tests/hidden/reader/test_data/int32.pt
tests/hidden/reader/test_data/int64.pt
tests/hidden/reader/test_data/int8.pt
tests/hidden/reader/test_data/large_shape.pt
tests/hidden/reader/test_data/legacy_shared_storage.pt
tests/hidden/reader/test_data/legacy_uncloned_views.pt
tests/hidden/reader/test_data/legacy_with_offsets.pt
tests/hidden/reader/test_data/mixed_types.pt
tests/hidden/reader/test_data/nested_dict.pt
tests/hidden/reader/test_data/non_contiguous.pt
tests/hidden/reader/test_data/parameter.pt
tests/hidden/reader/test_data/protocol4.pt
tests/hidden/reader/test_data/scalar.pt
tests/hidden/reader/test_data/simple_legacy.pt
tests/hidden/reader/test_data/special_values.pt
tests/hidden/reader/test_data/state_dict.pt
tests/hidden/reader/test_data/tar_2d_tensor.tar
tests/hidden/reader/test_data/tar_float32.tar
tests/hidden/reader/test_data/tar_float64.tar
tests/hidden/reader/test_data/tar_int64.tar
tests/hidden/reader/test_data/tar_multi_dtype.tar
tests/hidden/reader/test_data/tar_storage_view.tar
tests/hidden/reader/test_data/tar_weight_bias.tar
tests/hidden/reader/test_data/tensor_2d.pt
tests/hidden/reader/test_data/tensor_3d.pt
tests/hidden/reader/test_data/tensor_4d.pt
tests/hidden/reader/test_data/uint8.pt
tests/hidden/reader/test_data.py
tests/hidden/store/mod.rs
tests/hidden/store/test_data/generate_enum_test.py
tests/hidden/store/test_data/model_without_enum_variants.pt
tests/image-source/upstream.tar.gz
tests/score.py
tests/test.sh
</file_tree>

Read ALL files under /app/task (e.g., "/app/task/instruction.md", "/app/task/tests/test_state.py"). You must examine every file - including data files, configuration files, and any supporting scripts — not just the main files. This is critical for accurate evaluation.

Evaluate each criterion one at a time. For each criterion, think about and list reasons why this task may or may not meet it before making your final judgment. When a criterion fails, explain why it fails based on the criteria description. Do not suggest what the author should do to fix or improve the task.

Do not modify any files under /app/task.

Guidance:
- behavior_in_task_description: All behavior that tests verify should be clearly described in instruction.md. PASS if the instruction explicitly covers what tests check (including filenames, schema, exact commands or interfaces when relevant). FAIL if tests require details not specified or only implied by examples.
- behavior_in_tests: All behavior described in instruction.md should be tested. PASS if tests cover the specified behavior and outputs. FAIL if important required behavior is not tested.
- informative_test_structure: Tests should be readable and clearly organized (sections/comments naming what is being checked). PASS if the structure is clear and maintainable; FAIL otherwise.
- anti_cheating_measures: The agent does not see the solution or tests at run time. Assess whether the task encourages honest work (e.g., avoids depending on mutable external resources that allow shortcuts, avoids giving away answers in environment files, prevents trivial string matching on data that would not exist for the agent, etc.). PASS if the setup discourages trivial shortcuts; FAIL if there are obvious ways to bypass intended behavior (e.g., answer embedded in environment, copying ground-truth files into image, or relying on network calls that leak answers).
- structured_data_schema: If the task expects structured output (APIs, JSON, CSV, DB schemas), the exact schema must be documented in instruction.md or a clearly referenced spec file. PASS if schema is explicit; FAIL if only examples are given without marking them as normative.
- pinned_dependencies: If external dependencies are used, versions should be pinned (Python/pip packages pinned; apt packages generally need not be pinned). PASS if pinning is adequate; FAIL otherwise.
- typos: Look closely for typos in filenames, paths, commands, and variable names that could cause failures or confusion. PASS if none found; FAIL if present.
- tests_or_solution_in_image: The image should not copy tests/ or solution/ into the runtime build. PASS if they are not included; FAIL if they are.
- test_deps_in_image: Test-only dependencies should be installed in tests (e.g., test.sh), not baked into the image build. PASS if test-only deps are isolated to tests; FAIL otherwise.
- hardcoded_solution: The solution should not just echo/cat the final answer; it should demonstrate the steps an agent would perform. PASS if it derives the answer via a process; FAIL if it prints the final answer without computation.
- file_reference_mentioned: If the agent must produce files (e.g., output.csv) for tests to check, those filenames should be explicitly stated in instruction.md. PASS if mentioned; FAIL otherwise.

# Output

When you have evaluated every criterion, write your result to a file named check-result.json in your working directory. It must be a single JSON object with one key per criterion above; each value must be an object with:
- "outcome": one of "pass", "fail", or "not_applicable"
- "explanation": a short rationale

The file must validate against this JSON schema:

<output_schema>
{
  "$defs": {
    "CheckOutcome": {
      "enum": [
        "pass",
        "fail",
        "not_applicable"
      ],
      "title": "CheckOutcome",
      "type": "string"
    },
    "QualityCheckModel": {
      "properties": {
        "explanation": {
          "title": "Explanation",
          "type": "string"
        },
        "outcome": {
          "$ref": "#/$defs/CheckOutcome"
        }
      },
      "required": [
        "explanation",
        "outcome"
      ],
      "title": "QualityCheckModel",
      "type": "object"
    }
  },
  "properties": {
    "behavior_in_task_description": {
      "$ref": "#/$defs/QualityCheckModel"
    },
    "behavior_in_tests": {
      "$ref": "#/$defs/QualityCheckModel"
    },
    "informative_test_structure": {
      "$ref": "#/$defs/QualityCheckModel"
    },
    "anti_cheating_measures": {
      "$ref": "#/$defs/QualityCheckModel"
    },
    "structured_data_schema": {
      "$ref": "#/$defs/QualityCheckModel"
    },
    "pinned_dependencies": {
      "$ref": "#/$defs/QualityCheckModel"
    },
    "typos": {
      "$ref": "#/$defs/QualityCheckModel"
    },
    "tests_or_solution_in_image": {
      "$ref": "#/$defs/QualityCheckModel"
    },
    "test_deps_in_image": {
      "$ref": "#/$defs/QualityCheckModel"
    },
    "hardcoded_solution": {
      "$ref": "#/$defs/QualityCheckModel"
    },
    "file_reference_mentioned": {
      "$ref": "#/$defs/QualityCheckModel"
    }
  },
  "required": [
    "behavior_in_task_description",
    "behavior_in_tests",
    "informative_test_structure",
    "anti_cheating_measures",
    "structured_data_schema",
    "pinned_dependencies",
    "typos",
    "tests_or_solution_in_image",
    "test_deps_in_image",
    "hardcoded_solution",
    "file_reference_mentioned"
  ],
  "title": "QualityCheckResponse",
  "type": "object"
}
</output_schema>

check-result.json in your working directory is your only deliverable.
