# Reader guide: author type (X accounts), version J1

*Journal rebuild, protocol section 6.3. Facebook authors get their type from Meta's own
Page/profile field. X has no such field, so readers decide from what the account says about
itself.*

## What you are reading

Each line of your batch file is one X account, as JSON:

- `id`: copy it exactly, as text.
- `name`: the display name.
- `handle`: the @ name.
- `bio`: the profile description, cut at 300 characters. It may be empty.

You see no posts and no outcome data. Do not look anything up.

## One field: `author_type`

| Value | Meaning |
|---|---|
| `organisation` | The account speaks for a body, not a person: a news outlet, TV or radio station, newspaper, magazine, wire service, blog or newsletter brand; a government agency, department, extension service or official office; a company, farm business, trade association or co-operative; a university, institute, lab or research group; a charity, advocacy group, zoo, sanctuary or rescue; a bot, tracker, alert feed or aggregator account. |
| `individual` | The account is one person speaking for themselves, whatever their job: a journalist, scientist, vet, farmer, doctor, politician, activist or member of the public. |

Rules:

- **A person who works for an organisation is `individual`.** "Reporter at WXYZ", "Professor of
  virology", "USDA veterinarian, views my own" are people.
- **An elected official's or candidate's personal account is `individual`.** An office's account
  ("Office of the Governor", "Senator X Press Office") is `organisation`.
- **A farm or business run under a business name is `organisation`** ("Smith Family Poultry",
  "Green Acres Hatchery"). A farmer posting under their own name is `individual`.
- **Automated feeds are `organisation`:** accounts that describe themselves as bots, trackers,
  alerts, map updates or news aggregators.
- **A named brand run by one person** (a newsletter, podcast or blog brand as the account name)
  is `organisation`; the same person's own-name account is `individual`.
- **When the bio is empty,** decide from the name and handle. A personal name is `individual`. A
  name that reads as a station, outlet, agency or company is `organisation`. If nothing
  indicates an organisation, use `individual`.

## Procedure

- Read your batch file yourself, in chunks of 80–120 lines with the Read tool, and decide each
  account by reading it. **Do not classify with a keyword or regex script.** A script may only
  write out answers you decided.
- Keep any scratch files in your own folder, the one named in your instructions.
- Write the output CSV named in your instructions with the header `id,author_type`, one row per
  input line, `id` copied exactly as text. Use Python's `csv` module, run with `uv run python`
  from `C:\Users\NickS\Desktop\Harvard\Capstone`.
- Check: the row count equals the line count, every `id` appears once, and every value is
  `organisation` or `individual`.
- Reply only with: the number of rows and the count of each value.
