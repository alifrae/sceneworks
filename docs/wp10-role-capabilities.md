# WP10 — Role personas, capabilities, and domain overlays

## Goal

Make SceneWorks roles behave like credible professional specialists without
hard-coding one product's domain into the company itself.

WP10 separates four things that were previously collapsed into standing prompts:

1. **Persona** — stable professional reasoning style for a role.
2. **Capabilities** — skills and engineering methods the role can apply.
3. **Domain overlays** — project/task-specific technical expertise such as
   automotive sensing, LiDAR, point-cloud processing, or diagnostics.
4. **Project knowledge/evidence** — repository content, accepted Project Memory,
   task contracts, architecture/advisory results, and measured runtime evidence.

The fourth category is authoritative for project-specific facts. Persona and
capabilities guide reasoning; they never manufacture facts.

## Core decision: systems engineering is permanent; SysML/MBSE are optional

Engineer, Chief Architect, Technical Expert, and Reviewer all receive a systems
engineering lens appropriate to their role. This includes:

- black-box behavior before white-box implementation detail;
- explicit inputs/outputs, state, lifecycle, errors, timing, and failure modes;
- system decomposition and responsibility boundaries;
- end-to-end data/control flow;
- interfaces as contracts, including type, unit, ownership, lifetime, ordering,
  compatibility, and clock/coordinate domains where applicable;
- requirements-to-verification traceability;
- root-cause reasoning instead of symptom suppression;
- performance and resource behavior as system properties.

`mbse` and `sysml` are **methods**, not permanent role identities. They should be
activated only for projects/tasks where an explicit system model reduces
ambiguity or improves traceability across requirements, interfaces, behavior,
architecture, and verification. They should not be injected into every bug fix
or software refactor as ceremony.

## Generic Engineer vs PCS Engineer

The generic Engineer is a **senior systems-oriented software engineer**. Its
core capability set includes software engineering, systems engineering,
black-box reasoning, interface design, requirements-to-verification,
root-cause debugging, testing, performance engineering, and API design.

The generic Engineer is deliberately **not** permanently a LiDAR or automotive
engineer. That expertise is attached through project/task profiles. Thus an
Engineer working on PCS can behave as a senior automotive sensing/LiDAR systems
software engineer, while the same SceneWorks role can work on an unrelated web
or infrastructure repository without irrelevant sensor assumptions.

## Resolution order

Capabilities are additive and deterministic:

1. role core capabilities;
2. project-wide profile;
3. project role-specific overlay;
4. task-wide requirements;
5. task role-specific overlay.

Duplicates are removed. SceneWorks does not infer capabilities from task text;
explicit configuration avoids unstable prompt classification and hidden role
changes.

Unknown capability names are permitted. This allows a project to introduce a
new specialization without a SceneWorks release. An unknown name renders as a
custom capability with the same evidence boundary: the label never becomes a
source of project facts.

## Profile schema

Projects and tasks use the same shape:

```json
{
  "skills": ["real-time-data-pipelines"],
  "domains": ["automotive-sensor-systems", "lidar"],
  "methods": [],
  "roles": {
    "technical_expert": {
      "skills": [],
      "domains": ["point-cloud-processing"],
      "methods": []
    }
  }
}
```

Project profile:

- create/update through `capability_profile` on the Project API;
- persists with the project.

Task profile:

- create through `capability_requirements`;
- replace through `PUT /api/tasks/{task_id}/capabilities` while the task is
  still `NEW`;
- becomes fixed once workflow execution starts, for the same reason the task
  engineering contract is fixed before execution.

## Built-in capabilities

### Core software/systems

- `software-engineering`
- `systems-engineering`
- `black-box-thinking`
- `interface-design`
- `requirements-verification`
- `root-cause-debugging`
- `testing`
- `performance-engineering`
- `api-design`
- `software-architecture`
- `independent-verification`
- `domain-analysis`
- `product-requirements`
- `technology-strategy`
- `business-strategy`
- `research`

### Optional engineering methods

- `mbse`
- `sysml`

### Initial automotive/sensor domain overlays

- `automotive-sensor-systems`
- `lidar`
- `radar`
- `point-cloud-processing`
- `sensor-calibration`
- `time-synchronization`
- `automotive-diagnostics-uds`
- `someip`
- `real-time-data-pipelines`

## PCS recommendation

Use project-wide sensor capabilities for expertise that is relevant to a large
fraction of PCS engineering work:

```json
{
  "skills": ["real-time-data-pipelines", "performance-engineering"],
  "domains": [
    "automotive-sensor-systems",
    "lidar",
    "point-cloud-processing",
    "sensor-calibration",
    "time-synchronization"
  ],
  "methods": [],
  "roles": {
    "technical_expert": {
      "skills": [],
      "domains": [],
      "methods": []
    },
    "engineer": {
      "skills": [],
      "domains": [],
      "methods": []
    },
    "reviewer": {
      "skills": [],
      "domains": [],
      "methods": []
    }
  }
}
```

Add narrow expertise such as `automotive-diagnostics-uds` or `someip` at task
level when the task actually concerns those areas. This keeps prompts focused
and prevents every PCS task from carrying irrelevant protocol instructions.

Enable `mbse` / `sysml` selectively for work such as:

- major sensor/provider abstraction changes;
- multi-sensor architecture and fusion interfaces;
- hardware/software interface redesign;
- synchronization/clock-domain architecture;
- system requirements and verification decomposition;
- complex live-data/diagnostic interaction across components.

They are generally unnecessary for a local GUI bug, documentation correction,
or small isolated algorithm implementation.

## Advisory evidence preservation

Before WP10, Product/CTO/Technical Expert task outputs were appended into
`Task.architecture_result`, and the Architect later replaced that field with its
own result. Engineer and Reviewer therefore depended on the Architect to carry
forward every original domain constraint accurately.

WP10 persists advisory outputs independently in `Task.advisory_results`.
Engineer and Reviewer receive the original applicable advisory evidence in
addition to the approved architecture result. The Architect may interpret a
Technical Expert finding, but no longer becomes the only surviving copy of it.

## Invariants

- Role/back-end/model separation from WP8 is unchanged.
- Project-specific facts never originate from a persona/capability label.
- Domain overlays do not expand tool permissions.
- Capability selection does not bypass the engineering contract.
- Engineer remains the only implementation role; Technical Expert remains
  read-only.
- Reviewer receives original specialist evidence independently of the Engineer.
- Existing projects/tasks migrate to empty profiles; no historical expertise or
  advisory evidence is fabricated.
