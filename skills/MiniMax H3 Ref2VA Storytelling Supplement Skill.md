# MiniMax H3 Ref2VA — Storytelling Supplement

The application injects only the compact block below into the LLM system prompt. The complete document remains the human-readable source of truth.

<!-- runtime-prompt:start -->
Mandatory Ref2VA storytelling and continuity contract:
- Treat this clip as a completely independent generation request. Assume the video model has zero knowledge of any earlier or later clip.
- In `subject_definitions`, bind each referenced person to the supplied `<Picture N>`, one stable global `<Subject N>`, and a speaker ID. Character/speaker IDs never change with local speaking order. Use a concise image-based identity lock, for example: `<Subject 1>: the young woman shown in <Picture 1>, maintain 100% absolute consistency in facial identity, skin texture, hairstyle, body anatomy, existing outfit, and accessories. No outfit changes. Photorealistic pores, individual hair strands, natural fabric physics. No CGI, no anime, no plastic skin.`
- When a person has a usable reference picture, do not redescribe facial features, skin, hair, body build, clothing, footwear, or accessories in `subject_definitions` or later sections. Refer to that person as `<Subject N>` and let the picture control those details. State an appearance or outfit change only when the user explicitly requests one. Fully describe people without reference pictures.
- Explicitly restate the complete scene: exact location, time of day, weather if relevant, lighting direction/color/quality, major geometry, fixed landmarks, recurring props, and starting positions. A reference image never replaces this text.
- Reuse canonical scene and story facts from `continuity_bible` and `preceding_segments`. If older appearance wording conflicts with a reference picture, the picture controls.
- Never use continuity shorthand such as “same as before”, “same character”, “the previous room”, “continues from the last clip”, or equivalent wording in any language. Replace it with the complete concrete description.
- Restate incoming physical state: pose, facing direction, held objects, object placement, damage/wetness/dirt, open/closed doors, and other persistent changes. Then describe only the changes caused by this clip.
- Keep subject count exact. Do not duplicate a person because the same person appears in multiple references, reflections, or shot descriptions.
- For dialogue, retain each speaker's `<Subject N>` identity link and the complete scene description. Show the actual speaker clearly enough for visible lip, jaw, and facial movement; keep non-speakers' mouths inactive unless they speak.
- Before returning the prompt, perform a standalone test: a reader who has never seen any other prompt must know which supplied picture defines each referenced person, where the scene occurs, how it is lit, and what state the scene begins in. Do not turn the picture into a competing written portrait.
<!-- runtime-prompt:end -->

## Purpose

This supplement is used **together with the official MiniMax H3 `h3-prompt-writing` skill** for story-driven Ref2VA video generation.

The official skill controls the required Ref2VA structure and syntax.  
This supplement controls:

- character consistency
- scene continuity
- dialogue pacing
- lip-sync-friendly framing
- time-of-day consistency
- prevention of duplicated characters
- physically plausible shot design
- adaptation of story outlines into independent 15-second clips

When this supplement is provided, the user should normally only need to supply:

1. story outline
2. reference pictures for principal characters
3. approximate setting and time
4. desired number and duration of clips

---

# 1. Always Follow the Official Ref2VA Structure

Do not replace or modify the official Ref2VA six-section format:

```text
subject_definitions:
summary:
retention_analysis:
detailed_description:
overall_soundscape:
non_diegetic_music:
```

Follow the official MiniMax H3 rules for:

- `<Subject N>`
- `<Picture N>`
- speaker IDs `(S1)`, `(S2)`
- `<d>[Language] ...</d>`
- `[Shot N]`
- timestamps
- soundscape
- non-diegetic music

This supplement only adds storytelling and generation-stability rules.

---

# 2. Treat Every Video Clip as Completely Independent

Every generated clip must be self-contained.

Never write:

```text
same as the previous scene
continue from before
the same gallery as earlier
the same street
use the previous costume
use exactly the same room as later scenes
```

Instead, every prompt must independently restate:

- all principal-character definitions
- clothing
- hairstyle
- relevant accessories
- exact time of day
- location
- lighting
- important scene geometry
- important recurring props

Assume the video model has **zero knowledge of any previous clip**.

---

# 3. Build a Global Character Bible Before Writing Clips

Before generating the individual prompts, create one canonical visual description for every principal character.

Example:

```text
<Subject 1> = Lin
<Subject 2> = Bai
```

The wording describing the characters should remain as stable as possible across all clips.

## Character numbering must never change

Speaker IDs are global story identities, not local speaking order.

For example:

```text
<Subject 1> = Lin = (S1)
<Subject 2> = Bai = (S2)
```

Even if Bai speaks first in a particular clip:

```text
Bai (S2)
```

must remain `(S2)`.

Never renumber characters merely because one person speaks first.

---

# 4. Reference Image Overrides Written Assumptions

When a character reference image is provided, **use the image as the authority for identity and clothing**. Bind it to `<Subject N>` in `subject_definitions`, then refer to that subject by ID in later sections. Avoid a second written portrait or outfit inventory; those details can compete with the image during generation.

Do not rely on an earlier written costume description if it conflicts with the image.

Priority:

```text
reference image
> explicit latest user correction
> earlier textual character description
```

For a referenced person, a suitable opening is:

```text
<Subject 1>: the young woman shown in <Picture 1>, maintain 100% absolute consistency in facial identity, skin texture, hairstyle, body anatomy, existing outfit, and accessories. No outfit changes. Photorealistic pores, individual hair strands, natural fabric physics. No CGI, no anime, no plastic skin.
```

Do not describe the person's face, hair, body, or outfit again later in the prompt unless the user explicitly asks for a change. If a person has no usable reference image, describe that person's visible details in text instead.

---

# 5. Do Not Transfer Studio Lighting From Character References

Character sheets are often brightly lit studio photographs.

For non-studio scenes explicitly state:

```text
Do not transfer the bright studio lighting or neutral studio
background from <Picture 1> or <Picture 2>. The reference
pictures provide character identity, facial features, hairstyle,
body proportions and wardrobe only.
```

This is especially important for:

- night streets
- bars
- restaurants
- dark interiors
- dawn or sunset scenes

---

# 6. Create a Scene Bible for Every Recurring Location

If several clips occur in one location, first define a **canonical scene bible**.

Example dimensions:

```text
10 m × 7 m rectangular gallery
3.6 m ceiling
warm-white walls
light-gray polished concrete floor
two black lighting tracks
three fixed paintings on left wall
one smoky-glass sculpture at center
one black-framed window on right wall
one matte-black beverage counter at back-right
```

Then repeat essentially the **same wording in every clip** occurring there.

Do not casually change:

- room shape
- wall colors
- floor material
- windows
- ceiling
- furniture
- artwork count
- sculpture
- bar position
- entrance position

Otherwise the model will generate a new location.

---

# 7. Use Fixed Visual Anchors to Preserve Scene Continuity

Recurring locations should contain 2–4 unmistakable visual anchors.

Example gallery anchors:

```text
three abstract canvases on the left wall
smoky-gray glass sculpture in the center
black-framed floor-to-ceiling window on the right
matte-black beverage counter at the back-right
```

Every clip should show at least two anchors whenever practical.

When characters move inside the same location, describe movement relative to these fixed anchors:

```text
near the entrance
beside the central sculpture
two meters from the right-wall window
in front of the back-right beverage counter
near the third painting on the left wall
```

Do not create:

```text
another gallery room
side room
hallway
lounge
café
private room
```

unless the story genuinely changes location.

---

# 8. Time of Day Must Be Described as Visible Physics

A clock time alone is insufficient.

Writing:

```text
19:52
night
night lighting
```

may still produce daylight.

For strong nighttime consistency, describe physical evidence:

```text
fully after sunset
deep navy to nearly black sky
no remaining daylight
no sunset glow
no golden-hour illumination
streetlamps are the primary light source
dark gaps exist between pools of streetlamp light
warm apartment windows glow
vehicle headlights appear in the distance
faces are illuminated by localized streetlamp light
```

Also explicitly forbid unwanted lighting:

```text
No daylight.
No golden hour.
No sunset illumination.
Do not transition into daytime after any cut.
```

For daytime scenes similarly specify:

- direction of sunlight
- sky brightness
- window illumination
- shadow character
- approximate morning / noon / afternoon quality

---

# 9. The First Shot Establishes the Lighting State

The first shot strongly biases the rest of the generated clip.

Therefore Shot 1 must contain unmistakable visual evidence of:

- day
- night
- dawn
- dusk
- interior lighting

Do not rely on later shots to correct the lighting.

For night scenes, Shot 1 should ideally visibly contain:

- dark sky
- active streetlamps
- dark building façades
- illuminated windows
- headlights

---

# 10. Design Physically Possible Shots

Do not ask one shot to solve too many layers of visibility.

Bad example:

```text
Exterior tracking shot simultaneously showing two moving cars,
both characters' detailed faces through multiple reflective windows,
and precise mutual eye contact.
```

This is physically and generatively difficult.

Instead decompose spatial logic:

```text
Shot 1 — wide shot establishes vehicle positions
Shot 2 — over-the-shoulder shot establishes who is looking
Shot 3 — interior close-up shows the other person's reaction
```

General rule:

**Establish geometry first, then show perception, then show reaction.**

This applies to:

- vehicles
- windows
- mirrors
- crowded rooms
- long-distance eye contact
- characters looking through doors
- multi-level architecture

---

# 11. Dialogue Clips Need Enough Dialogue

For a 15-second story clip, one short sentence is usually too little.

Very sparse dialogue often causes:

- unnaturally slow speech
- long dead pauses
- visually boring shots
- loss of story logic

For dialogue-driven 15-second clips, normally use:

- about 3–5 dialogue beats
- roughly 3–6 shots
- short conversational sentences
- fast but natural Taiwan Mandarin pacing

A useful pattern:

```text
setup
response
counter-response
small escalation
reaction
```

Avoid long monologues.

Avoid forcing one sentence to occupy 6–8 seconds unless slow delivery is intentional.

---

# 12. Dialogue Must Preserve Cause-and-Effect Logic

Do not shorten dialogue until its context disappears.

Every exchange should allow the audience to understand:

1. what one person just did or assumed
2. why the other person reacts
3. what disagreement exists
4. whether attraction, discomfort, humor or tension remains

For example:

Bad:

```text
Bai: 妳住哪？
Lin: 附近。
```

Better:

```text
Bai asks a progressively more personal question.
Lin deliberately gives a broad answer.
Bai pushes for more detail.
Lin explains why she is withholding it.
```

The audience should not need narration to reconstruct missing context.

---

# 13. Convert Internal Monologue Into Dialogue and Behavior

For stories containing extensive internal thoughts, avoid narration unless explicitly requested.

Instead expose psychology through:

- short spoken reactions
- jokes
- correction
- hesitation
- facial expression
- pauses
- physical distance
- changing eye contact
- restrained gestures

A useful rule is:

**say half, perform half.**

For example, instead of writing:

```text
Lin secretly thinks Bai is attractive but too self-centered.
```

show:

```text
Lin laughs genuinely,
then says that he is moving too fast,
then maintains a little more physical distance.
```

The audience should be able to understand both attraction and discomfort.

---

# 14. Dialogue Shots Must Be Lip-Sync Friendly

When a character speaks, show at least:

```text
chest-up medium close-up
medium close-up
close-up
```

Avoid delivering important dialogue in:

- full-body wide shots
- distant two-shots
- crowded establishing shots

When dialogue begins, cut to the speaker.

---

# 15. Prefer One Speaker Per Dialogue Shot

To reduce lip-sync confusion and duplicated characters, dialogue shots should normally show only the speaking principal character.

Example:

```text
cut to Bai in chest-up close-up.
Lin is completely off-screen.
Bai (S2) says...
```

Then:

```text
cut to Lin in chest-up close-up.
Bai is completely off-screen.
Lin (S1) replies...
```

Avoid unnecessary constructions such as:

```text
Bai speaks while another Bai-like figure appears blurred behind Lin.
```

or:

```text
Lin close-up with Bai's foreground shoulder.
```

Use reaction shots instead.

---

# 16. Prevent Principal-Character Duplication Explicitly

When H3 tends to generate two copies of a character, add hard constraints.

Example:

```text
There is exactly ONE instance of <Subject 1>, Lin,
and exactly ONE instance of <Subject 2>, Bai.
Do not duplicate either principal character.
```

For background actors add:

```text
No background guest resembles Bai.
No male passerby wears Bai's distinctive navy blazer and burgundy scarf.
```

For close-ups:

```text
The other principal character is completely off-screen.
```

For reflective environments:

```text
Reflections in windows and mirrors must not reproduce either principal character.
```

These rules are especially important in:

- galleries
- parties
- crowds
- night streets
- glass interiors
- mirror-heavy environments

---

# 17. Keep Two-Shots Brief

Two-person compositions are useful primarily for:

- establishing physical distance
- showing movement together
- showing relationship geometry
- transitions

A good dialogue sequence is often:

```text
1–2 s brief two-shot
→ close-up speaker A
→ close-up speaker B
→ close-up speaker A
→ reaction
```

This improves:

- lip synchronization
- identity stability
- facial acting
- control of interpersonal distance

---

# 18. Background Extras Must Have Controlled Appearance

If background extras are needed, specify:

- approximate number
- age range
- general clothing palette
- that none resemble principal characters

Example:

```text
Approximately fifteen adult Taiwanese gallery guests,
wearing cream, gray, black and muted navy smart-casual clothing.
No background guest resembles either principal character.
No background man wears Bai's burgundy scarf.
```

Avoid overly detailed background characters.

---

# 19. Avoid Generated Written Text

Unless visible text is essential to the story, eliminate it.

Avoid:

- signs
- labels
- posters
- invitations
- laptop documents
- phone interfaces
- LINE messages
- QR codes
- addresses
- subtitles
- license plates
- handwritten letters

Useful instructions:

```text
No readable text appears.
Phone screen faces away from camera.
Notebook is angled away from camera.
Artwork labels do not exist.
Book spines are blurred or turned away.
```

If the story requires the content of written material, have a character **say it aloud** instead.

---

# 20. Preserve Story Information Without Showing Phone Screens

For messaging, searching, photography or contact exchange, use actions and dialogue.

Instead of showing:

```text
LINE username
search results
address
message text
```

show:

```text
Bai holds an unbranded phone with the screen turned away.
He tells Lin what he searched.
```

or:

```text
Bai asks to exchange LINE contact information,
but no QR code or phone interface is shown.
```

---

# 21. Romantic Projection Should Be Shown as Projection

When a story intentionally portrays a character misunderstanding attraction, do not automatically portray the other character as reciprocating everything.

Differentiate:

```text
聊得來
interested
attracted
```

from:

```text
already understands her
destined lovers
soulmates
mutual love
```

Use dialogue to make the gap visible.

A useful dramatic progression:

```text
real chemistry
→ premature interpretation
→ assumption
→ boundary crossing
→ correction
→ partial learning
```

This produces a more believable story than making one character purely villainous from the beginning.

---

# 22. Use Classical Quotes Sparingly

When adapting classical literature into a modern story, original lines can be used as dialogue for irony or characterization.

Use approximately:

- 1–2 quotations across the whole short film
- only at moments where the quotation naturally fits
- followed by a modern reaction

The quotation should reveal character.

Example pattern:

```text
Bai quotes classical poetry elegantly.
Lin is genuinely impressed by his literary knowledge.
She then points out that he is using poetry to justify his own romantic projection.
```

Do not turn the film into poetry recitation.

---

# 23. Legal or Social Boundary Humor Should Remain Character-Based

If the story involves privacy, unwanted photography, address questions, stalking-like behavior or sexual-harassment boundaries:

- show the behavior
- show the recipient's discomfort
- allow dialogue to identify the boundary
- do not automatically narrate a definitive legal conviction

Humorous legal references may be used in dialogue when appropriate.

The scene should distinguish:

```text
good intentions
```

from:

```text
permission
privacy
personal boundaries
```

---

# 24. Expressions Must Carry Story Information

Avoid generic descriptions such as:

```text
she looks uncomfortable
he looks romantic
```

Prefer observable performance:

```text
her smile remains but becomes smaller
one eyebrow rises
she waits half a second before answering
she shifts her bag toward the front of her body
she takes one small step sideways
she stops maintaining intimate eye contact
```

For self-confidence:

```text
he adjusts his blazer
his smile returns too quickly
his face visibly brightens after hearing only the flattering part
he pauses briefly, then rationalizes the situation
```

These are easier to generate and more cinematic.

---

# 25. Do Not Overload a 15-Second Clip

A 15-second clip should normally contain:

- one dramatic idea
- one location
- one time period
- one interpersonal development

Avoid placing several major events in one clip.

Good:

```text
Bai asks for address → Lin establishes boundary
```

Bad:

```text
leave gallery → walk three blocks → restaurant → address argument
→ taxi → phone exchange
```

If necessary, create another clip.

---

# 26. Suggested 15-Second Shot Rhythm

For fast dialogue scenes:

```text
[Shot 1] 0.0–1.5 s
brief establishing shot

[Shot 2] 1.5–5.0 s
speaker A close-up

[Shot 3] 5.0–8.0 s
speaker B close-up

[Shot 4] 8.0–11.0 s
speaker A close-up

[Shot 5] 11.0–14.0 s
speaker B close-up

[Shot 6] 14.0–15.0 s
reaction / visual punctuation
```

This is only a guideline. Adjust to dialogue length.

---

# 27. Dialogue Timing Rule

Estimate whether the Mandarin dialogue can realistically fit within the assigned seconds.

Do not assign a long sentence to 1.5 seconds.

Do not assign six Chinese characters to 6 seconds unless intentionally slow.

Natural conversation should generally sound slightly energetic rather than theatrical.

For comedy and romantic banter, use:

```text
short sentences
quick responses
minimal dead air
brief reaction beats
```

---

# 28. Avoid Redundant Cinematic Adjectives

Do not rely heavily on abstract wording such as:

```text
beautiful
cinematic
romantic
emotional
dreamlike
```

Prefer concrete instructions:

```text
50 mm close-up
warm streetlamp on one side of face
cool dark-blue fill on opposite side
small slow push-in
one-second pause
eyes shift away
```

Concrete visual information has higher priority.

---

# 29. Final Epilogue Can Reveal Unreliable Memory

When the story is told around projection, self-delusion or romantic reinterpretation, a useful final clip is:

```text
the next day
character alone
remembering the encounter
editing inconvenient facts out of memory
turning the experience into a flattering story
```

Internal thought should still be communicated by:

- self-dialogue
- facial expressions
- short hesitation
- writing action

rather than narration.

This is especially effective when the audience already saw what actually happened.

---

# 30. Required Pre-Writing Workflow

Before drafting the clips, perform these steps:

## Step 1 — Extract Character References

For every principal character record:

```text
Subject number
speaker ID
age
face
hair
body type
exact wardrobe
accessories
```

Verify against the supplied pictures.

---

## Step 2 — Build Location Bibles

For each recurring location define:

```text
dimensions
layout
walls
floor
ceiling
windows
doors
fixed furniture
fixed artwork or props
lighting
background extras
```

---

## Step 3 — Build Timeline

Example:

```text
17:45 street
18:05 gallery
18:10–19:20 gallery
19:35 gallery exit
19:45–19:55 night street
next day 09:10 office
```

Check that lighting progresses logically.

---

## Step 4 — Divide Story Into 15-Second Dramatic Units

Each unit should have one clear purpose.

Example:

```text
01 first sight
02 second encounter
03 genuine rapport
04 premature soulmate projection
05 personality projection
06 photography boundary
07 privacy boundary
08 follows her outside
09 address question
10 stronger boundary
11 central emotional confrontation
12 resolution
13 ironic epilogue
```

---

## Step 5 — Write Dialogue Before Shots

First create a logical conversation.

Then divide it into shots.

Do not create shots first and squeeze disconnected dialogue into them.

---

## Step 6 — Design Physical Camera Logic

For every shot ask:

```text
Where is the camera?
Where is the speaker?
What can realistically be seen?
Who must be off-screen?
What fixed scene anchor is visible?
```

If the geometry is difficult, split the shot.

---

## Step 7 — Anti-Error Pass

Before final output check:

### Character
- [ ] Subject numbering never changes.
- [ ] S1/S2 never change.
- [ ] Clothing matches reference pictures.
- [ ] Referenced people are linked to pictures without competing appearance or clothing descriptions.
- [ ] Reference studio lighting is not accidentally transferred.

### Scene
- [ ] Recurring locations use the same scene bible.
- [ ] Fixed objects remain in the same position.
- [ ] No accidental new rooms appear.
- [ ] At least two recurring visual anchors appear where practical.

### Time
- [ ] Time is explicitly stated.
- [ ] Visual lighting physically matches the time.
- [ ] Night scenes explicitly prohibit daylight where needed.
- [ ] Shot 1 establishes the correct lighting state.

### Dialogue
- [ ] 15 seconds contain enough dialogue.
- [ ] Dialogue has complete context and cause-and-effect.
- [ ] Important dialogue uses chest-up or closer framing.
- [ ] Speech duration can realistically fit the timeline.
- [ ] Psychology is understandable without narration.

### Duplication
- [ ] Exactly one instance of each principal character.
- [ ] Other principal character is off-screen during single-person dialogue close-ups.
- [ ] No background actor resembles a principal character.
- [ ] No reflections duplicate principal characters.

### Text
- [ ] No unnecessary readable text.
- [ ] Phone and laptop screens are hidden when possible.
- [ ] No accidental subtitles.
- [ ] No readable addresses, plates or signs.

### Shot feasibility
- [ ] Every shot is physically possible.
- [ ] No shot requires impossible visibility through several surfaces.
- [ ] Establishing geometry is separated from detailed facial reaction when necessary.

---

# 31. Recommended Input Format for Future Stories

The user can provide only:

```text
STORY:
[brief story outline]

CHARACTERS:
Picture 1 = female protagonist
Picture 2 = male protagonist

TIME:
example:
17:30–20:00 same evening
next morning 09:00

LOCATIONS:
example:
private gallery
nearby residential street
male protagonist's office

VIDEO:
13 clips
15 seconds each

SPECIAL REQUIREMENTS:
optional dialogue style, genre, legal/social theme,
classical quotation, comedy level, ending, etc.
```

Then automatically:

1. inspect the character images
2. construct global subject definitions
3. assign permanent S1/S2 IDs
4. create scene bibles
5. build a coherent timeline
6. divide the story into independent 15-second units
7. write dialogue with sufficient density
8. convert inner psychology into dialogue and acting
9. create lip-sync-friendly shot-reverse-shot sequences
10. enforce character and scene continuity
11. enforce time-of-day lighting
12. run the anti-duplication and feasibility checks
13. output each clip as a complete standalone official Ref2VA prompt

---

# Core Principle

For story-driven H3 Ref2VA generation:

**Do not merely describe what happens.  
Describe exactly what the camera can see, what the speaker can physically do, what remains constant across clips, and what information the model must not invent.**

The goal is not maximum prose detail.

The goal is:

**stable characters + stable locations + physically plausible shots + dense logical dialogue + explicit lighting + controlled visual information.**
