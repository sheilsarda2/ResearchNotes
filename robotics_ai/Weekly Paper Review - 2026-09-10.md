# Weekly Paper Review - September 10, 2026

## RoFacto: Robot-Factored World Models via Robot Rendering

Seoul National University + RLWRLD. Byungjun Kim, Taeksoo Kim, Hyunsoo Cha, Hanbyul Joo. [arXiv 2607.22535](https://arxiv.org/abs/2607.22535) · [project page](https://bjkim95.github.io/rofacto/) · [code placeholder](https://github.com/bjkim95/rofacto). July 2026 preprint; release status checked September 10.

[![Original and edited robot renderings above their corresponding generated videos](media/rofacto/action-edit.gif)](https://bjkim95.github.io/rofacto/static/videos/counterfactual/pick_tube.mp4)

**Change the motion; watch the prediction change.** Top: original and edited robot renderings. Bottom: generated videos. Near the end, each prediction lifts a different object from the same initial scene. [Full video](https://bjkim95.github.io/rofacto/static/videos/counterfactual/pick_tube.mp4) · [Authors’ example](https://bjkim95.github.io/rofacto/#action-controllability)

Companion: [27 questions from the annotated paper, with researched answers](RoFacto%20-%20Questions%20and%20Research.md).

[Read with video controls](Weekly%20Paper%20Review%20-%202026-09-10.html). Previews use the authors’ released videos at their original speed; labels sit outside the imagery. Click any preview for the full video. [Media credits](media/rofacto/README.md).

RoFacto moves command realization and robot rendering outside a learned video world model. The robot's controller and kinematics turn candidate commands into motion; the URDF and camera calibration turn that motion into images. The video model receives the rendered robot and predicts the scene's response. Its contribution centers on **which motion gets rendered**: a nominal trajectory computed before interaction, paired with depth that helps distinguish projected overlap from possible contact.

This division of work is useful because an action command does not specify the resulting image. A joint target must pass through controller tracking and actuation limits. The resulting robot configuration must then project through a camera. A video model receiving numeric commands has to learn those relationships alongside object dynamics. RoFacto supplies them through explicit preprocessing and leaves the interaction prediction to the network.


### Nominal motion before interaction

A raw command, a nominal trajectory, and a recorded robot trajectory contain different information. RoFacto replays DROID's joint and gripper targets in a scene-free, robot-only Isaac Lab environment. The replay accounts for controller behavior and actuation limits without already simulating the interaction with the objects. RoboCasa-GR1 uses a collision-free shadow rollout from the clip's starting state. These produce the nominal motion that gets rendered. [Paper, §3.2 and Appendix A](https://arxiv.org/abs/2607.22535v1).

Consider a command to close a gripper around a cup. In an empty scene, the fingers can close farther than they would around the cup. The recorded future finger positions therefore contain evidence about whether contact occurred. Feeding those positions to a predictor helps it reproduce the video, but also gives it part of the interaction outcome. That is the paper's future-state leakage concern. The nominal trajectory instead describes what the robot would do before the scene intervenes; the model must predict both the object's response and the robot's contact-induced departure from that trajectory.

[![Synchronized raw-target, nominal-motion, and logged-motion overlays from the same DROID episode](media/rofacto/nominal-motion.gif)](media/rofacto/nominal-motion.mp4)

**Compare raw targets, nominal motion, and logged motion.** The outlines come from the same DROID episode and play in sync. They are diagnostic overlays on observed footage, not generated outputs or literal conditioning videos; the background is for visual alignment. [Full video](media/rofacto/nominal-motion.mp4) · [Authors’ example](https://bjkim95.github.io/rofacto/#nominal-trajectory-conditioning)


This distinction concerns the task being evaluated. A video generator can legitimately accept a prescribed skeleton or motion sequence. The problem arises when a result conditioned on *realized future motion* is presented as predicting the consequences of an unexecuted command. Rendering does not remove leakage by itself; the source of the rendered motion determines what information reaches the model.

### Depth and contact

The full interface supplies four visual streams: static scene RGB, static scene depth, robot mesh RGB, and end-effector depth. With a fixed camera, the static RGB stream repeats the initial observation. For a moving camera, the scene must be rendered along its trajectory. The scene description supplies appearance context; the paper excludes intended actions and future outcomes from the text prompt. [Paper, §§3.1–3.5](https://arxiv.org/abs/2607.22535v1).

Imagine the gripper passing behind a cup. Their projections overlap, but the cup should hide the gripper, and the cup should not move merely because the images overlap. Move the gripper closer to the camera and the occlusion order reverses. Bring it to the cup's depth and contact becomes possible. End-effector depth needs scene depth to make these comparisons meaningful. The pair supplies geometric evidence; it does not prove that the fingers establish a stable grasp.

[![Without depth, with paired depth, and recorded reference: compare the white paper bag](media/rofacto/depth-contact.gif)](https://bjkim95.github.io/rofacto/static/videos/depth/depth_c02_full.mp4)

**Watch the white bag.** Without depth / with paired depth / recorded reference. The no-depth prediction moves the bag with the approaching gripper and bottle; the other two keep it approximately stationary. This example shows the false-contact error that the depth pair targets. [Full video](https://bjkim95.github.io/rofacto/static/videos/depth/depth_c02_full.mp4) · [Authors’ example](https://bjkim95.github.io/rofacto/#impact-of-depth-conditioning)


The generator still produces the complete future video. It does not paste the rendered arm over an unchanged photograph or solve contact forces. RoFacto adapts a Wan2.1 14B video inpainting model using additional conditioning channels and LoRA. Static context gives it a reference for the scene; the learned model accounts for interaction-induced changes, including objects leaving their original positions and exposing previously hidden surfaces. [Paper, §3.5 and Appendix B](https://arxiv.org/abs/2607.22535v1).

[![Authors’ RoFacto architecture diagram](media/rofacto/method-overview.png)](https://bjkim95.github.io/rofacto/static/image/overview.png)

*The authors’ method overview.* Follow the robot motion and static-scene streams into the shared model. The [Q&A](RoFacto%20-%20Questions%20and%20Research.md#19-what-is-a-dit-block-and-what-follows-it-in-the-architecture-diagram-p-4-and-earlier-question) expands the sampling step compressed in this diagram.


### Novelty

Visual action conditioning predates RoFacto. [Visual Action Prompts](https://zju3dv.github.io/VAP/) uses camera-aligned skeleton videos, with mesh and depth variants, to communicate human and robot motion. Its robot training prompts come from state logs. [Dexterous World Models](https://snuvclab.github.io/dwm/) supplies the closer architectural precedent: static-scene renderings along the camera path, hand-mesh renderings, and an inpainting initialization intended to preserve scene appearance while learning interaction changes.

RoFacto's specific advance over those precedents is nominal command realization before rendering, used consistently at training and inference, plus the paired end-effector/scene-depth interface. The paper tests both additions. The contribution rests on choosing and constructing a usable robot-motion signal, with the network architecture inherited from earlier work.

### Evidence

The DROID-only ablation is the clearest evidence. The dataset and backbone stay fixed while the input changes:

| Conditioning | PSNR ↑ | SSIM ↑ | LPIPS ↓ |
|---|---:|---:|---:|
| Raw-action mesh | 21.57 | 0.860 | 0.175 |
| Nominal mesh | 22.44 | 0.872 | 0.164 |
| Nominal mesh + end-effector/scene depth | 23.08 | 0.874 | 0.161 |

Replaying commands before rendering improves all three reported means; adding the depth pair improves them again. PSNR rescales pixel error, SSIM compares local image structure, and LPIPS measures distance in learned visual features. None measures grasp success. Both depths enter together, so the experiment does not isolate their individual contributions. [Paper, Table 2](https://arxiv.org/abs/2607.22535v1).

The jointly trained Wan model gives a separate comparison: on DROID, numeric AdaLN conditioning scores 18.57 PSNR, 0.824 SSIM, and 0.224 LPIPS; mesh plus depth scores 21.87, 0.859, and 0.178. The numeric baseline already receives **nominal** states. This comparison tests the rendered interface against numeric conditioning, while Table 2 tests the source of the rendered trajectory and the addition of depth. The SVD group uses different resolution and training data, so its numbers should not be ranked against Wan's. Downsampling can hide fine boundary and texture errors, making a coarse prediction look closer to its reference. [Paper, Table 1 and Appendix B](https://arxiv.org/abs/2607.22535v1).

[![DROID: AdaLN prediction, RoFacto prediction, and recorded reference](media/rofacto/droid-comparison.gif)](https://bjkim95.github.io/rofacto/static/videos/droid/rank041.mp4)

**A published DROID comparison.** AdaLN prediction / RoFacto prediction / recorded reference. Follow the robot and yellow object through the lift. This illustrates the comparison; the reported means summarize the held-out clips. [Full video](https://bjkim95.github.io/rofacto/static/videos/droid/rank041.mp4) · [Authors’ example](https://bjkim95.github.io/rofacto/#results)


### Grains of salt

Better reproduction of the robot can improve whole-image scores without establishing better contact prediction. Small object movements and brief missed grasps may occupy few pixels. DROID's numeric baseline also describes only the end-effector pose and gripper, while the rendered input exposes the full robot geometry and camera alignment; the full method adds depth as well. This is a useful system comparison, but it does not hold every piece of input information equal.

The evaluation uses 256 held-out DROID clips and 128 RoboCasa-GR1 clips. The tables give no confidence intervals or repeated-training variability. DROID contains mostly successful demonstrations, which the authors acknowledge leaves relatively little evidence about failures. The paper reports no closed-loop policy evaluation, prospective action-ranking accuracy, or hardware task-success test. Four denoising steps with a distillation LoRA reduce sampling work, but the paper gives no wall-clock latency for online planning. Its moving-view setup receives a camera trajectory and static scene from the simulator, prerequisites that require additional work in a real environment. [Paper, §§4.1–5 and Appendix B](https://arxiv.org/abs/2607.22535v1).

### Embodiment and motion transfer

The unseen xArm 6–Inspire F1 pairing and bimanual Panda examples demonstrate that the same trained model can consume new rendered robot configurations. The evidence is qualitative. An unchanged input format and plausible example videos do not establish reliable performance across arbitrary embodiments.

[![xArm and Inspire hand: mesh input, generated video, and recorded reference](media/rofacto/unseen-hand.gif)](https://bjkim95.github.io/rofacto/static/videos/embodiment/hrdex_banana.mp4)

**An unseen xArm 6–Inspire F1 pairing.** Mesh input / generated video / recorded reference. Watch the hand and banana. The model also receives scene and end-effector depth, omitted from this strip. [Full video](https://bjkim95.github.io/rofacto/static/videos/embodiment/hrdex_banana.mp4) · [Authors’ example](https://bjkim95.github.io/rofacto/#zero-shot-embodiment-generalization)


For human demonstrations, RoFacto retargets hand motion, solves robot-arm motion, renders the robot, and then generates the interaction video. That ordering matters. DWM's earlier robot-video extension replaced the human in an already-generated video. RoFacto supplies robot geometry *before* predicting the scene response, giving the generator an opportunity to account for the substituted body. It does not establish that every retargeted human grasp becomes physically executable. [RoFacto, Appendix D](https://arxiv.org/abs/2607.22535v1), [DWM, Appendix F](https://arxiv.org/html/2512.17907v1#A6).

[![Recorded human demonstration beside the generated robot video](media/rofacto/human-retargeting.gif)](https://bjkim95.github.io/rofacto/static/videos/human2robot/detergent.mp4)

**Human demonstration → generated robot video.** The human recording supplies the motion to retarget. The right-hand result is generated, not an execution recording. Compare the reach and lift rather than treating the demo as a robot success test. [Full video](https://bjkim95.github.io/rofacto/static/videos/human2robot/detergent.mp4) · [Authors’ example](https://bjkim95.github.io/rofacto/#application-human-demonstration--robot-video)


### The Hydra-0 connection

The September 1 review covered [Hydra-0](https://arxiv.org/abs/2608.18077), whose deployment interface projects controller-and-physics rollouts into visible point tracks. RoFacto was submitted July 24; Hydra-0 followed August 18. That earlier date qualifies the previous review's broad “no prior instance found” assessment of splitting work between a physics engine and a video model. Hydra-0's particular flow interface remains distinguishable from RoFacto's mesh/depth interface.

Hydra-0 also exposes the distinction RoFacto emphasizes: its reported policy-evaluation correlation uses **achieved-trajectory replay**. The model receives recorded robot motion, and the policy does not act on generated observations. That experiment measures agreement under replay; it does not resolve prediction from a nominal command sequence. [Hydra-0, §§2.4 and 4.5](https://arxiv.org/html/2608.18077v1#S2.SS4).

### Usable

RoFacto's official repository still says “Code coming soon.” DWM is a runnable precursor with [training/inference code](https://github.com/snuvclab/dwm) and released [CogVideoX 5B](https://huggingface.co/byungjun-kim/DWM-CogVideoX-Fun-5b-LoRA) and [Wan2.1 14B](https://huggingface.co/byungjun-kim/DWM-Wan2.1-Fun-14b-LoRA) checkpoints. It provides implementation groundwork; those checkpoints do not already implement RoFacto's nominal robot/depth interface.

The first experiment I would run is narrower than a policy-learning loop: take proposed command segments, run robot-only nominal replay, and render them before physical execution. Compare mesh-only and paired-depth predictions against the recorded interactions, scoring false contact, missed grasps, object displacement, and occlusion order. Include failures and near misses. That would test whether the interface improves the scene response that matters for planning, beyond reproducing the robot.

The depth-camera teleoperation idea in the annotations deserves its own test. A calibrated RGB-D view can provide scene geometry and a ghost-gripper overlay. Replacing the SpaceMouse also requires a way to express motion, such as camera-based hand tracking and retargeting; [AnyTeleop](https://arxiv.org/abs/2307.04577) is a relevant precedent. I would first measure whether the depth overlay improves placement with the existing input device, then test replacing the device. RoFacto motivates the geometric hypothesis, but these would be new experiments.
