# MiniMax H3 T2VA — Storytelling Supplement

This supplement is used together with the official MiniMax H3 prompt-writing rules for story-driven text-to-video generation. The official rules control field names and timing syntax; this supplement controls cross-clip identity, location, state, and story continuity when no reference image is available.

The application injects only the compact block below into the LLM system prompt. The longer guidance after it documents the reasoning behind that contract.

<!-- runtime-prompt:start -->
Mandatory T2VA storytelling and continuity contract:
- Treat this clip as a completely independent generation request. Assume the video model has zero knowledge of any earlier or later clip.
- Make `integrated_multimodal_description` self-contained. Before the first shot action, explicitly establish the complete visual identity of every principal character visible in this clip: stable name or global ID, apparent age, face and skin features, eye color when relevant, hair color/style/length, body build, complete wardrobe with exact colors/materials, footwear, and persistent accessories or props.
- Explicitly restate the complete scene in every clip: exact location, time of day, weather if relevant, lighting direction/color/quality, major spatial geometry, doors/windows/furniture/landmarks, recurring props, and the characters' starting positions.
- Reuse canonical wording from `continuity_bible` and `preceding_segments` as literally as practical. Do not paraphrase stable visual facts into new colors, shapes, materials, ages, hairstyles, costumes, layouts, or lighting.
- Never use continuity shorthand such as “same as before”, “same character”, “the previous room”, “continues from the last clip”, or equivalent wording in any language. Replace it with the complete concrete description.
- Character and speaker IDs are global story identities. Use IDs already supplied by the segment or continuity bible and never renumber them because speaking order changes in this clip.
- Restate the incoming physical state needed to understand the clip: pose, facing direction, held objects, object placement, damage/wetness/dirt, open/closed doors, and other persistent changes. Then describe only the changes caused by this clip.
- Keep one canonical scene design. Camera angle may reveal a different portion of the location, but it must not silently redesign the architecture or move fixed landmarks.
- For dialogue, retain the full identity and scene descriptions even when they make the prompt longer. Frame the actual speaker clearly enough for visible lip, jaw, and facial movement; keep non-speakers' mouths inactive unless they speak.
- Before returning the prompt, perform a standalone test: a reader who has never seen any other prompt must know exactly who is present, what each person looks like and wears, where the scene occurs, how it is lit, and what state the scene begins in.
<!-- runtime-prompt:end -->

## 1. Why T2VA Needs Stronger Restatement

T2VA receives no image anchor. Each generated clip starts from text alone, so a pronoun, a character name, or “the same room” carries no usable visual information. Cross-clip continuity therefore depends on repeating stable, concrete language rather than relying on narrative memory.

## 2. Build Canonical Bibles Before Clip Prompts

The storyboard phase must create a global character bible and scene bible before producing individual H3 prompts.

Each principal-character entry should lock:

- one global character/speaker ID
- apparent age and body build
- face shape, skin details, eyes, and distinctive marks
- hair color, length, texture, and style
- complete wardrobe, footwear, accessories, and recurring props
- facts that may change only as explicit story state

Each recurring-location entry should lock:

- place type, scale, and spatial layout
- wall, floor, ceiling, door, window, and furniture design
- fixed landmarks and recurring props
- time of day, weather, and lighting direction/quality
- stable palette, materials, and atmosphere

Canonical descriptions should be copied into each relevant clip with minimal wording drift.

## 3. Every Clip Is a Standalone Specification

Every T2VA prompt must independently answer:

1. Who is visible?
2. Exactly what does each person look like and wear?
3. Where are they, and what fixed geometry surrounds them?
4. What is the exact time, weather, and lighting?
5. What physical state carries into the first frame?
6. What action and state change happen during this clip?

Do not depend on clip order to supply any of these facts. “Continue”, “still”, and “again” may describe story meaning only after the concrete visual facts have been fully restated; they must never substitute for those facts.

## 4. Preserve Global Identity and State

Character IDs and speaker IDs belong to the whole project, not one clip. If Lin is `(S1)` and Bai is `(S2)`, Bai remains `(S2)` even when Bai speaks first in a later clip.

Track state transitions explicitly. If a jacket becomes wet, a vase breaks, or a door opens, later clips must state that new condition until the story changes it again. Do not reset persistent state for visual novelty.

## 5. Keep Shot Design Physically Coherent

Within a clip, establish the location and starting positions before complex camera movement. Cuts may change framing, but characters, props, light direction, and fixed landmarks must remain spatially coherent. Prefer a simple readable shot plan over simultaneous actions that cannot fit the clip duration.

For on-screen dialogue, use a medium close-up, upper-body close-up, or facial close-up that keeps the active speaker visible. Voiceover must not cause visible people to lip-sync.

## 6. Final Continuity Checklist

Before accepting any T2VA prompt, verify:

- no sentence relies on a previous prompt
- all visible principal characters are fully redefined
- global IDs did not change
- wardrobe and accessories use the canonical wording
- location geometry and lighting are fully restated
- incoming object and character states are explicit
- dialogue framing and timing are physically achievable
- the prompt remains understandable when viewed by itself
