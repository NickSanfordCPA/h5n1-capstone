# Reader guide: unified post labels (Meta and X), version J1

*Journal rebuild, 2026-11-17. This guide merges the Meta guide v2 (`GUIDE_local_obs.md`,
`GUIDE_exclusion.md`), the X poultry-announcement guide and the X topic guide into one read per
post. The same guide is used for Facebook and X posts, in English and Spanish. It is part of
`PROTOCOL.md` and is frozen when the protocol is committed. It was piloted on 600 posts dated 2022–2024
only; the clarifications marked *(pilot)* answer questions the pilot readers raised.*

## What you are reading

Public social-media posts that mention avian influenza ("bird flu", H5N1, HPAI, "gripe aviar").
Each line of your batch file is one post, as JSON:

- `id`: copy it exactly, as text.
- `date`: the day the post was published.
- `lang`: `en` or `es`.
- `text`: the post text. Long posts are cut at 1,500 characters (`truncated: true`).

You do not see the author, the platform, any location the software guessed, or any outbreak
data. Decide from the text and the date only. Do not look anything up.

Answer **eight** fields for every post.

## 1. `relevant`: `yes` / `no`

**yes** if the post is about avian influenza: in birds, in other animals, or in people, in any
country. Include prices, policy, science and opinion that are about avian influenza.

**no** if avian influenza is absent or incidental: other diseases, "flu" in general, spam, word
lists, adverts, jokes where bird flu is only a punchline, posts that only name it in a list of
unrelated topics.

*(pilot)* **Roundups and digests.** A news roundup, headline list, "coming up" list, fundraiser or
status report is `yes` only when avian influenza is its lead or main item. When it is one item
among several unrelated ones, or a passing clause, it is `no`.

If `relevant` is `no`, set `topic` = `off_topic`, `content_class` = `other`, `local_place` = `no`,
`illness` = `no`, and leave `exclude`, `place_state` and `place_name` as described below
(`place_state` = `NONE`).

## 2. `topic`: one label

Pick what the post is **mainly** about. If two fit, use this precedence:
`local_observation` > `outbreak_report` > the rest.

| Label | Use when the post… |
|---|---|
| `local_observation` | reports something the author, or someone near them, saw first-hand: sick or dead birds or animals, their own flock or herd, a neighbour's farm, a local closure |
| `outbreak_report` | relays a detection, confirmation, case count, die-off or quarantine from officials or news (animal or human, any country) |
| `policy_response` | is about government or industry response: culling, indemnity, testing, vaccination policy, trade bans, criticism of agencies |
| `economic` | is about prices, supply, jobs or business losses |
| `human_health` | is about risk to people: pandemic potential, human symptoms, raw milk, worker safety, human vaccines |
| `science` | explains virology, mutation, transmission research or surveillance methods |
| `off_topic` | is not really about avian influenza |

## 3. `content_class`: one of four

This is about **what kind of report the post is**, whoever wrote it.

| Value | Meaning |
|---|---|
| `domestic_detection` | News, an agency notice, or someone relaying one, about a **confirmed or suspected detection in domestic animals**: a poultry flock (backyard or commercial chickens, turkeys, ducks, game birds, layers, a live-bird market), a dairy herd or other livestock. It stays `domestic_detection` even when it names the county or the farm, and even when a local resident passes it on ("county confirms bird flu at a farm near us"). |
| `wildlife_report` | News, an agency notice, or someone relaying one, about **sick or dead wild birds, or sick or dead wild, zoo or pet mammals** (including confirmed detections in them): geese dead at a lake, a zoo's big cats, a pet cat, seals on a beach. |
| `first_hand` | The author, or someone close to them (family, a neighbour, their own organisation), describes in their own words what they **saw or experienced themselves**: "our hens started dying last week", "found three dead geese by my pond", "we have closed our aviary", a rehab centre describing the birds it took in. |
| `other` | Everything else: national or statewide counts and maps, human cases, prices, policy, advice, science, opinion, jokes. |

Rules:

- **Precedence when a post mixes classes:** `first_hand` > `wildlife_report` >
  `domestic_detection` > `other`. A post that reports a poultry detection **and** a separate
  wild-bird or mammal die-off is `wildlife_report`.
- **An owner describing their own flock is `first_hand`,** even if the flock later tested
  positive ("my flock tested positive and we had to put them all down"). A report of someone
  else's flock is `domestic_detection`.
- **When unsure between `first_hand` and a relayed report,** ask whether the post reads as
  reporting a detection or die-off that officials or news announced (→ `domestic_detection` or
  `wildlife_report`) or as the author's own account of their own animals or what they
  personally found (→ `first_hand`).
- **Human cases are `other`,** including poultry and dairy workers who tested positive.
- Statewide or national summaries ("HPAI now in 12 Iowa counties") are `other` unless the post
  is mainly about one specific detection or die-off.
- *(pilot)* **A human case that mentions the flock or herd the person was exposed to is still
  `other`.** Use `domestic_detection` only when the post reports the animal detection as news in
  its own right.
- *(pilot)* **A detection with no animal stated** ("a case was confirmed in X County") is `other`.
  Do not guess whether it was wild or domestic.
- *(pilot)* **Products and environment.** Raw-milk or retail-milk findings, pet-food recalls and
  wastewater detections are `other`. A pet that got sick or died after eating a product is
  `wildlife_report`.
- *(pilot)* **An organisation speaking for itself** ("we", "our birds", "our centre") about its
  own animals or its own work is `first_hand`, with or without sick animals. A news story *about*
  that organisation is `wildlife_report` if it reports sick or dead animals there, and `other` if
  it reports only precautions. Agency surveillance programmes (sampling, testing, farm visits)
  are `other`.

## 4. `local_place`: `yes` / `no`

**yes** if the thing the post reports happened at a **named place at county level or smaller**:
a county, city, town, park, lake, river, beach, zoo, sanctuary, farm, refuge or neighbourhood.
A first-hand post counts as `yes` when it is plainly about the author's own place ("my
backyard", "our farm", "the pond by my house"), even if the place has no name.

**no** if the smallest place is a state, a region of a state ("eastern Arkansas"), a country, or
nothing.

*(pilot)* Answer for whatever the post reports, animal or not: a human case, a recall or a
wastewater finding in a named county or city is `yes`. Abroad, a province, department or region
is like a state (`no`); a named town, commune or site is `yes`.

## 5. `illness`: `yes` / `no`

**yes** only if the post describes **sick or dead animals, or a suspected or confirmed infection
among animals**, at the place it is about. Precautions alone are `no`: birds kept indoors, an
aviary or park closed, a fair cancelling its poultry show, a negative test with no sick animals
described. Human illness alone is `no`.

*(pilot)* `illness` is about animals and does not depend on `local_place`: a post saying a
state's flocks or herds are infected is `illness` = `yes`, `local_place` = `no`. A virus found in
milk, food or wastewater, with no sick animal described, is `no`.

## 6. `exclude`: blank, or one reason

Fill this in **only when `illness` is `yes`**. Otherwise leave it blank. Check the reasons in
this order and give the first that applies:

1. `outside_us`: the animals are outside the United States (Canada, Mexico, Peru, Chile, the
   UK, Antarctica, South Georgia island…). Hawaii, Alaska and US territories are in the US.
2. `retrospective`: the event happened more than about 30 days before the post's `date`:
   anniversary stories, research papers on past die-offs, season recaps, "last winter". An
   ongoing die-off first seen weeks ago and still happening is not retrospective. *(pilot)*
   Apply it only when the text states or plainly implies the earlier date; a post with no event
   date is treated as current.
3. `not_hpai`: the post says the animals tested negative, or names another cause (poisoning,
   botulism, West Nile, cold stun, vehicles).
4. `not_credible`: a joke, sarcasm, or a conspiracy claim presented as an observation.

## 7. `place_state`: where the post is about

Answer for **every** post.

- A two-letter US state code (`MN`, `CA`, `DC`…) when the post is mainly about one US state: the
  event, detection, policy or story it reports is located there. A city or county counts as its
  state.
- `US` when the post is about the United States with no single state: national news, several
  states, or a state named only in passing.
- `FOREIGN` when the post is about a place outside the US.
- `NONE` when the post is about no place at all.

Traps:

- "Georgia" can be the country; "South Georgia" is an Antarctic island; "Baja California" is
  Mexico; "La Florida" and "El Colorado" are in Chile; "Washington" in "Washington said…" is
  the federal government (`US`), not the state.
- A dateline or a publisher's home city is not what the post is about.
- A named county belongs to the state the text gives it: "Delaware County, New York" is `NY`.

## 8. `place_name`: short text, or blank

When `local_place` is `yes`, the named place in a few words, as written ("Lake Harriet,
Minneapolis", "Weld County", "our farm"). Otherwise blank.

## Worked examples

These are made-up posts in the style of the data.

| Post (shortened) | relevant | topic | content_class | local_place | illness | exclude | place_state |
|---|---|---|---|---|---|---|---|
| "A commercial turkey flock in Kandiyohi County has tested positive for HPAI, the Board of Animal Health said." | yes | outbreak_report | domestic_detection | yes | yes | | MN |
| "Dozens of dead geese at Silver Lake this week. City says avian flu is suspected." | yes | outbreak_report | wildlife_report | yes | yes | | the state the text supports, else `US` |
| "Lost 14 of our hens since Sunday. Vet thinks it's bird flu. Heartbroken." | yes | local_observation | first_hand | yes | yes | | `NONE` if no state can be told |
| "The zoo has moved its flamingos indoors as a precaution against avian flu." | yes | local_observation | first_hand if the zoo is the author, else other | yes | no | | as supported |
| "Egg prices hit $7 a dozen as bird flu spreads." | yes | economic | other | no | no | | US |
| "CDC confirms a human H5N1 case in a Louisiana patient." | yes | outbreak_report | other | no | no | | LA |
| "A year ago this week, 3,000 cranes died at the refuge." | yes | outbreak_report | wildlife_report | yes | yes | retrospective | as supported |
| "Mueren cientos de lobos marinos en la costa de Perú por gripe aviar." | yes | outbreak_report | wildlife_report | yes | yes | outside_us | FOREIGN |
| "Chicken soup is the best cure for the flu." | no | off_topic | other | no | no | | NONE |

Note on the first example: a poultry-flock detection is labelled exactly as it reads
(`illness` = `yes`, `local_place` = `yes`). The analysis, not the reader, removes
`domestic_detection` posts from the sightings, because an announced poultry detection is the
outcome being predicted.

## Procedure

- Read your batch file yourself, in chunks of 40–60 lines with the Read tool, and decide each
  post by reading it. **Do not classify with a keyword or regex script.** A script may only
  write out answers you decided.
- Keep any scratch files in your own folder, the one named in your instructions. Other readers
  are working in parallel. Never use a shared scratch path.
- Write the output CSV named in your instructions with the header
  `id,relevant,topic,content_class,local_place,illness,exclude,place_state,place_name`,
  one row per input line, `id` copied exactly as text. Use Python's `csv` module, run with
  `uv run python` from `C:\Users\NickS\Desktop\Harvard\Capstone`.
- Then run the checker named in your instructions on your CSV and fix anything it reports.
- Reply only with: the number of rows, and the count of each `content_class` value.
