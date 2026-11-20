# Coder guide: checking the post labels

You are coding 400 public posts about bird flu (Facebook and X, English and Spanish) and their
authors. A language model has already labelled every post with a longer version of this guide
(`GUIDE_reader.md`). Your codes test those labels, so you will not see them.

**Rules for the exercise**

- Work alone. Do not discuss items with the other coders until all three files are in.
- Decide from what the sheet shows. Do not look anything up, and do not open the post online.
- Fill every yellow cell. If you are unsure, give your best answer and write why in `notes`.
- Expect about 3 to 4 hours. Stopping and resuming is fine.

## Sheet "Posts"

Each row shows the post's date, language and text (cut at 1,500 characters).

### 1. `relevant`

- **yes**: the post is about avian influenza, in birds, other animals or people, in any country.
  Prices, policy, science and opinion about it count.
- **no**: avian influenza is absent or incidental: another disease, "flu" in general, spam, an
  advert, a joke where bird flu is only the punchline, or a news roundup where it is one item
  among several unrelated ones.

### 2. `sick_dead_local`

Answer **yes** only if **all four** hold:

1. The post describes sick or dead animals, or a suspected or confirmed infection in animals.
   Precautions alone (birds kept indoors, a park closed, a show cancelled), a negative test, a
   virus found in milk or wastewater, and human illness do not count.
2. It happened at a **named place at county level or smaller in the United States**: a county,
   city, park, lake, beach, zoo, farm. "My backyard" or "our farm" counts in a first-hand post. A
   state, a region of a state or a country is too large. Alaska, Hawaii and US territories are in
   the United States.
3. It is **current**: within about 30 days of the post's date. Anniversary stories, research on
   past die-offs and "last winter" are not current. If the text gives no earlier date, treat it
   as current.
4. The post does not give another cause (poisoning, botulism, a negative test) and is not a joke
   or a conspiracy claim.

Otherwise answer **no**. A poultry-flock detection in a named county is **yes** here; question 3
is where it is separated from wildlife.

### 3. `content_class`

What kind of report is the post, whoever wrote it?

| Value | Use when the post is… |
|---|---|
| `domestic_detection` | news, an agency notice, or someone passing one on, about a confirmed or suspected detection in **domestic animals**: a poultry flock (backyard or commercial), a dairy herd, other livestock |
| `wildlife_report` | news, an agency notice, or someone passing one on, about **sick or dead wild birds, or wild, zoo or pet mammals** |
| `first_hand` | the author, or someone close to them (family, a neighbour, their own organisation), describing in their own words what they **saw or experienced**: "our hens started dying", "found three dead geese by my pond", a rehab centre describing birds it took in |
| `other` | everything else: national or statewide counts, human cases, prices, policy, advice, science, opinion, jokes |

- If a post mixes classes, use the first that fits in this order: `first_hand`, `wildlife_report`,
  `domestic_detection`, `other`.
- An owner describing their own flock is `first_hand`. A report of someone else's flock is
  `domestic_detection`.
- Human cases are `other`, including farm workers, even if the post mentions the flock.
- "A case was confirmed in X County" with no animal stated is `other`.
- A post you marked not relevant is `other`.

### 4. `state_correct`

`geocoded_state` is the US state our software read from the text. Answer **yes** if the post is
mainly about that state: the event, detection, policy or story it reports is located there (a
city or county counts as its state). Answer **no** if the post is about another state, several
states, the country as a whole, a foreign place, or no place.

Traps: "Georgia" can be the country; "Washington said…" is the federal government; a newspaper's
home city is not what the post is about.

## Sheet "Authors"

Each row is one account, with no posts. Fill `author_type`.

- **organisation**: the account speaks for a body: a news outlet or station, a government agency
  or office, a company or farm business, a university or lab, a charity, zoo or rescue, a
  newsletter or podcast brand, or a bot, tracker or alert feed.
- **individual**: one person speaking for themselves, whatever their job. "Reporter at WXYZ",
  "USDA veterinarian, views my own" and a politician's personal account are individuals.
- With an empty bio, decide from the name and handle. If nothing indicates an organisation, use
  `individual`.
- For Facebook rows the sheet shows whether Facebook lists the account as a Page or a personal
  profile. Use it together with the name.
