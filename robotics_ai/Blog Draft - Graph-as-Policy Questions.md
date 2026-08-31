# [Working title: Can Agents Engineer Reliable Robot Policies?]

*Draft status: collecting questions and evidence.*

Source paper: [GaP: A Graph-as-Policy Multi-Agent Self-Learning Harness for Variational Automation Tasks](2607.05369v1.pdf), Chen et al., 2026.

## Starting point

Chen et al. define *Variational Automation* as repeated robot work inside a known workcell, with bounded variation in object geometry and pose. Their examples include packing groceries, making popcorn, inserting USB-C cables, and washing crates. This sits between fixed automation, where engineers can prescribe the motion, and generalist robotics, where a model must handle unfamiliar tasks and environments.

GaP represents the robot policy as a typed computation graph. A set of coding agents decomposes the task, selects perception, grasping, planning, and control skills from a 51-skill library, and connects them into an executable graph. The system rehearses that graph across sampled simulator scenes, records failures at graph checkpoints, and asks agents to revise the graph or its parameters. The resulting policy runs through a graph interpreter without an LLM in the execution loop.

The paper reports strong results across eight simulation and physical benchmarks. Those numbers also raise questions about what GaP learned, which assumptions carried the result, how fair the baselines were, and how much engineering remains outside the graph-generation loop.

## Questions

<!--
For each question Sheil asks, add a numbered subsection using this form:

### Q1. [Question in Sheil's words]

**Why I am asking:** [Context or intuition from the conversation.]

**Working answer:** [Short answer after research.]

**Evidence:**

- [Claim, experiment, citation, or calculation.]

**Counterargument or uncertainty:** [Strongest caveat.]

**Implication:** [How the answer changes the larger thesis.]

Keep agent assignments and raw research notes under Research record. Rewrite this
section as continuous prose during synthesis.
-->

### Q1. What hardware setup does GaP use in simulation and for real rollouts?

**Why I am asking:** The paper reports results across four simulated and four physical benchmarks, but its main hardware descriptions mix robot embodiments, cameras, simulators, task props, and execution infrastructure. I want a model-level inventory and a clear record of anything the authors leave unspecified.

**Working answer:** The paper does not contain a reproducible hardware bill of materials. Its only exact product/version disclosures are **NVIDIA Isaac Sim 5.1.0** and a **Stereolabs ZED Mini**; it names robot families, but not most model numbers. The later public release narrows the arms to Franka Panda and UR5e, although it cannot prove that those were the exact paper rigs.

| Benchmark | Paper-level setup | What remains unspecified |
|---|---|---|
| I-a, Fulfill Grocery Orders (sim) | One simulated Franka in a LIBERO-derived kitchen; basket, target grocery item, and distractors; item positions varied within a 20 × 20 cm region. Isaac/Isaac Lab is the reported rehearsal stack, and reference [3] pins Isaac Sim 5.1.0. | Franka version, gripper, simulated camera model and image specification, Isaac Lab version, workstation/GPU, and asset IDs. |
| I-b, Fulfill Grocery Orders (real) | Franka arm, wrist camera, grocery-store items, and baskets. | Arm, gripper, camera, basket, and grocery SKU model numbers; camera settings; controller and compute host. |
| II-a/b, Pack Grocery Items (sim/real) | Benchmark I modified to pack six objects into a container in six grasp attempts. The same single-Franka workcell is strongly implied. | The paper does not enumerate the six objects, basket/bin model, arm, gripper, camera, or physical reset procedure. |
| III-a, Make Popcorn (sim) | One Franka and Franka gripper with LIBERO frypan, stove, and knob assets. | Exact arm/gripper and asset versions. |
| III-b, Make Popcorn (real) | Franka, wrist camera, an Amazon portable stove, and Jiffy Pop. | Arm, gripper, camera, stove brand/model/ASIN, and Jiffy Pop variant. |
| IV, Insert Cables (real) | Universal Robots **UR5**, wrist-mounted **ZED Mini**, USB-C cable, port bank, and internal force/torque feedback. | The paper says UR5, not UR5e. It gives no controller generation, end-effector, cable/socket SKU, separate F/T sensor, or compute host. Figure 3 shows a custom cable holder but does not identify it. |
| V, Wash Crates (sim) | Two Franka arms side-grasp, lift, flip, and place a crate. | Franka/gripper versions, mounting geometry, camera system, crate dimensions, and compute hardware. |

The [released GaP repository](https://github.com/graph-robots/graph-as-policy) adds useful but later evidence:

- Its real example is labeled **Franka Panda + Robotiq + ZED**. The pinned controller configuration uses a ZED serial number `35062621`, HD720 at 15 fps with depth, and an overhead camera transform—not the paper's stated wrist-camera configuration. The Robotiq driver supports 2F-85, 2F-140, and Hand-E, so the config still does not identify the gripper model. ([Franka client config](https://github.com/graph-robots/controllers/blob/b91fa9db001f68483025ba2f78d7d8853348d07f/configs/franka/franka_robotiq_client.yaml), [robot config](https://github.com/graph-robots/controllers/blob/b91fa9db001f68483025ba2f78d7d8853348d07f/robot_configs/franka/franka_robotiq_gripper.yaml), [Robotiq driver](https://github.com/graph-robots/controllers/blob/b91fa9db001f68483025ba2f78d7d8853348d07f/robots_realtime/robots/robotiq_gripper.py#L48-L66))
- Its cable example calls the robot a **UR5e**, resolving the released stack more precisely than the paper. A UR5e has a 5 kg payload, 850 mm reach, ±0.03 mm repeatability, and an integrated tool-flange F/T sensor according to the [manufacturer datasheet](https://www.universal-robots.com/manuals/EN/TechSheets/UR5e_techsheet_pdf_online/UR5e_techsheet_en.pdf). This remains release evidence, not proof that the manuscript's “UR5” was a UR5e.
- The ZED Mini's manufacturer SKU is **ZED-121210**. It is a 63 mm-baseline stereo camera with up to 2208 × 1242 at 15 fps per sensor, 1080p at 30 fps, or 720p at 60 fps. The paper does not report which mode it used. ([Stereolabs product page](https://store.stereolabs.com/products/zed-mini), [datasheet](https://support.stereolabs.com/hc/article_attachments/27901442262551))
- The release asks for one RTX 4090 with at least 24 GB VRAM, while its quickstart timings mention an A100. Neither is the undisclosed paper-experiment GPU. ([release quickstart](https://github.com/graph-robots/graph-as-policy#quickstart))

There is also a simulator-version wrinkle. Section 4.2 says Isaac, the contribution statement says Isaac Lab, and reference [3] says Isaac Sim 5.1.0; Appendix D describes `sim_bridge.StepOnce` as a MuJoCo timestep. The later public beta uses MuJoCo 3.6.0 and says Isaac rehearsal was omitted from that release. The safest conclusion is that Isaac Sim 5.1.0 was the paper's claimed rehearsal backend, but the paper does not give enough task-to-backend or configuration detail to reproduce it.

**Evidence:** [paper §§4.2 and 5.1](https://arxiv.org/html/2607.05369v1#S5.SS1), [Isaac Sim citation](https://arxiv.org/html/2607.05369v1#bib.bib3), [released packing configuration](https://github.com/ehehee/Variational-Automation-Benchmark/blob/fd2bc0f63369ba39137df018bbca8f6b372ffa0b/tasks/libero_object_packing/pack_all_objects_v00.yaml), and [release roadmap](https://github.com/graph-robots/graph-as-policy/blob/main/docs/source/developers/roadmap.md).

**Counterargument or uncertainty:** The public code is newer than the paper and differs in camera placement and simulator backend. It can clarify the released implementation, but it should not be retroactively presented as the experimental BOM.

**Implication:** The performance results are system-level evidence, not a hardware-reproducibility package. Anyone reproducing the work must choose and document several material components that the paper leaves open.

#### Hardware sidebar: mounting a ZED Mini on a Franka wrist

GaP specifies a wrist camera for its physical Franka tasks but provides no mount design. The public controller configuration uses an overhead ZED, so it does not fill that mechanical gap. An independent design from the DROID project provides the strongest starting point for a Franka Panda or FR3 fitted with a Robotiq 2F-85.

DROID standardized a wrist-mounted ZED Mini across its multi-lab data-collection platform. The project publishes two printable files, `hand_camera_part_1.stl` and `hand_camera_part_2.stl`, plus a photographed [assembly procedure](https://droid-dataset.github.io/droid/hardware-setup/assembly.html#mounting-hand-camera-on-robot). The parts measure about 49 × 45 × 34 mm and 71 × 101 × 34 mm. The project's [shopping list](https://droid-dataset.github.io/droid/hardware-setup/shopping-list.html) estimates two dollars of print material and links the [STL folder](https://drive.google.com/drive/folders/1k56XVdlfrXCX4iOlFlTlkoTh-2Px6CyD).

The mount-level bill of materials is:

| Component | Quantity | Specification and caveat |
|---|---:|---|
| Stereolabs ZED Mini | 1 | SKU ZED-121210; 60 g; four M2 × 0.4 mounting holes with 2.3 mm maximum screw insertion |
| DROID printed mount | 1 set | Two STL parts; use the source files as separate prints |
| Camera-clamp screw and nut | 1 each | DROID specifies a 10 mm screw but omits thread diameter, head style, and grade |
| Longer wrist/coupling screws | 2 | DROID specifies 30 mm length but omits thread diameter, grade, and torque |
| ZED USB 3 cable | 1 | Use the supplied 4 m cable or the long cable supplied with the camera revision |
| Cable restraints | Several | Hook-and-loop straps plus small zip ties for strain relief and controlled joint slack |
| Robotiq 2F-85 and Franka coupling | 1 | The DROID geometry assumes this gripper arrangement |

The missing fastener details prevent direct procurement from the DROID guide. Franka's flange uses M6 threaded holes with limited engagement depth. The [Franka Hand manual](https://download.franka.de/documents/Product%20Manual%20Franka%20Hand_R50010_1.1_EN.pdf) specifies M6 × 12 DIN 7984 screws, 5 Nm torque, and 8 mm engagement for the Franka Hand. DROID replaces two coupling screws with longer fasteners that pass through the camera bracket. A reproducer should measure the bracket and coupling stack, retain the permitted flange engagement, and select the screw grade and torque from the applicable Franka and gripper-coupling documentation.

MIT CLEAR Lab's [Cortado robot description](https://github.com/MIT-CLEAR-Lab/cortado_description) models a related FR3, Robotiq 2F-85, and ZED Mini setup under an MIT license. It includes visual and collision meshes, an editable Onshape assembly, and a URDF camera transform. The URDF assigns 45 g to the mount and pitches the camera by 1.22 radians, about 70 degrees. It also rotates the Robotiq by 180 degrees to reduce cable coupling. These values provide a simulation seed; hand-eye calibration must determine the transform on the assembled robot. Cortado's combined mesh supports visualization, while DROID's two-part source files support printing.

Franka publishes an [official generic printable wrist mount](https://franka.de/3d-assets) with STEP/STL files, at least 50% infill, and an M6 × 28 grade-8.8 mounting screw. That design expects a camera with a ¼-20 socket. The ZED Mini instead uses four M2 × 0.4 mounting holes, so the official generic mount requires a ZED-specific adapter plate. Franka's ZED Mini head bracket belongs to the FR3 Duo reference-camera assembly and does not provide an eye-in-hand view.

A practical DROID print should use PETG or a stiffer engineering filament, 0.2 mm layers, four or five perimeters, and 40–50% infill. The installer should repair and inspect the meshes before slicing, add strain relief near the USB-C connector, and move each robot joint through its full intended range at low speed while watching cable tension. The ZED Mini and mount add about 100–150 g before cable forces. The robot configuration and motion planner need the assembled payload, inertia, camera geometry, and collision shape.

DROID's guide recommends the long original ZED cable, direct USB connection, and slack checks at joint extremes. It also rotates the 2F-85 for a clearer wrist view and covers the gripper status light with soft hook-and-loop material to keep red glare out of the camera. Those details affect image quality and long-run cable reliability as much as the printed bracket does. ([ZED Mini product page](https://store.stereolabs.com/products/zed-mini), [mechanical drawing](https://support.stereolabs.com/hc/article_attachments/27901442262551), [Cortado camera URDF](https://raw.githubusercontent.com/MIT-CLEAR-Lab/cortado_description/main/robots/common/fr3_robotiq_2f_85.xacro))

### Q2. Does the GaP implementation use ROS 2 under the hood to orchestrate?

**Why I am asking:** The paper cites ROS as an architectural influence, lists a ROS translation skill, and uses ROS nodes in the cable-insertion benchmark. Those facts do not establish which software executes and schedules a GaP graph.

**Working answer:** No. GaP does not use ROS 2 as its general agent orchestrator or graph scheduler. The paper-era implementation has a custom typed graph and external interpreter, with gRPC/protobuf skill calls. ROS appears as an adapter for robot capabilities—most clearly in cable insertion, where GaP invokes four existing ROS nodes and then decides which graph edge to take.

The layers are easy to conflate:

1. LLM agents author and validate a GaP computation graph.
2. GaP's custom interpreter schedules nodes and follows typed data and control edges.
3. MORSL skills are exposed primarily as gRPC methods or local scripts in the paper-era interface.
4. For cable insertion, an intermediate interface creates a temporary ROS “orchestrator” node, invokes `align`, `touch`, `insert`, or `extract`, waits for a result, and reports it back to the GaP graph.

Figure 1 shows a ROS 2 logo and the bibliography cites the ROS 2 architecture paper, but the cable description says only “ROS” and gives no distribution or DDS implementation. The released cable companion stack is explicitly ROS 1. The defensible statement is therefore “GaP can orchestrate ROS skills,” not “GaP itself runs on ROS 2.”

The public beta has since moved further away from that interpretation: its design says gRPC/protobuf were removed, and its custom `WorkflowExecutor` now runs plain-Python tools in process. ([paper Appendix B](https://arxiv.org/html/2607.05369v1#A2), [paper Appendix F](https://arxiv.org/html/2607.05369v1#A6), [current design](https://github.com/graph-robots/graph-as-policy/blob/main/docs/design.md#1-overview--principles), [cable example](https://github.com/graph-robots/graph-as-policy/blob/main/examples/cable_ur/README.md#the-full-cable-project))

**Implication:** A GaP policy is a workflow above the robot middleware. ROS can implement skills and device communication without owning policy topology, agent coordination, validation, or rehearsal.

### Q3. What feedback did CaP-X's "visual differencing" provide before and after execution, and did it supplement feedback that cuRobo could provide about IK?

**Why I am asking:** GaP contrasts CaP-X's VLM feedback with geometric and numerical checks. I want to know what CaP-X showed the VLM, what the VLM returned, and whether cuRobo could cover the same failure information through IK or motion planning.

**Working answer:** CaP-X's Visual Differencing Model (VDM) provides semantic, image-based feedback to the coding agent. Before the first action, it receives the task and initial image and describes task-relevant scene attributes. After execution, it receives the task plus previous and current images—main view and optionally wrist view—and writes free-form text describing what changed and whether the task appears complete. That text is appended alongside the executed code and stdout/stderr before the coding agent chooses `FINISH` or generates a revision.

This is supplementary to kinematic or motion-planning feedback, not a replacement for it. The VDM can report “the carton moved but did not enter the basket” or “the task looks complete.” It cannot reliably establish that a requested pose has a joint solution, identify a collision waypoint, or quantify a grasp approach corridor. A motion planner can provide those geometric diagnostics, but it cannot decide from pixels whether the semantically intended task outcome occurred.

There is one terminology correction: CaP-X's documented control primitives use PyRoKI-style IK/planning rather than cuRobo specifically. cuRobo is still the right comparison class. Its native results include IK success and pose error, while `MotionGen` distinguishes failures such as IK failure, graph-search failure, trajectory-optimization failure, invalid joint limits, and start-state collision. GaP wraps similar information into skill-level failure reasons and trajectory validators. ([CaP-X method](https://arxiv.org/html/2603.22435v1#S3.SS2), [VDM implementation](https://github.com/capgym/cap-x/blob/main/capx/envs/trial.py#L332-L386), [cuRobo IK results](https://curobo.org/_api/curobo.wrap.reacher.ik_solver.html), [cuRobo MotionGen results](https://curobo.org/_api/curobo.wrap.reacher.motion_gen.html))

**Counterargument or uncertainty:** The GaP paper describes VLM feedback as prone to hallucination, but that is a limitation claim, not proof that every VLM judgment is wrong. Conversely, planner status is only as truthful as the robot model, collision world, calibration, and query supplied to it.

**Implication:** Robust feedback needs both levels: semantic observation to determine what happened and model-based geometry/physics to diagnose why a planned manipulation did or did not work.

#### Q3.2. Why use a VLM instead of feedback from a physics simulator such as Isaac Lab-Arena?

**Why I am asking:** A simulator can expose poses, contacts, collisions, and task rewards without asking a vision model to infer them from pixels. I want to understand why CaP-X used visual feedback, which problems simulation would solve, and which new assumptions a simulator would introduce.

**Working answer:** CaP-X used a VLM because its experiment asks whether a coding agent can improve from the same visual observations available in simulation and on a real robot. The authors found that placing raw RGB directly into the coding context performed worse than stdout/stderr alone; they hypothesize a cross-modal alignment problem between code reasoning and execution images. The VDM converts those images into task-relevant text, which improved results.

This was not because CaP-X lacked a simulator. CaP-Gym has privileged simulator-state conditions, and CaP-RL uses privileged training. The VDM is the non-privileged, deployment-portable feedback channel under study. The paper does not say that digital-twin cost drove the choice.

A physics simulator such as Isaac Lab-Arena would answer different questions better. With a sufficiently accurate scene it can expose object poses, contacts, collision pairs, forces, joint state, and exact task predicates across vectorized variations. That requires a robot articulation, collision geometry, materials, masses, friction, camera calibration, initial-state distribution, and success checks that match the real workcell. A VLM needs images and a prompt and transfers more directly to physical rollout, but returns an uncertain semantic judgment rather than privileged physical truth. ([CaP-X visual-feedback ablation](https://arxiv.org/html/2603.22435v1#S3.SS3), [Isaac Sim architecture](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/introduction/reference_architecture.html), [Isaac Lab-Arena](https://isaac-sim.github.io/IsaacLab-Arena/main/index.html))

**Implication:** The productive design is not necessarily VLM *or* physics. A VLM can close the observational loop on real hardware, while simulation supplies precise rehearsal feedback before deployment.

### Q4. How does GaP's Pack Grocery Items scene differ from our high-mix packing scene in `spatio_monorepo`, and can we reuse GaP's assets?

**Why I am asking:** Similar task names can hide different perception, planning, and generalization problems. I want a scene-level comparison and an asset audit that covers file formats, provenance, licensing, and the work required to run any GaP assets in our simulator.

**Working answer:** Interpreting “our high-mix scene” as `piper_grocery_packing_office_v0`, the scenes share a grocery-packing label but test materially different systems.

| Dimension | GaP Pack Grocery Items | `spatio_monorepo` PiPER grocery scene |
|---|---|---|
| Robot | One Franka/Panda in the released reproduction | Two PiPER arms |
| Objects | Six: alphabet soup, salad dressing, cream cheese, milk, tomato sauce, and butter in the released VAB task | Five: Celestial Seasonings tea carton, Colgate carton, KIND 12-bar carton, Adauxter HDMI pouch, and iPhone charging-cable pouch |
| Destination | Basket/container; paper's sim and real figures even use visibly different container forms | Fixed, open PHAREGE 12 × 9 × 4 inch tuck-top cardboard box |
| Episode contract | Pack all six; six grasp attempts; score is packed count out of six. Released VAB provides 50 initial poses and a `pack_all_into` predicate. | Fixed initial layout; 800 control steps at 30 Hz; timeout-only termination; currently no task metrics or success predicate |
| Variation | Paper reports `fixed` and `varied`, but does not define Pack's distribution precisely. Released VAB enumerates initial poses. | No reset randomization in this environment; product and box transforms are fixed |
| Simulation/control | Paper claims Isaac Sim 5.1.0/Isaac Lab; released reproduction is LIBERO/robosuite on MuJoCo 3.6.0 with Panda `OSC_POSE` | Isaac Lab-Arena, 120 Hz physics, 30 Hz control |
| Cameras | Released reproduction: `agentview` and `robot0_eye_in_hand`, 128 × 128 RGB, depth off; paper does not document the experimental camera specification | One top and two wrist views, 640 × 480 |
| Asset form | LIBERO/MuJoCo XML plus OBJ/MTL/PNG, `.msh`, and some collision OBJ/STL | Physics-authored USD/USDZ packages registered directly in Arena |

The local evidence is [scene config](/Users/sheil/Development/spatio_monorepo/robot/arena/environments/piper_grocery_packing_office_v0/config.py), [environment composition](/Users/sheil/Development/spatio_monorepo/robot/arena/environments/piper_grocery_packing_office_v0/environment.py), and [timeout-only task](/Users/sheil/Development/spatio_monorepo/robot/arena/environments/piper_grocery_packing_office_v0/task.py). The GaP release evidence is the [VAB task YAML](https://github.com/ehehee/Variational-Automation-Benchmark/blob/fd2bc0f63369ba39137df018bbca8f6b372ffa0b/tasks/libero_object_packing/pack_all_objects_v00.yaml) and its [object-packing task directory](https://github.com/ehehee/Variational-Automation-Benchmark/tree/fd2bc0f63369ba39137df018bbca8f6b372ffa0b/tasks/libero_object_packing).

Can we use the GaP assets? **Technically yes, but not as a drop-in replacement and not yet as blanket-cleared commercial assets.** The public VAB fork contains the object and basket files. OBJ/texture sources can be brought into Isaac and wrapped as USD, but each object needs scale checks, collision geometry, mass/inertia, friction/contact tuning, materials, stable placement, and grasp validation. The LIBERO workcell is generated through robosuite rather than shipped as one portable USD.

The license chain also needs care. VAB's root is MIT and upstream LIBERO labels its code MIT and datasets CC BY 4.0, but neither provides a complete asset-by-asset provenance ledger for every `stable_hope_objects`, scanned, or third-party mesh. Branded textures also deserve a redistribution review. These files are reasonable for an internal conversion prototype with attribution; importing them into a redistributable commercial asset catalog should wait for provenance review. ([VAB license](https://github.com/ehehee/Variational-Automation-Benchmark/blob/fd2bc0f63369ba39137df018bbca8f6b372ffa0b/LICENSE), [LIBERO license statement](https://github.com/Lifelong-Robot-Learning/LIBERO#license), [Isaac MJCF importer](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/importer_exporter/import_mjcf.html))

**Implication:** GaP's assets are most useful as an additional benchmark set, not a replacement for the existing USD grocery assets. Before comparing policies, our PiPER scene needs an actual packed-count success metric and reset variation; otherwise it is a fixed visual scene rather than an equivalent benchmark.

### Q5. Why does the paper call CaP-X "an ablation of GaP with a single agent and without self-learning"?

**Why I am asking:** An ablation normally removes one component while holding the rest of the system fixed. The CaP-X comparison appears to change the policy representation, agent structure, prior knowledge, skill interface, and improvement loop at once. The paper also says GaP did not use self-learning on the two grocery benchmarks in Table 1.

**Working answer:** The authors are using “ablation” loosely. Their conceptual argument is that CaP-X resembles GaP after removing two headline ingredients: hierarchical multi-agent graph authoring and simulator-based self-learning. What remains is one coding agent using visual/task feedback to generate or revise executable code.

That makes CaP-X an external baseline that is *conceptually ablated*, not a controlled ablation. A conventional ablation would hold the task information, skill interfaces, representation, observation history, execution runtime, and budget constant while changing one component. Here several variables change at once:

- initial image and language versus GaP's known workcell geometry;
- free-form Python versus a typed, validated graph;
- one general coding agent versus specialized graph/skill/verification agents;
- different perception, planning, and skill scaffolding;
- different runtime and recovery behavior.

The paper acknowledges that the comparison is not fair because GaP has known geometry. More importantly, it says GaP used **no self-learning on Benchmarks I and II** because its first graph already performed well. Table 1 therefore cannot measure the contribution of self-learning even though the text calls CaP-X “without self-learning.” It compares complete systems under different information and representation assumptions.

Section 5.4's graphless, single-LLM, and graph-validation variants are closer to actual component ablations. The Make Popcorn improvement from 33% to 94% over iterative rehearsal is the paper's direct evidence for self-learning, although it is still not a randomized one-factor experiment. ([GaP comparison protocol](https://arxiv.org/html/2607.05369v1#S5.SS2), [GaP ablations](https://arxiv.org/html/2607.05369v1#S5.SS4), [CaP-X experimental axes](https://arxiv.org/html/2603.22435v1#S3))

**Implication:** Table 1 supports an end-to-end claim—GaP's full structured system did better in that setup. It does not establish how much of the gap came from multi-agent authoring, graph representation, geometry access, validation, or self-learning.

### Q6. Why does cuRobo fail in the Section 5.3.1 simulation results, and shouldn't IK always work?

**Why I am asking:** The paper attributes some failures to cuRobo even though inverse kinematics can often produce a joint configuration for a requested end-effector pose. I want to separate pose-level IK from collision-free trajectory planning, grasp feasibility, controller execution, and the way GaP formulated the planning query.

**Working answer:** The paper does not show that cuRobo's IK is broken. It reports that the complete **TipTop pipeline—M2T2 grasp proposals, cuRobo motion generation, and cuTAMP task-and-motion planning—could not produce feasible plans** for many scene instances. No per-component status, IK residual, collision trace, or log is provided, so attributing the failures to cuRobo alone would go beyond the evidence. GaP itself uses cuRobo services and reports high success on the same task family.

IK also does not “always work.” It answers a narrower question: does some joint configuration put the end effector at a requested position and orientation, subject to the solver's model and tolerances? Failure is normal when the pose is outside the workspace, overconstrained in orientation, beyond joint limits, self-colliding, or colliding with the world.

Even successful endpoint IK is only one gate:

1. The target joint state can exist while no collision-free path connects it to the current state.
2. A grasp needs feasible pre-grasp, approach, contact, closure, lift, transport, and release segments—not one endpoint.
3. Basket rims, table clearance, neighboring products, and the grasped object's volume constrain the whole swept path.
4. Contact-aware planning must permit intended finger/object contact without permitting unwanted collision.
5. cuRobo uses finite seeds and local trajectory optimization. Failure to find a path before the search/timeout ends is not a mathematical proof that no path exists.
6. After planning, calibration, control tracking, gripper closure, slip, and attached-object clearance can still fail.

The paper's real failures on cubic objects, tall baskets, and varied object orientations are consistent with bad or tightly constrained grasp candidates: a cube-face proposal can force an awkward wrist orientation, and a tall rim can eliminate the approach/lift corridor. That is a plausible diagnosis, not a reported component log. An inaccurate or overly conservative collision world and finite-search local minima are other possibilities.

Official cuRobo documentation visualizes workspace points with no IK solution, defines motion generation over collision and joint/velocity/acceleration/jerk constraints, and describes grasp planning as a multi-segment optimization. ([Isaac Sim IK example](https://curobo.org/get_started/2b_isaacsim_examples.html), [cuRobo technical report](https://curobo.org/reports/curobo_report.pdf), [motion-planning documentation](https://nvlabs.github.io/curobo/latest/getting-started/motion_planning.html))

**Implication:** The result is better read as a failure of the M2T2 → cuRobo → cuTAMP problem formulation and candidate pipeline under those scenes, not as evidence that IK should have succeeded or that cuRobo is defective.

### Q7. What does "accumulated kinematic error" mean in Section 5.5?

**Why I am asking:** The phrase could refer to IK residual, controller tracking error, calibration error, numerical integration drift, or an object-pose error propagated through a chain of open-loop moves. I want to identify what the paper actually measured and why the revised popcorn policy reduced it.

**Working answer:** The paper does not define or measure “accumulated kinematic error.” It is a qualitative diagnosis for one of 20 physical Make Popcorn trials, explicitly separate from the other failure, which the authors call an IK error during linear Cartesian motion. A commented-out note in the public arXiv source uses the fuller phrase “kinematics accumulation error of long-horizon rollout,” confirming that the authors mean error building across the long action chain rather than simulator integration drift.

The most plausible technical meaning is an accumulating mismatch between the geometry the policy believes and the physical geometry it achieves:

`camera/depth → hand–eye/world transform → handle pose → commanded grasp pose → achieved end-effector pose → actual gripper-to-pan transform → commanded placement or regrasp`

Small errors can enter at each transform: wrist-camera calibration, depth/segmentation, TCP calibration, forward kinematics, controller tracking, gripper contact, or pan slip. The object-in-hand transform is especially important. If the real transform from the end effector to the pan differs from the one assumed when computing a drop pose, the pan will be displaced from the burner even when the end effector reaches its commanded pose. A more repeatable handle grasp makes a learned placement offset more meaningful.

Several limits matter:

- The paper publishes no desired-versus-measured joints or end-effector poses, calibration residuals, numerical error curve, failing-trial trace, or identity of the final misgrasp. It does not isolate which error accumulated.
- The graph re-perceives the handle before removing the pan, so it is not blindly propagating one initial pan estimate through the whole episode. Fresh perception can correct some earlier error and introduce new error.
- Repeated absolute closed-loop motions do not inherently accumulate drift. Accumulation requires chained relative commands, persistent calibration bias, an incorrect held-object transform, slip, or another state discrepancy that is not reset. The paper does not disclose the relevant script details.
- It cannot mean simulation numerical drift because this failure occurred in a physical rollout. It should not be collapsed into IK residual either, since the paper lists the IK failure separately.

The rehearsal edits provide context but are not a causal diagnosis of this physical failure. GaP changed from pure GraspGen to a GraspGen/OBB mixture, localized the pan handle, and then tuned the pan-placement offset required by the new grasp. Those edits raised simulation success from 33% to 94%; the accumulated-error misgrasp still happened under the revised policy. ([paper §5.5](https://arxiv.org/html/2607.05369v1#S5.SS5), [full popcorn graph](https://arxiv.org/html/2607.05369v1#A3.SS5), [sample rehearsal feedback](https://arxiv.org/html/2607.05369v1#A5.SS2))

The current skill release offers a useful implementation clue, not experiment telemetry: its drop-pose calculation preserves the gripper-to-object relationship and documents problems caused by stale pre-grasp end-effector state and wrist-yaw changes. ([current `compute_drop_pose`](https://github.com/graph-robots/open-robot-skills/blob/main/skills/transporting-objects/scripts/compute_drop_pose.py#L90-L177))

**Implication:** “Accumulated kinematic error” is a plausible engineering postmortem label, not a demonstrated failure mechanism. The result shows a residual long-horizon sim-to-real reliability problem, but the paper does not provide enough telemetry to locate it.

## Emerging thesis

*To be written after the questions reveal the central argument.*

## Research record

This section tracks parallel research and prevents unsupported claims from entering the post. It will be removed or condensed before publication.

| Question | Research angle | Agent | Status | Best evidence |
|---|---|---|---|---|
| Q1 | Paper, appendix, figures, and benchmark setup | paper-hardware | Complete | Paper §§4.2, 5.1; released configs; manufacturer datasheets |
| Q1-Q2 | Project site, public implementation, and configuration | project-sources | Complete | Paper appendices; public-beta architecture and controller config |
| Q1-Q3 | Platform documentation and cuRobo capability boundary | platform-sources | Complete | Official cuRobo, ROS, Isaac Sim, and Isaac Lab documentation |
| Q3 | CaP-X paper and official implementation | project-sources | Complete | CaP-X §3.2 and VDM prompt/execution code |
| Q3.2 | VLM feedback versus simulator rehearsal | All three agents | Complete | CaP-X ablations; Isaac physics and Arena documentation |
| Q4 | GaP assets and scene versus `spatio_monorepo` high-mix packing | All three agents plus local repository inspection | Complete | VAB task/assets and local PiPER scene/task files |
| Q5 | Meaning and validity of the CaP-X "ablation" | project-sources plus paper audit | Complete | GaP §§5.2 and 5.4; CaP-X experimental axes |
| Q6 | Exact cuRobo failure mechanism; IK versus motion-planning feasibility | paper-hardware plus platform-sources | Complete | GaP §§5.2–5.3; official cuRobo planning documentation |
| Q7 | Meaning and evidence for accumulated kinematic error in Make Popcorn | project-sources plus paper audit | Complete | GaP §5.5, full task graph, arXiv source, current transport skill |

## Draft synthesis

*The final narrative will go here after we answer the questions. It should connect the answers into an argument rather than preserve the order in which we investigated them.*
